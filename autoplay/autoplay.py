from __future__ import annotations

import ctypes
import math
import random
import os
import time
from dataclasses import dataclass
from typing import Callable

from action import ActionPlanner, LOCATION
from akagi.libriichi_helper import meta_to_recommend
from settings.settings import settings, MITMType

from .logger import logger
from .overlay import OverlayMarkerPayload, RecommendationOverlay


@dataclass(slots=True)
class WindowObject:
    hwnd: int
    name: str


@dataclass(slots=True)
class WindowGeometry:
    left: int
    top: int
    width: int
    height: int


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", InputUnion)]


class AutoPlay:
    def __init__(self):
        self.bot = None
        self._target_hwnd: int | None = None
        self._planner = ActionPlanner()
        self._overlay: RecommendationOverlay | None = None
        self._user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
        self._input = ctypes.windll.user32 if hasattr(ctypes, "windll") else None

    @property
    def target_window(self) -> WindowObject | None:
        if self._target_hwnd is None:
            return None
        for window in self.get_windows():
            if window.hwnd == self._target_hwnd:
                return window
        return None

    def set_bot(self, bot) -> None:
        self.bot = bot

    def set_autoplay(self):
        if self._user32 is None:
            logger.warning("Autoplay is only available on Windows.")
            return

        if settings.autoplay_overlay.enabled and self._overlay is None:
            self._overlay = RecommendationOverlay()

    def get_windows(self) -> list[WindowObject]:
        if self._user32 is None:
            return []

        windows: list[WindowObject] = []
        current_pid = os.getpid()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_windows_proc(hwnd, _lparam):
            if not self._user32.IsWindowVisible(hwnd):
                return True
            if self._user32.IsIconic(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, title, length + 1)
            name = title.value.strip()
            if not name:
                return True
            pid = ctypes.c_ulong()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == current_pid or self._is_ignored_window(name):
                return True
            geometry = self._get_window_geometry(hwnd)
            if geometry is None or geometry.width < 640 or geometry.height < 360:
                return True
            windows.append(WindowObject(hwnd=int(hwnd), name=name))
            return True

        self._user32.EnumWindows(enum_windows_proc, 0)
        return windows

    def select_window(self, hwnd: int) -> None:
        self._target_hwnd = hwnd

    def check_window(self) -> bool:
        if self._target_hwnd is None or self._user32 is None:
            return False
        return bool(self._user32.IsWindow(self._target_hwnd) and self._user32.IsWindowVisible(self._target_hwnd))

    def auto_select_window(self) -> WindowObject | None:
        if self.check_window():
            return self.target_window

        keywords = self._window_keywords()
        windows = self.get_windows()
        if not windows:
            return None

        for window in windows:
            lowered = window.name.lower()
            if any(keyword in lowered for keyword in keywords):
                self._target_hwnd = window.hwnd
                return window

        if len(windows) == 1:
            self._target_hwnd = windows[0].hwnd
            return windows[0]
        return None

    def observe_mjai_messages(self, mjai_msgs: list[dict]) -> None:
        for mjai_msg in mjai_msgs:
            self._planner.observe_event(mjai_msg)

    def update_overlay(self, mjai_msg: dict) -> None:
        if not settings.autoplay_overlay.enabled:
            if self._overlay is not None:
                self._overlay.hide()
            return

        if self.bot is None or self._overlay is None:
            return

        if "meta" not in mjai_msg or "q_values" not in mjai_msg["meta"]:
            self._overlay.hide()
            return

        if not self.check_window():
            if self.auto_select_window() is None:
                self._overlay.hide()
                return

        tehai, tsumohai = self._current_hand()
        markers = self._planner.build_overlay_markers(
            meta_to_recommend(mjai_msg["meta"], getattr(self.bot, "is_3p", False)),
            tehai,
            tsumohai,
        )
        if not markers:
            self._overlay.hide()
            return

        payload: list[OverlayMarkerPayload] = []
        geometry = self._get_window_geometry(self._target_hwnd)
        if geometry is None:
            self._overlay.hide()
            return

        for marker in markers:
            x, y = self._normalized_to_screen(geometry, marker.coord)
            tile_width = max(
                24,
                int((LOCATION["tiles"][1][0] - LOCATION["tiles"][0][0]) * min(geometry.width / 16.0, geometry.height / 9.0) * settings.autoplay_overlay.scale),
            )
            payload.append(
                OverlayMarkerPayload(
                    x=x,
                    y=int(y - 44 * settings.autoplay_overlay.scale),
                    width=tile_width,
                    probability=marker.probability,
                    primary=marker.primary,
                )
            )
        self._overlay.update(payload)

    def act(self, mjai_msg: dict) -> bool:
        if self.bot is None or self._user32 is None:
            return False
        if not self.check_window():
            return False

        tehai, tsumohai = self._current_hand()
        latest_operation_list = self._latest_operation_list()
        self._planner.update_operation_list(latest_operation_list)
        plan = self._planner.plan(mjai_msg, tehai, tsumohai, bot=self.bot)
        if not plan:
            logger.debug(
                f"No autoplay plan for action={mjai_msg.get('type')} "
                f"pai={mjai_msg.get('pai')} tsumogiri={mjai_msg.get('tsumogiri')} "
                f"tehai={tehai} tsumohai={tsumohai} operations={latest_operation_list}"
            )
            return False

        self._focus_target_window()
        last_success = False
        for click in plan:
            time.sleep(max(click.delay, 0.0))
            geometry = self._get_window_geometry(self._target_hwnd)
            if geometry is None:
                return False
            target = self._normalized_to_screen(geometry, click.coord)
            logger.debug(f"Autoplay click '{click.label}' at {target}.")
            self._move_mouse_with_curve(target)
            self._user32.SetCursorPos(target[0], target[1])
            time.sleep(0.015)
            last_success = self._click_with_retry(target, click.expected_types or None)
            if not last_success:
                logger.warning(f"Autoplay click did not complete for {click.label}.")
                return False
        return last_success

    def _current_hand(self) -> tuple[list[str], str | None]:
        tehai = list(getattr(self.bot, "tehai_mjai", []))
        tsumohai = getattr(self.bot, "last_self_tsumo", None)
        if len(tehai) in {14, 11, 8, 5, 2} and tsumohai in tehai:
            tehai.remove(tsumohai)
            return tehai, tsumohai
        return tehai, None

    def _window_keywords(self) -> tuple[str, ...]:
        match settings.mitm.type:
            case MITMType.MAJSOUL:
                return ("mahjong soul", "majsoul", "jantama", "雀魂")
            case MITMType.RIICHI_CITY:
                return ("riichi city", "麻将一番街")
            case MITMType.AMATSUKI:
                return ("amatsuki", "天月麻将")
            case _:
                return ("mahjong", "riichi", "雀魂")

    def _latest_operation_list(self) -> list[dict]:
        if settings.mitm.type != MITMType.MAJSOUL:
            return []

        try:
            from mitm.majsoul import get_latest_self_operation_list

            return get_latest_self_operation_list()
        except Exception as exc:
            logger.debug(f"Unable to read latest operation list: {exc}")
            return []

    def _get_window_geometry(self, hwnd: int | None) -> WindowGeometry | None:
        if hwnd is None or self._user32 is None:
            return None

        rect = RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None

        origin = POINT(0, 0)
        if not self._user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None

        return WindowGeometry(
            left=origin.x,
            top=origin.y,
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
        )

    def _normalized_to_screen(self, geometry: WindowGeometry, coord: tuple[float, float]) -> tuple[int, int]:
        scale = min(geometry.width / 16.0, geometry.height / 9.0)
        play_width = 16.0 * scale
        play_height = 9.0 * scale
        offset_x = geometry.left + (geometry.width - play_width) / 2.0
        offset_y = geometry.top + (geometry.height - play_height) / 2.0
        return (
            int(offset_x + coord[0] * scale),
            int(offset_y + coord[1] * scale),
        )

    def _move_mouse_with_curve(self, target: tuple[int, int]) -> None:
        start = POINT()
        self._user32.GetCursorPos(ctypes.byref(start))
        start_point = (start.x, start.y)
        distance = math.hypot(target[0] - start_point[0], target[1] - start_point[1])
        path = self._build_bezier_path(start_point, target)
        if not path:
            return
        duration = min(0.24, max(0.12, distance / 2200.0))
        step_delay = duration / len(path)
        for point in path:
            self._user32.SetCursorPos(int(point[0]), int(point[1]))
            time.sleep(step_delay)

    def _build_bezier_path(self, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[float, float]]:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        distance = math.hypot(dx, dy)
        if distance < 1:
            return [end]

        steps = max(int(settings.autoplay_input.bezier_steps), 10)
        steps = min(42, steps + max(0, int(distance / 18)))
        smoothing = max(0.0, min(settings.autoplay_input.bezier_smoothing, 1.0))
        if smoothing <= 0:
            return [
                (
                    start[0] + dx * index / steps,
                    start[1] + dy * index / steps,
                )
                for index in range(1, steps + 1)
            ]

        normal_x = -dy / distance
        normal_y = dx / distance
        bend = distance * 0.16 * smoothing
        control_1 = (
            start[0] + dx * 0.33 + normal_x * bend,
            start[1] + dy * 0.33 + normal_y * bend,
        )
        control_2 = (
            start[0] + dx * 0.66 - normal_x * bend * 0.75,
            start[1] + dy * 0.66 - normal_y * bend * 0.75,
        )

        path: list[tuple[float, float]] = []
        for index in range(1, steps + 1):
            t = index / steps
            omt = 1.0 - t
            x = (
                omt ** 3 * start[0]
                + 3 * omt ** 2 * t * control_1[0]
                + 3 * omt * t ** 2 * control_2[0]
                + t ** 3 * end[0]
            )
            y = (
                omt ** 3 * start[1]
                + 3 * omt ** 2 * t * control_1[1]
                + 3 * omt * t ** 2 * control_2[1]
                + t ** 3 * end[1]
            )
            path.append((x, y))
        return path

    def _left_click(self) -> None:
        if self._input is None:
            return

        try:
            extra = ctypes.c_ulong(0)
            down = INPUT()
            down.type = 0
            down.union.mi = MouseInput(
                dx=0,
                dy=0,
                mouseData=0,
                dwFlags=0x0002,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )
            up = INPUT()
            up.type = 0
            up.union.mi = MouseInput(
                dx=0,
                dy=0,
                mouseData=0,
                dwFlags=0x0004,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )
            event_arr = (INPUT * 2)(down, up)
            sent = self._input.SendInput(2, ctypes.byref(event_arr), ctypes.sizeof(INPUT))
            if sent != 2:
                raise RuntimeError(f"SendInput sent {sent}/2 events")
        except Exception as exc:
            logger.debug(f"SendInput failed, fallback to mouse_event: {exc}")
            self._user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.012)
            self._user32.mouse_event(0x0004, 0, 0, 0, 0)

    def _focus_target_window(self) -> None:
        if self._target_hwnd is None or self._user32 is None:
            return
        try:
            if self._user32.IsIconic(self._target_hwnd):
                self._user32.ShowWindow(self._target_hwnd, 9)
            self._user32.SetForegroundWindow(self._target_hwnd)
        except Exception as exc:
            logger.debug(f"Unable to focus target window: {exc}")

    def _click_with_retry(self, target: tuple[int, int], expected_types: tuple[int, ...] | None) -> bool:
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            logger.debug(f"Click attempt {attempt}/{max_attempts}, expected operation={expected_types}.")
            self._left_click()

            if not expected_types or settings.mitm.type != MITMType.MAJSOUL:
                return True

            for _ in range(6):
                time.sleep(0.1)
                if not self._operation_still_available(expected_types):
                    return True

            if attempt < max_attempts:
                jitter_x = random.randint(-2, 2)
                jitter_y = random.randint(-2, 2)
                self._user32.SetCursorPos(target[0] + jitter_x, target[1] + jitter_y)
                time.sleep(0.015)
                self._user32.SetCursorPos(target[0], target[1])
                time.sleep(0.015)
                logger.debug(f"Retrying click because operation type {expected_types} is still pending.")
        return False

    def _operation_still_available(self, expected_types: tuple[int, ...]) -> bool:
        operation_list = self._latest_operation_list()
        return any(op.get("type") in expected_types for op in operation_list)

    def _is_ignored_window(self, name: str) -> bool:
        lowered = name.lower()
        return "akagi" in lowered or lowered.endswith("akagi.exe")
