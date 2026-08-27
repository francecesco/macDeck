"""Il layout e' la sorgente di verita' del deck.

Il display e' 320x480 VERTICALE: l'AXS15231B non supporta lo scambio degli
assi (`swap_xy` e' UNDEFINED nel preset di ESPHome), quindi il landscape non
e' ottenibile via transform. La griglia di default e' 3x4, che in verticale
da' tile quasi quadrate da 101x99.

Due proprieta' che i test bloccano deliberatamente:

- la geometria delle tile si calcola qui, non nel firmware, perche' e' il Mac
  a decidere dove vanno;
- un file malformato non spegne il deck: lo store mantiene in memoria
  l'ultimo layout valido e riporta l'errore a parte.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .actions import known_types

DISPLAY_W = 320
DISPLAY_H = 480
HEADER_H = 36
NAVBAR_H = 28
GUTTER = 4
MAX_SLOTS = 12

DEFAULT_GRID: dict[str, int] = {"cols": 3, "rows": 4}

DEFAULT_THEME: dict[str, str] = {
    "background": "#12141A",
    "tile": "#1E222B",
    "text": "#E8EAF0",
    "accent": "#4A9EFF",
    "font": "SFNS",
}

DEFAULT_LAYOUT: dict[str, Any] = {
    "schema": 1,
    "grid": dict(DEFAULT_GRID),
    "theme": dict(DEFAULT_THEME),
    "pages": [
        {
            "name": "Dev",
            "slots": [
                {"pos": [0, 0], "label": "VS Code",
                 "icon": "app:/Applications/Visual Studio Code.app",
                 "action": {"type": "app", "target": "Visual Studio Code"}},
                {"pos": [1, 0], "label": "DataGrip",
                 "icon": "app:/Applications/DataGrip.app",
                 "action": {"type": "app", "target": "DataGrip"}},
                {"pos": [2, 0], "label": "iTerm",
                 "icon": "app:/Applications/iTerm.app",
                 "action": {"type": "app", "target": "iTerm"}},
                {"pos": [0, 1], "label": "Sourcetree",
                 "icon": "app:/Applications/Sourcetree.app",
                 "action": {"type": "app", "target": "Sourcetree"}},
                {"pos": [1, 1], "label": "Chrome",
                 "icon": "app:/Applications/Google Chrome.app",
                 "action": {"type": "app", "target": "Google Chrome"}},
                {"pos": [2, 1], "label": "Postman",
                 "icon": "app:/Applications/Postman.app",
                 "action": {"type": "app", "target": "Postman"}},
                {"pos": [0, 2], "label": "Docker",
                 "icon": "app:/Applications/Docker.app",
                 "action": {"type": "app", "target": "Docker"}},
                {"pos": [1, 2], "label": "Slack",
                 "icon": "app:/Applications/Slack.app",
                 "action": {"type": "app", "target": "Slack"}},
                {"pos": [2, 2], "label": "Screenshot", "icon": "mdi:camera",
                 "action": {"type": "keys", "keys": "cmd+shift+4"}},
                {"pos": [0, 3], "label": "Mission\nControl",
                 "icon": "mdi:view-dashboard",
                 "action": {"type": "keys", "keys": "ctrl+up"}},
                {"pos": [1, 3], "label": "Spotlight", "icon": "mdi:magnify",
                 "action": {"type": "keys", "keys": "cmd+space"}},
                {"pos": [2, 3], "label": "Blocca", "icon": "mdi:lock",
                 "action": {"type": "keys", "keys": "ctrl+cmd+q"}},
            ],
        },
        {
            "name": "Media",
            "grid": {"cols": 3, "rows": 2},
            "slots": [
                {"pos": [0, 0], "label": "Indietro", "icon": "mdi:skip-previous",
                 "action": {"type": "media", "op": "prev"}},
                {"pos": [1, 0], "label": "Play / Pausa", "icon": "mdi:play-pause",
                 "action": {"type": "media", "op": "play_pause"}},
                {"pos": [2, 0], "label": "Avanti", "icon": "mdi:skip-next",
                 "action": {"type": "media", "op": "next"}},
                {"pos": [0, 1], "label": "Volume -", "icon": "mdi:volume-minus",
                 "action": {"type": "volume", "op": "down", "step": 8}},
                {"pos": [1, 1], "label": "Muto", "icon": "mdi:volume-off",
                 "action": {"type": "volume", "op": "mute_toggle"},
                 "state": "volume.muted"},
                {"pos": [2, 1], "label": "Volume +", "icon": "mdi:volume-plus",
                 "action": {"type": "volume", "op": "up", "step": 8}},
            ],
        },
        {
            "name": "Casa",
            "slots": [
                {"pos": [0, 0], "label": "Home\nAssistant",
                 "icon": "app:/Applications/Home Assistant.app",
                 "action": {"type": "app", "target": "Home Assistant"}},
                {"pos": [1, 0], "label": "Plancia", "icon": "mdi:tablet-dashboard",
                 "action": {"type": "url", "url": "http://test-plancia.local"}},
                {"pos": [2, 0], "label": "Spotify",
                 "icon": "app:/Applications/Spotify.app",
                 "action": {"type": "app", "target": "Spotify"}},
                {"pos": [0, 1], "label": "Telegram",
                 "icon": "app:/Applications/Telegram.app",
                 "action": {"type": "app", "target": "Telegram"}},
                {"pos": [1, 1], "label": "WhatsApp",
                 "icon": "app:/Applications/WhatsApp.app",
                 "action": {"type": "app", "target": "WhatsApp"}},
                {"pos": [2, 1], "label": "VLC",
                 "icon": "app:/Applications/VLC.app",
                 "action": {"type": "app", "target": "VLC"}},
            ],
        },
    ],
}


class LayoutError(ValueError):
    pass


def slot_boxes(grid: dict) -> dict[int, dict]:
    """Indice dello slot -> rettangolo in pixel sul display."""
    cols, rows = int(grid["cols"]), int(grid["rows"])
    area_h = DISPLAY_H - HEADER_H - NAVBAR_H
    w = (DISPLAY_W - (cols + 1) * GUTTER) // cols
    h = (area_h - (rows + 1) * GUTTER) // rows
    boxes = {}
    for row in range(rows):
        for col in range(cols):
            boxes[row * cols + col] = {
                "x": GUTTER + col * (w + GUTTER),
                "y": HEADER_H + GUTTER + row * (h + GUTTER),
                "w": w,
                "h": h,
            }
    return boxes


def slot_index(pos: list[int], grid: dict) -> int:
    col, row = int(pos[0]), int(pos[1])
    return row * int(grid["cols"]) + col


def normalize_grid(grid: dict, where: str = "griglia") -> dict:
    """Valida e normalizza una griglia. Pubblica: la usa anche l'anteprima."""
    return _check_grid(grid, where)


