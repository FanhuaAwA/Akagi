from __future__ import annotations

import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .logger import logger


@dataclass(slots=True)
class OverlayMarkerPayload:
    x: int
    y: int
    width: int
    probability: float
    primary: bool


class RecommendationOverlay:
    TRANSPARENT_COLOR = "#FF00FF"

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="akagi-overlay", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def update(self, markers: list[OverlayMarkerPayload]) -> None:
        self._queue.put(("update", markers))

    def hide(self) -> None:
        self._queue.put(("hide", None))

    def close(self) -> None:
        self._queue.put(("close", None))

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:
            logger.warning(f"Overlay disabled because tkinter is unavailable: {exc}")
            self._ready.set()
            return

        self._tk = tk
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.attributes("-topmost", True)
        self._windows: list[tk.Toplevel] = []
        self._canvases = []
        self._images = []
        self._icon_cache = {
            True: self._load_icon(True),
            False: self._load_icon(False),
        }
        self._ready.set()
        self._root.after(16, self._pump)
        self._root.mainloop()

    def _pump(self) -> None:
        while True:
            try:
                command, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if command == "update":
                self._apply_markers(payload)
            elif command == "hide":
                self._hide_all()
            elif command == "close":
                self._destroy_all()
                self._root.quit()
                return
        self._root.after(16, self._pump)

    def _ensure_window(self, index: int):
        while len(self._windows) <= index:
            window = self._tk.Toplevel(self._root)
            window.withdraw()
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            try:
                window.attributes("-transparentcolor", self.TRANSPARENT_COLOR)
            except Exception:
                pass
            window.configure(bg=self.TRANSPARENT_COLOR)
            canvas = self._tk.Canvas(
                window,
                width=1,
                height=1,
                highlightthickness=0,
                bd=0,
                bg=self.TRANSPARENT_COLOR,
            )
            canvas.pack(fill="both", expand=True)
            self._windows.append(window)
            self._canvases.append(canvas)
            self._images.append(None)
        return self._windows[index], self._canvases[index]

    def _apply_markers(self, markers: list[OverlayMarkerPayload]) -> None:
        if not markers:
            self._hide_all()
            return

        for index, marker in enumerate(markers[:3]):
            window, canvas = self._ensure_window(index)
            pil_image = self._compose_marker_image(marker.primary, marker.probability)
            if marker.width > 0 and pil_image.width != marker.width:
                scaled_height = max(1, round(pil_image.height * marker.width / pil_image.width))
                pil_image = pil_image.resize((marker.width, scaled_height), Image.Resampling.LANCZOS)
            pil_image = self._prepare_colorkey_image(pil_image)
            self._images[index] = ImageTk.PhotoImage(pil_image)
            canvas.delete("all")
            width, height = pil_image.size
            canvas.configure(width=width, height=height)
            canvas.create_image(width // 2, height // 2, image=self._images[index])
            window.geometry(f"{width}x{height}+{marker.x - width // 2}+{marker.y - height}")
            window.deiconify()

        for index in range(len(markers), len(self._windows)):
            self._windows[index].withdraw()

    def _hide_all(self) -> None:
        for window in self._windows:
            window.withdraw()

    def _destroy_all(self) -> None:
        for window in self._windows:
            try:
                window.destroy()
            except Exception:
                pass
        self._windows.clear()
        self._canvases.clear()
        self._images.clear()

    def _compose_marker_image(self, primary: bool, probability: float) -> Image.Image:
        image = self._icon_cache[primary].copy()
        draw = ImageDraw.Draw(image)
        percentage = f"{probability * 100:.1f}%"
        font = self._load_font(percentage)
        bbox = draw.textbbox((0, 0), percentage, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        text_x = (image.width - text_width) / 2
        text_y = (image.height - text_height) / 2 + 8
        draw.text(
            (text_x, text_y),
            percentage,
            fill="#F8FBFF" if not primary else "#FFF9D6",
            font=font,
        )
        return image

    def _load_icon(self, primary: bool) -> Image.Image:
        icon_name = "maka_corner_label_1.png" if primary else "maka_corner_label_2.png"
        icon_path = self._resolve_icon_path(icon_name)
        if icon_path is not None:
            try:
                return self._sanitize_icon(Image.open(icon_path).convert("RGBA"))
            except Exception as exc:
                logger.warning(f"Failed to load overlay icon '{icon_path}': {exc}")
        return self._sanitize_icon(self._build_icon(primary))

    def _resolve_icon_path(self, icon_name: str) -> Path | None:
        candidates: list[Path] = []
        # Running from source.
        candidates.append(Path.cwd() / "img" / icon_name)
        # Running from frozen executable.
        candidates.append(Path(sys.executable).resolve().parent / "img" / icon_name)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "img" / icon_name)
        # Fallback relative to module path.
        candidates.append(Path(__file__).resolve().parents[1] / "img" / icon_name)

        for path in candidates:
            if path.exists():
                return path
        return None

    def _build_icon(self, primary: bool) -> Image.Image:
        width, height = 96, 64
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        base_fill = "#FFCD47" if primary else "#3D7FD9"
        base_shadow = "#C88A16" if primary else "#214C91"
        body_fill = "#F4BA26" if primary else "#2E6FCB"
        accent_fill = "#FFD971" if primary else "#609AE8"

        draw.rounded_rectangle((18, 18, 78, 54), radius=10, fill=base_shadow, outline=None)
        draw.rounded_rectangle((18, 16, 78, 52), radius=10, fill=body_fill, outline=None)
        draw.rounded_rectangle((22, 10, 44, 24), radius=6, fill=accent_fill, outline=None)

        ear_points_left = [(40, 18), (45, 8), (50, 18)]
        ear_points_right = [(50, 18), (55, 8), (60, 18)]
        draw.polygon(ear_points_left, fill=base_fill)
        draw.polygon(ear_points_right, fill=base_fill)
        draw.rounded_rectangle((38, 14, 62, 28), radius=8, fill=base_fill, outline=None)
        draw.ellipse((45, 18, 48, 21), fill="#FFF7E0")
        draw.ellipse((52, 18, 55, 21), fill="#FFF7E0")
        draw.line((50, 22, 50, 25), fill="#FFF7E0", width=2)
        return image

    def _prepare_colorkey_image(self, image: Image.Image) -> Image.Image:
        colorkey = (255, 0, 255, 255)
        source = image.convert("RGBA")
        padding = 4
        prepared = Image.new(
            "RGBA",
            (source.width + padding * 2, source.height + padding * 2),
            colorkey,
        )
        src_pixels = source.load()
        dst_pixels = prepared.load()

        for y in range(source.height):
            for x in range(source.width):
                r, g, b, a = src_pixels[x, y]
                if a <= 20:
                    dst_pixels[x + padding, y + padding] = colorkey
                    continue

                # Transparent-color overlays cannot preserve semi-transparent edges.
                # Snap visible pixels to opaque colors to avoid magenta/green fringing.
                dst_pixels[x + padding, y + padding] = (r, g, b, 255)
        return prepared

    def _sanitize_icon(self, image: Image.Image) -> Image.Image:
        source = image.convert("RGBA")
        alpha = source.getchannel("A")
        threshold = 16
        mask = alpha.point(lambda a: 255 if a >= threshold else 0)
        bbox = mask.getbbox()
        if bbox is not None:
            source = source.crop(bbox)
            alpha = source.getchannel("A")

        pixels = source.load()
        for y in range(source.height):
            for x in range(source.width):
                r, g, b, a = pixels[x, y]
                if a < threshold:
                    pixels[x, y] = (0, 0, 0, 0)
        return source

    def _load_font(self, text: str):
        size = 18
        if len(text) >= 6:
            size = 16
        if len(text) >= 7:
            size = 14
        try:
            return ImageFont.truetype("arial.ttf", size=size)
        except Exception:
            return ImageFont.load_default()
