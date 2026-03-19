from __future__ import annotations

import random
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Iterable

from loguru import logger

from mitm.bridge.majsoul.bridge import MS_TILE_2_MJAI_TILE, compare_pai
from settings.settings import settings, MITMType

# Coordinates are normalized to a 16:9 play area.
LOCATION = {
    "tiles": [
        (2.23125, 8.3625),
        (3.021875, 8.3625),
        (3.8125, 8.3625),
        (4.603125, 8.3625),
        (5.39375, 8.3625),
        (6.184375, 8.3625),
        (6.975, 8.3625),
        (7.765625, 8.3625),
        (8.55625, 8.3625),
        (9.346875, 8.3625),
        (10.1375, 8.3625),
        (10.928125, 8.3625),
        (11.71875, 8.3625),
        (12.509375, 8.3625),
    ],
    "tsumo_space": 0.246875,
    "actions": [
        (10.875, 7.0),
        (8.6375, 7.0),
        (6.4, 7.0),
        (10.875, 5.9),
        (8.6375, 5.9),
        (6.4, 5.9),
    ],
    "candidates": [
        (3.6625, 6.3),
        (4.49625, 6.3),
        (5.33, 6.3),
        (6.16375, 6.3),
        (6.9975, 6.3),
        (7.83125, 6.3),
        (8.665, 6.3),
        (9.49875, 6.3),
        (10.3325, 6.3),
        (11.16625, 6.3),
        (12.0, 6.3),
    ],
    "candidates_kan": [
        (4.325, 6.3),
        (5.4915, 6.3),
        (6.6583, 6.3),
        (7.825, 6.3),
        (8.9917, 6.3),
        (10.1583, 6.3),
        (11.325, 6.3),
    ],
}

ACTION_PRIORITY = {
    0: 0,
    1: 99,
    2: 4,
    3: 3,
    4: 3,
    5: 2,
    6: 3,
    7: 2,
    8: 1,
    9: 1,
    10: 4,
    11: 2,
}

ACTION2TYPE = {
    "none": 0,
    "chi": 2,
    "pon": 3,
    "ankan": 4,
    "daiminkan": 5,
    "kakan": 6,
    "reach": 7,
    "hora": 9,
    "ryukyoku": 10,
    "nukidora": 11,
}

HAND_ACTIONS = {"dahai", "nukidora"}
BUTTON_ACTIONS = set(ACTION2TYPE)


@dataclass(slots=True)
class PlannedClick:
    coord: tuple[float, float]
    delay: float
    label: str
    expected_types: tuple[int, ...] = ()


@dataclass(slots=True)
class OverlayMarker:
    coord: tuple[float, float]
    probability: float
    primary: bool