def _check_grid(grid: dict, where: str) -> dict:
    try:
        cols, rows = int(grid["cols"]), int(grid["rows"])
    except (KeyError, TypeError, ValueError) as e:
        raise LayoutError(f"{where}: griglia malformata ({e})") from e
    if cols < 1 or rows < 1:
        raise LayoutError(f"{where}: la griglia deve avere almeno 1x1")
    if cols * rows > MAX_SLOTS:
        raise LayoutError(
            f"{where}: griglia {cols}x{rows} = {cols * rows} slot, "
            f"il firmware ne gestisce {MAX_SLOTS}"
        )
    return {"cols": cols, "rows": rows}


def _check_action(spec: Any, where: str) -> dict:
    if not isinstance(spec, dict):
        raise LayoutError(f"{where}: l'azione deve essere una mappa")
    kind = spec.get("type")
    if kind not in known_types():
        raise LayoutError(f"{where}: tipo di azione ignoto: {kind!r}")
    if kind == "sequence":
        steps = spec.get("steps") or []
        if not isinstance(steps, list):
            raise LayoutError(f"{where}: 'steps' deve essere una lista")
        for i, step in enumerate(steps, start=1):
            _check_action(step, f"{where}, passo {i}")
    return spec


def validate(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise LayoutError("il layout deve essere una mappa")
    schema = raw.get("schema", 1)
    if schema != 1:
        raise LayoutError(f"schema non supportato: {schema!r} (atteso 1)")

    grid = _check_grid(raw.get("grid") or DEFAULT_GRID, "griglia globale")
    theme = {**DEFAULT_THEME, **(raw.get("theme") or {})}

    pages_raw = raw.get("pages") or []
    if not isinstance(pages_raw, list) or not pages_raw:
        raise LayoutError("serve almeno una pagina")

    pages = []
    for pi, page in enumerate(pages_raw):
        where_page = f"pagina {pi + 1}"
        if not isinstance(page, dict):
            raise LayoutError(f"{where_page}: deve essere una mappa")
        name = page.get("name")
        if not name or not isinstance(name, str):
            raise LayoutError(f"{where_page}: manca 'name'")
        page_grid = _check_grid(page.get("grid") or grid, f"{where_page}, griglia")
        boxes = slot_boxes(page_grid)

        slots = []
        occupati: dict[int, str] = {}
        for slot in page.get("slots") or []:
            if not isinstance(slot, dict):
                raise LayoutError(f"{where_page}: ogni slot deve essere una mappa")
            pos = slot.get("pos")
            if (
                not isinstance(pos, (list, tuple))
                or len(pos) != 2
                or not all(isinstance(v, int) for v in pos)
            ):
                raise LayoutError(f"{where_page}: 'pos' deve essere [colonna, riga]")
            col, row = pos
            if not (0 <= col < page_grid["cols"] and 0 <= row < page_grid["rows"]):
                raise LayoutError(
                    f"{where_page}: posizione {list(pos)} fuori dalla griglia "
                    f"{page_grid['cols']}x{page_grid['rows']}"
                )
            index = slot_index(list(pos), page_grid)
            if index in occupati:
                raise LayoutError(
                    f"{where_page}: slot {list(pos)} occupato da {occupati[index]!r}"
                )
            label = slot.get("label", "")
            occupati[index] = label
            where_slot = f"{where_page}, slot {list(pos)}"
            slots.append(
                {
                    "pos": [col, row],
                    "index": index,
                    "box": boxes[index],
                    "label": label,
                    "icon": slot.get("icon") or "text:?",
                    "color": slot.get("color"),
                    "state": slot.get("state"),
                    "timeout_ms": slot.get("timeout_ms"),
                    "action": _check_action(slot.get("action"), where_slot),
                }
            )
        pages.append({"name": name, "grid": page_grid, "slots": slots})

    return {"schema": 1, "grid": grid, "theme": theme, "pages": pages}


class LayoutStore:
    """Tiene il layout in memoria e non lo perde mai.

    `version` e' il contatore che il display confronta via /state: cambia a
    ogni salvataggio riuscito, quindi il display sa quando ricaricare.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._layout: dict = copy.deepcopy(validate(DEFAULT_LAYOUT))
        self._version = 1
        self.error: str | None = None

    @property
    def layout(self) -> dict:
        return self._layout

    @property
    def version(self) -> int:
        return self._version

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                yaml.safe_dump(DEFAULT_LAYOUT, sort_keys=False, allow_unicode=True)
            )
            self._layout = copy.deepcopy(validate(DEFAULT_LAYOUT))
            self._version += 1
            self.error = None
            return
        try:
            raw = yaml.safe_load(self.path.read_text())
        except yaml.YAMLError as e:
            self.error = f"YAML non parsabile in {self.path.name}: {e}"
            return
        try:
            self._layout = validate(raw or {})
        except LayoutError as e:
            self.error = f"layout non valido: {e}"
            return
        self._version += 1
        self.error = None

    def save(self, raw: dict) -> None:
        validated = validate(raw)          # solleva prima di toccare il disco
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        )
        self._layout = validated
        self._version += 1
        self.error = None
