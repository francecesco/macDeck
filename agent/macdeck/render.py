"""Composizione della tile.

Renderizzare qui invece che sul display e' la decisione che elimina il
problema dei font LVGL: accenti, gradi, glifi MDI e emoji si risolvono dove
c'e' Pillow, non dove serve un font subsettato a compile time.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import icons

FONT_ALIASES: dict[str, str] = {
    "SFNS": "/System/Library/Fonts/SFNS.ttf",
    "SF": "/System/Library/Fonts/SFNS.ttf",
    "Helvetica": "/System/Library/Fonts/HelveticaNeue.ttc",
    "Arial": "/System/Library/Fonts/Supplemental/Arial.ttf",
    "ArialBold": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
}

_FALLBACK_FONT = "/System/Library/Fonts/SFNS.ttf"

CORNER = 10
PAD = 5


def resolve_font(name: str, px: int) -> ImageFont.FreeTypeFont:
    for candidate in (FONT_ALIASES.get(name), name, _FALLBACK_FONT):
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int,
          max_lines: int = 2) -> list[str]:
    """Manda a capo sulle parole; se non basta, tronca con l'ellissi."""
    if not text:
        return []
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        current = ""
        for word in words:
            probe = f"{current} {word}".strip()
            if draw.textlength(probe, font=font) <= max_w or not current:
                current = probe
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def render_tile(slot: dict, theme: dict, *, root: Path | None = None) -> Image.Image:
    box = slot["box"]
    w, h = int(box["w"]), int(box["h"])
    bg = slot.get("color") or theme["tile"]

    im = Image.new("RGB", (w, h), theme["background"])
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=CORNER, fill=bg)

    label = slot.get("label") or ""
    font_px = max(9, min(14, h // 6))
    font = resolve_font(theme.get("font", "SFNS"), font_px)
    lines = _wrap(d, label, font, w - 2 * PAD)
    line_h = font_px + 2
    text_block = len(lines) * line_h

    icon_area = h - text_block - 2 * PAD
    icon_px = max(16, min(icon_area, w - 2 * PAD))
    icon = icons.resolve(slot.get("icon") or "", icon_px, root=root)
    im.paste(icon, ((w - icon_px) // 2, PAD), icon)

    y = h - PAD - text_block
    for line in lines:
        d.text((w / 2, y), line, font=font, fill=theme["text"], anchor="ma")
        y += line_h

    return im


def tile_png(slot: dict, theme: dict, *, root: Path | None = None) -> bytes:
    buf = io.BytesIO()
    render_tile(slot, theme, root=root).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _key(slot: dict, theme: dict) -> str:
    payload = json.dumps(
        {
            "box": slot["box"],
            "label": slot.get("label"),
            "icon": slot.get("icon"),
            "color": slot.get("color"),
            "theme": theme,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class TileCache:
    """Cache in memoria. Le tile cambiano solo quando cambia il layout."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    @property
    def size(self) -> int:
        return len(self._data)

    def png(self, slot: dict, theme: dict, *, root: Path | None = None) -> bytes:
        k = _key(slot, theme)
        cached = self._data.get(k)
        if cached is None:
            cached = tile_png(slot, theme, root=root)
            self._data[k] = cached
        return cached

    def clear(self) -> None:
        self._data.clear()
