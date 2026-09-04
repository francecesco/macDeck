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

from PIL import Image, ImageDraw, ImageFont, ImageColor

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
PAD = 4          # margine orizzontale
PAD_V = 2        # margine verticale: stretto apposta, l'altezza e' il vincolo
                 # che limita la dimensione dell'icona

INFO_MIN_PX = 12
INFO_VALUE_RATIO = 0.45


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
        lines[-1] = _ellipsize(draw, lines[-1], font, max_w, force=True)
    return lines


def _dim(color: str, background: str) -> str | tuple[int, int, int]:
    """Il colore del testo attenuato verso lo sfondo della tile.

    Ritorna la tupla (r, g, b) del colore sfumato. Se i colori non sono
    validi, ritorna il colore originale senza sfumare.
    """
    try:
        a = ImageColor.getrgb(color)
        b = ImageColor.getrgb(background)
    except ValueError:
        return color
    return tuple((x * 3 + y * 2) // 5 for x, y in zip(a, b))


def _ellipsize(draw, text: str, font, max_w: int, force: bool = False) -> str:
    """Tronca il testo con ellissi se non rientra in max_w.

    Se force=True, sempre aggiunge "…" (anche se il testo entra, per la
    segnalazione di troncamento). Se force=False, ritorna il testo intatto
    se gia' rientra.
    """
    if not force and draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def render_info_tile(slot: dict, theme: dict, *, root: Path | None = None) -> Image.Image:
    """Valore grande, didascalia piccola, icona a sinistra se c'e'.

    Il valore sta su UNA riga: si parte da h*0.45 px e si scende fino a
    INFO_MIN_PX, poi si tronca con l'ellissi. Con il valore vuoto resta la
    didascalia: la tile non sparisce, cosi' la pagina non balla quando
    Spotify e' in pausa.
    """
    box = slot["box"]
    w, h = int(box["w"]), int(box["h"])
    bg = slot.get("color") or theme["tile"]
    im = Image.new("RGB", (w, h), theme["background"])
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=CORNER, fill=bg)

    x = PAD * 2
    if slot.get("icon"):
        icon_px = max(16, min(h // 2, 40))
        icon = icons.resolve(slot["icon"], icon_px, root=root)
        im.paste(icon, (x, (h - icon_px) // 2), icon)
        x += icon_px + PAD * 2
    avail_w = max(8, w - x - PAD * 2)

    font_name = theme.get("font", "SFNS")
    value = slot.get("label") or ""
    caption = slot.get("caption") or ""
    cap_px = max(9, min(13, h // 7))
    cap_font = resolve_font(font_name, cap_px)
    cap_h = cap_px + 2 if caption else 0

    if value:
        px = max(INFO_MIN_PX, int(h * INFO_VALUE_RATIO))
        font = resolve_font(font_name, px)
        while px > INFO_MIN_PX and d.textlength(value, font=font) > avail_w:
            px -= 1
            font = resolve_font(font_name, px)
        text = _ellipsize(d, value, font, avail_w)
        y = (h - (px + cap_h)) // 2
        d.text((x, y), text, font=font, fill=theme["text"], anchor="la")
        y += px + 2
    else:
        y = (h - cap_h) // 2

    if caption:
        d.text((x, y), _ellipsize(d, caption, cap_font, avail_w), font=cap_font,
               fill=_dim(theme["text"], bg), anchor="la")
    return im


def render_button_tile(slot: dict, theme: dict, *, root: Path | None = None) -> Image.Image:
    box = slot["box"]
    w, h = int(box["w"]), int(box["h"])
    bg = slot.get("color") or theme["tile"]

    im = Image.new("RGB", (w, h), theme["background"])
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=CORNER, fill=bg)

    label = slot.get("label") or ""
    font_px = max(9, min(14, h // 7))
    font = resolve_font(theme.get("font", "SFNS"), font_px)
    lines = _wrap(d, label, font, w - 2 * PAD)
    line_h = font_px + 1
    text_block = len(lines) * line_h

    # L'icona prende tutto lo spazio verticale che l'etichetta non usa.
    # `icon_scale` nel tema permette di spingerla oltre (o rimpicciolirla)
    # senza toccare il codice: su griglie basse conviene alzarlo.
    scale = float(theme.get("icon_scale", 1.0))
    icon_area = h - text_block - 2 * PAD_V
    icon_px = max(16, int(min(icon_area, w - 2 * PAD) * scale))
    icon_px = min(icon_px, h - text_block - PAD_V, w - PAD)
    icon = icons.resolve(slot.get("icon") or "", icon_px, root=root)
    im.paste(icon, ((w - icon_px) // 2, PAD_V), icon)

    y = h - PAD_V - text_block
    for line in lines:
        d.text((w / 2, y), line, font=font, fill=theme["text"], anchor="ma")
        y += line_h

    return im


def render_tile(slot: dict, theme: dict, *, root: Path | None = None) -> Image.Image:
    if slot.get("kind") == "info":
        return render_info_tile(slot, theme, root=root)
    return render_button_tile(slot, theme, root=root)


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
            "kind": slot.get("kind"),
            "caption": slot.get("caption"),
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


def render_screen(
    page: dict,
    theme: dict,
    *,
    root: Path | None = None,
) -> Image.Image:
    """Compone l'INTERA schermata: sfondo e tile.

    Il firmware scarica questa e basta. Dodici immagini separate erano la
    causa di due problemi distinti sul dispositivo — esaurimento dei socket
    ESP-IDF (10 disponibili, 12 richieste in parallelo) e la necessita' di
    re-invalidare ogni widget dopo il download. Una sola immagine li elimina
    entrambi per costruzione, e da' al Mac il controllo di ogni pixel.

    L'area dell'header resta di solo sfondo: sopra ci va un pannello LVGL con
    i valori vivi, che cambiano ogni 2 s e non vale la pena rirenderizzare.
    """
    from . import layout as L

    im = Image.new("RGB", (L.DISPLAY_W, L.DISPLAY_H), theme["background"])
    for slot in page["slots"]:
        box = slot["box"]
        im.paste(render_tile(slot, theme, root=root), (box["x"], box["y"]))

    return im


def screen_png(page: dict, theme: dict, **kw) -> bytes:
    buf = io.BytesIO()
    render_screen(page, theme, **kw).save(buf, format="PNG", optimize=True)
    return buf.getvalue()