class ActionPlanner:
    def __init__(self) -> None:
        self.is_new_round = True
        self.reached = False
        self.pending_reach_discard = False
        self.latest_operation_list: list[dict] = []

    def observe_event(self, mjai_msg: dict) -> None:
        event_type = mjai_msg.get("type")
        if event_type == "start_kyoku":
            self.is_new_round = True
            self.reached = False
            self.pending_reach_discard = False
        elif event_type in {"end_kyoku", "end_game"}:
            self.is_new_round = True
            self.reached = False
            self.pending_reach_discard = False

    def update_operation_list(self, operation_list: list[dict] | None) -> None:
        self.latest_operation_list = operation_list or []

    def discard_delay(self) -> float:
        if self.is_new_round:
            return settings.autoplay_time.first_tile
        low = min(settings.autoplay_time.rand_min, settings.autoplay_time.rand_max)
        high = max(settings.autoplay_time.rand_min, settings.autoplay_time.rand_max)
        return random.uniform(low, high)

    def action_delay(self) -> float:
        return max(settings.autoplay_time.candidate, 0.12) + 0.8

    def candidate_delay(self) -> float:
        return max(settings.autoplay_time.candidate, 0.08)

    def get_pai_coord(self, idx: int, tehais: list[str]) -> tuple[float, float]:
        tehai_count = sum(1 for tehai in tehais if tehai != "?")
        if idx == 13:
            base = LOCATION["tiles"][tehai_count]
            return (base[0] + LOCATION["tsumo_space"], base[1])
        return LOCATION["tiles"][idx]

    def plan(
        self,
        mjai_msg: dict | None,
        tehai: list[str],
        tsumohai: str | None,
        bot=None,
    ) -> list[PlannedClick]:
        if mjai_msg is None:
            return []

        action_type = mjai_msg.get("type")
        if action_type == "dahai" and (not self.reached or self.pending_reach_discard):
            coord = self._find_discard_coord(
                mjai_msg["pai"],
                list(tehai),
                tsumohai,
                prefer_tsumo=bool(mjai_msg.get("tsumogiri")),
            )
            if coord is None:
                logger.warning(f"Discard target not found for {mjai_msg['pai']}")
                return []
            label = "reach-discard" if self.pending_reach_discard else "discard"
            self.pending_reach_discard = False
            self.is_new_round = False
            return [
                PlannedClick(
                    coord=coord,
                    delay=self.discard_delay(),
                    label=label,
                    expected_types=(1,),
                )
            ]

        if action_type not in BUTTON_ACTIONS:
            return []

        operation_list = self._sorted_operation_list(bot, action_type, tehai, tsumohai)
        action_index = self._find_action_index(operation_list, self._operation_types_for_action(action_type))
        if action_index is None:
            logger.warning(f"Action button not found for {action_type}")
            return []

        click_label = "skip" if action_type == "none" else action_type

        plan = [
            PlannedClick(
                coord=LOCATION["actions"][action_index],
                delay=self.action_delay(),
                label=click_label,
                expected_types=self._expected_types_for_action(action_type),
            )
        ]

        if action_type == "reach":
            self.reached = True
            pai = mjai_msg.get("pai")
            if pai is None:
                self.pending_reach_discard = True
                return plan
            discard_coord = self._find_discard_coord(
                pai,
                list(tehai),
                tsumohai,
                prefer_tsumo=bool(mjai_msg.get("tsumogiri")),
            )
            if discard_coord is None:
                logger.warning(f"Riichi discard target not found for {pai}")
                self.pending_reach_discard = True
                return plan
            self.is_new_round = False
            self.pending_reach_discard = False
            plan.append(
                PlannedClick(
                    coord=discard_coord,
                    delay=self.candidate_delay(),
                    label="reach-discard",
                    expected_types=(1,),
                )
            )
            return plan

        if action_type in {"chi", "pon", "ankan", "kakan"}:
            candidate_click = self._plan_candidate_click(operation_list, mjai_msg)
            if candidate_click is not None:
                plan.append(candidate_click)
        return plan

    def build_overlay_markers(
        self,
        recommends: list[tuple[str, float]],
        tehai: list[str],
        tsumohai: str | None,
    ) -> list[OverlayMarker]:
        markers: list[OverlayMarker] = []
        hand = list(tehai)
        if tsumohai and tsumohai != "?":
            hand.append(tsumohai)

        used_indexes: set[int] = set()
        for recommend, probability in recommends:
            tile = "N" if recommend == "nukidora" else recommend
            if not any(self._tile_matches(tile, hand_tile) for hand_tile in hand):
                continue
            coord = self._find_tile_coord_for_overlay(tile, list(tehai), tsumohai, used_indexes)
            if coord is None:
                continue
            markers.append(
                OverlayMarker(
                    coord=coord,
                    probability=probability,
                    primary=len(markers) == 0,
                )
            )
            if len(markers) >= 3:
                break
        return markers

    def _find_tile_coord_for_overlay(
        self,
        tile: str,
        tehai: list[str],
        tsumohai: str | None,
        used_indexes: set[int],
    ) -> tuple[float, float] | None:
        hand = list(tehai)
        if tsumohai and tsumohai != "?":
            hand.append(tsumohai)

        for idx, hand_tile in enumerate(hand):
            if idx in used_indexes:
                continue
            if self._tile_matches(tile, hand_tile):
                used_indexes.add(idx)
                return self.get_pai_coord(13 if idx == len(hand) - 1 and tsumohai and tsumohai != "?" else idx, tehai)
        return None

    def _find_discard_coord(
        self,
        dahai: str,
        tehai: list[str],
        tsumohai: str | None,
        prefer_tsumo: bool = False,
    ) -> tuple[float, float] | None:
        if self.is_new_round:
            temp_tehai = [tile for tile in tehai if tile != "?"]
            if tsumohai and tsumohai != "?":
                temp_tehai.append(tsumohai)
            temp_tehai = sorted(temp_tehai, key=cmp_to_key(compare_pai))
            for idx, tile in enumerate(temp_tehai):
                if self._tile_matches(dahai, tile):
                    return self.get_pai_coord(idx, temp_tehai)

        if tsumohai and tsumohai != "?" and self._tile_matches(dahai, tsumohai):
            return self.get_pai_coord(13, tehai)

        for idx, tile in enumerate(tehai[:13]):
            if self._tile_matches(dahai, tile):
                return self.get_pai_coord(idx, tehai)

        if prefer_tsumo:
            return self.get_pai_coord(13, tehai)
        return None

    def _sorted_operation_list(
        self,
        bot,
        requested_type: str,
        tehai: list[str],
        tsumohai: str | None,
    ) -> list[dict]:
        operations = self.latest_operation_list or self._fallback_operation_list(bot, requested_type, tehai, tsumohai)
        operations = [operation.copy() for operation in operations]
        operations.append({"type": 0, "combination": []})

        can_ankan = any(operation["type"] == 4 for operation in operations)
        can_kakan = any(operation["type"] == 6 for operation in operations)
        if can_ankan and can_kakan:
            ankan_combinations = [
                combination
                for operation in operations
                if operation["type"] == 4
                for combination in operation.get("combination", [])
            ]
            merged_operations: list[dict] = []
            for operation in operations:
                if operation["type"] == 4:
                    continue
                if operation["type"] == 6:
                    updated = operation.copy()
                    updated["combination"] = list(updated.get("combination", [])) + ankan_combinations
                    merged_operations.append(updated)
                    continue
                merged_operations.append(operation)
            operations = merged_operations

        operations.sort(key=lambda item: ACTION_PRIORITY.get(item["type"], 99))
        return operations

    def _find_action_index(self, operations: list[dict], action_types: tuple[int, ...]) -> int | None:
        for idx, operation in enumerate(operations):
            if operation["type"] in action_types:
                return idx
        return None

    def _plan_candidate_click(self, operation_list: list[dict], mjai_msg: dict) -> PlannedClick | None:
        action_type = mjai_msg["type"]
        consumed = sorted(mjai_msg.get("consumed", []), key=cmp_to_key(compare_pai))
        target_type = ACTION2TYPE[action_type]

        for operation in operation_list:
            if operation["type"] != target_type and not (
                action_type in {"ankan", "kakan"} and operation["type"] == 6
            ):
                continue

            combinations = list(operation.get("combination", []))
            if len(combinations) <= 1:
                return None

            candidate_slots = LOCATION["candidates_kan"] if action_type in {"ankan", "kakan"} else LOCATION["candidates"]
            mid_point = 3 if action_type in {"ankan", "kakan"} else 5
            for idx, combination in enumerate(combinations):
                normalized = sorted(
                    self._normalize_combination(combination),
                    key=cmp_to_key(compare_pai),
                )
                if normalized != consumed:
                    continue
                candidate_index = int((-(len(combinations) / 2) + idx + 0.5) * 2 + mid_point)
                if 0 <= candidate_index < len(candidate_slots):
                    return PlannedClick(
                        coord=candidate_slots[candidate_index],
                        delay=self.candidate_delay(),
                        label=f"{action_type}-candidate",
                        expected_types=self._operation_types_for_action(action_type),
                    )
        return None

    def _fallback_operation_list(
        self,
        bot,
        requested_type: str,
        tehai: list[str],
        tsumohai: str | None,
    ) -> list[dict]:
        if bot is None:
            return [{"type": self._operation_types_for_action(requested_type)[0], "combination": []}]

        operations: list[dict] = []
        if getattr(bot, "can_chi", False) or getattr(bot, "can_chi_low", False) or getattr(bot, "can_chi_mid", False) or getattr(bot, "can_chi_high", False):
            operations.append({"type": 2, "combination": ["|".join(combo) for combo in bot.find_chi_consume_simple()]})
        if getattr(bot, "can_pon", False):
            operations.append({"type": 3, "combination": ["|".join(combo) for combo in bot.find_pon_consume_simple()]})
        if getattr(bot, "can_ankan", False):
            operations.append({"type": 4, "combination": self._build_ankan_combinations(tehai, tsumohai)})
        if getattr(bot, "can_daiminkan", False):
            last_tile = getattr(bot, "last_kawa_tile", "")
            if last_tile:
                operations.append({"type": 5, "combination": ["|".join([last_tile] * 3)]})
            else:
                operations.append({"type": 5, "combination": []})
        if getattr(bot, "can_kakan", False):
            operations.append({"type": 6, "combination": []})
        if getattr(bot, "can_riichi", False):
            operations.append({"type": 7, "combination": []})
        if getattr(bot, "can_agari", False):
            operations.append({"type": self._operation_types_for_action("hora")[0], "combination": []})
        if getattr(bot, "can_ryukyoku", False):
            operations.append({"type": 10, "combination": []})
        if requested_type == "nukidora":
            operations.append({"type": 11, "combination": []})
        return operations

    def _operation_types_for_action(self, action_type: str) -> tuple[int, ...]:
        if action_type == "hora":
            if settings.mitm.type == MITMType.MAJSOUL:
                return (8, 9)
            return (9,)
        return (ACTION2TYPE[action_type],)

    def _expected_types_for_action(self, action_type: str) -> tuple[int, ...]:
        if action_type == "none":
            pending_types = tuple(
                op["type"]
                for op in self.latest_operation_list
                if op.get("type", 0) != 0
            )
            return pending_types
        return self._operation_types_for_action(action_type)

    def _build_ankan_combinations(self, tehai: list[str], tsumohai: str | None) -> list[str]:
        full_hand = [tile for tile in tehai if tile != "?"]
        if tsumohai and tsumohai != "?":
            full_hand.append(tsumohai)

        grouped: dict[str, list[str]] = {}
        for tile in full_hand:
            grouped.setdefault(tile.replace("r", ""), []).append(tile)

        combinations: list[str] = []
        for base_tile, tiles in grouped.items():
            if len(tiles) < 4:
                continue
            ordered = list(sorted(tiles, key=cmp_to_key(compare_pai)))
            while len(ordered) < 4:
                ordered.append(base_tile)
            combinations.append("|".join(ordered[:4]))
        return combinations

    def _normalize_combination(self, combination: str | Iterable[str]) -> list[str]:
        if isinstance(combination, str):
            tiles = combination.split("|")
        else:
            tiles = list(combination)
        return [MS_TILE_2_MJAI_TILE.get(tile, tile) for tile in tiles]

    def _tile_matches(self, target: str, current: str) -> bool:
        if target == current:
            return True
        if target.endswith("r"):
            return target[:2] == current[:2]
        if current.endswith("r"):
            return target[:2] == current[:2]
        return target == current
