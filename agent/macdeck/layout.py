"""Il layout e' la sorgente di verita' del deck.

Il pannello e' 320x480 e l'AXS15231B non supporta lo scambio degli assi, ma
LVGL ha una propria rotazione software che ruota anche le coordinate del
touch. Con `lvgl: rotation: 90` lo spazio utile diventa 480x320 landscape,
ed e' in quello spazio che questo modulo calcola tutto. Griglia di default
3x3, tile 154x80.

Due proprieta' che i test bloccano deliberatamente:

- la geometria delle tile si calcola qui, non nel firmware, perche' e' il Mac
  a decidere dove vanno;
- un file malformato non spegne il deck: lo store mantiene in memoria
  l'ultimo layout valido e riporta l'errore a parte.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .actions import known_types

DISPLAY_W = 480
DISPLAY_H = 320
# Nessuna fascia fissa in alto: le informazioni che ci stavano (volume, brano,
# CPU) sono decorazione su un aggeggio che serve a lanciare cose, e costavano
# 36 px permanenti. Gli avvisi che invece contano davvero — Mac irraggiungibile,
# permesso Accessibilita' mancante — li disegna LVGL come sovrapposizione, che
# occupa spazio solo quando c'e' qualcosa che non va.
HEADER_H = 0
NAVBAR_H = 28
GUTTER = 4
MAX_SLOTS = 12

DEFAULT_GRID: dict[str, int] = {"cols": 3, "rows": 3}

DEFAULT_THEME: dict[str, str] = {
    "background": "#12141A",
    "tile": "#1E222B",
    "text": "#E8EAF0",
    "accent": "#4A9EFF",
    "font": "SFNS",
    # Moltiplicatore della dimensione dell'icona. 1.0 = tutto lo spazio che
    # l'etichetta lascia libero. Su griglie con poche righe conviene alzarlo.
    "icon_scale": 1.0,
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

                # --- riga in basso: app quando non c'e' musica ---
                {"pos": [0, 2], "label": "Slack",
                 "icon": "app:/Applications/Slack.app",
                 "action": {"type": "app", "target": "Slack"}},
                {"pos": [1, 2], "label": "Spotify",
                 "icon": "app:/Applications/Spotify.app",
                 "action": {"type": "app", "target": "Spotify"}},
                {"pos": [2, 2], "label": "Screenshot", "icon": "mdi:camera",
                 "action": {"type": "keys", "keys": "cmd+shift+4"}},

                # --- ...e comandi multimediali quando c'e'. Stesse caselle:
                #     lo slot condizionale soddisfatto vince su quello sotto.
                {"pos": [0, 2], "label": "Indietro", "icon": "mdi:skip-previous",
                 "when": "media.app",
                 "action": {"type": "media", "op": "prev"}},
                {"pos": [1, 2], "label": "Play / Pausa", "icon": "mdi:play-pause",
                 "when": "media.app",
                 "action": {"type": "media", "op": "play_pause"}},
                {"pos": [2, 2], "label": "Avanti", "icon": "mdi:skip-next",
                 "when": "media.app",
                 "action": {"type": "media", "op": "next"}},
            ],
        },
    ],
}


class LayoutError(ValueError):
    pass


def slot_boxes(grid: dict, *, navbar: bool = True) -> dict[int, dict]:
    """Indice dello slot -> rettangolo in pixel sul display.

    Con una pagina sola la navbar non serve, e i suoi 28 px vanno alle tile:
    la griglia 3x3 passa da 154x80 a 154x89. Per questo il calcolo dipende da
    quante pagine sono visibili, e quindi non puo' stare in validate(): si fa
    al momento di servire la richiesta, quando la visibilita' e' nota.
    """
    cols, rows = int(grid["cols"]), int(grid["rows"])
    area_h = DISPLAY_H - HEADER_H - (NAVBAR_H if navbar else 0)
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


def content_version(layout: dict) -> int:
    """Versione derivata dal CONTENUTO, non da un contatore.

    Un contatore che riparte da capo a ogni avvio dell'agent fa credere al
    display che nulla sia cambiato quando invece il layout e' diverso: e'
    successo, e la pagina 1 e' rimasta ferma alla griglia vecchia. Un hash
    del contenuto cambia se e solo se cambia qualcosa, e sopravvive ai
    riavvii.
    """
    payload = json.dumps(layout, sort_keys=True, ensure_ascii=False)
    return int(hashlib.sha256(payload.encode()).hexdigest()[:8], 16)


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
        when = page.get("when")
        if when is not None and not isinstance(when, str):
            raise LayoutError(f"{where_page}: 'when' deve essere una stringa")
        page_grid = _check_grid(page.get("grid") or grid, f"{where_page}, griglia")

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
            slot_when = slot.get("when")
            if slot_when is not None and not isinstance(slot_when, str):
                raise LayoutError(
                    f"{where_page}, slot {list(pos)}: 'when' deve essere una stringa"
                )
            # Piu' slot possono condividere una posizione, purche' al massimo
            # uno sia incondizionato: e' cosi' che la riga in basso diventa i
            # comandi multimediali quando un player e' attivo, e torna alle
            # app quando non lo e'. Due slot incondizionati sulla stessa
            # casella sarebbero invece un errore di battitura.
            if slot_when is None:
                if index in occupati:
                    raise LayoutError(
                        f"{where_page}: slot {list(pos)} occupato da "
                        f"{occupati[index]!r} e nessuno dei due ha 'when'"
                    )
                occupati[index] = slot.get("label", "")
            label = slot.get("label", "")
            where_slot = f"{where_page}, slot {list(pos)}"
            slots.append(
                {
                    "pos": [col, row],
                    "index": index,
                    "when": slot_when,
                    "label": label,
                    "icon": slot.get("icon") or "text:?",
                    "color": slot.get("color"),
                    "state": slot.get("state"),
                    "timeout_ms": slot.get("timeout_ms"),
                    "action": _check_action(slot.get("action"), where_slot),
                }
            )
        pages.append({"name": name, "grid": page_grid, "when": when,
                      "slots": slots})

    return {"schema": 1, "grid": grid, "theme": theme, "pages": pages}


class LayoutStore:
    """Tiene il layout in memoria e non lo perde mai.

    `version` e' il contatore che il display confronta via /state: cambia a
    ogni salvataggio riuscito, quindi il display sa quando ricaricare.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._layout: dict = copy.deepcopy(validate(DEFAULT_LAYOUT))
        self._version = content_version(self._layout)
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
            self._version = content_version(self._layout)
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
        self._version = content_version(self._layout)
        self.error = None

    def save(self, raw: dict) -> None:
        validated = validate(raw)          # solleva prima di toccare il disco
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        )
        self._layout = validated
        self._version = content_version(validated)
        self.error = None
