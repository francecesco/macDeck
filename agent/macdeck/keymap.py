"""Traduzione di combinazioni tipo "cmd+shift+4" in AppleScript.

Tabelle pure, nessun I/O. I key code sono quelli di Carbon, invariati da
vent'anni e l'unico modo di premere tasti non stampabili via System Events.
"""

from __future__ import annotations


class InvalidKeys(ValueError):
    pass


MODIFIERS: dict[str, str] = {
    "cmd": "command down",
    "command": "command down",
    "shift": "shift down",
    "opt": "option down",
    "option": "option down",
    "alt": "option down",
    "ctrl": "control down",
    "control": "control down",
}

# ordine canonico di emissione, cosi' l'output e' deterministico
_MOD_ORDER = ["command down", "control down", "option down", "shift down"]

KEY_CODES: dict[str, int] = {
    "return": 36, "enter": 36, "tab": 48, "space": 49,
    "delete": 51, "backspace": 51, "forwarddelete": 117,
    "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}

# Nome canonico in uscita per ciascun modificatore: il piu' corto fra quelli
# che MODIFIERS accetta. In ingresso ne vanno bene tanti, in uscita uno solo,
# altrimenti la stessa combinazione produce stringhe diverse e i test non la
# possono confrontare per uguaglianza.
_MOD_CANON: dict[str, str] = {
    "command down": "cmd",
    "control down": "ctrl",
    "option down": "opt",
    "shift down": "shift",
}

# Rovescio di KEY_CODES. Piu' nomi puntano allo stesso codice (return/enter
# -> 36): vince il primo elencato, che e' quello per esteso.
_CODE_NAMES: dict[int, str] = {}
for _nome, _codice in KEY_CODES.items():
    _CODE_NAMES.setdefault(_codice, _nome)


def _parse(combo: str) -> tuple[list[str], str]:
    if not combo or not combo.strip():
        raise InvalidKeys("combinazione vuota")
    if " " in combo.strip():
        raise InvalidKeys(f"usa '+' come separatore, non spazi: {combo!r}")
    parts = combo.split("+")
    if any(p == "" for p in parts[:-1]) or parts[-1] == "":
        raise InvalidKeys(f"combinazione malformata: {combo!r}")
    *mods, key = parts
    clauses: list[str] = []
    for m in mods:
        clause = MODIFIERS.get(m.lower())
        if clause is None:
            raise InvalidKeys(f"modificatore ignoto: {m!r}")
        if clause not in clauses:
            clauses.append(clause)
    clauses.sort(key=_MOD_ORDER.index)
    return clauses, key


def _key_expression(key: str) -> str:
    lowered = key.lower()
    if lowered in KEY_CODES:
        return f"key code {KEY_CODES[lowered]}"
    if len(key) != 1:
        raise InvalidKeys(f"tasto ignoto: {key!r}")
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'keystroke "{escaped}"'


def to_applescript(combo: str, target: str | None = None) -> str:
    clauses, key = _parse(combo)
    expr = _key_expression(key)
    if clauses:
        expr += " using {" + ", ".join(clauses) + "}"
    lines = []
    if target:
        lines.append(f'tell application "{target}" to activate')
        lines.append("delay 0.05")
    lines.append(f'tell application "System Events" to {expr}')
    return "\n".join(lines)


def from_event(key_code: int, modifiers: list[str] | None = None,
               chars: str = "") -> str:
    """Da un evento di tastiera macOS alla combinazione che il dritto legge.

    I key code di NSEvent sono quelli di Carbon, cioe' esattamente quelli di
    KEY_CODES: la tabella non va duplicata in Swift, basta rovesciarla qui.

    `chars` serve per i tasti stampabili, che in KEY_CODES non ci sono: il
    codice del tasto "4" dipende dalla disposizione della tastiera, il
    carattere no.
    """
    clauses: list[str] = []
    for m in modifiers or []:
        clause = MODIFIERS.get(m.lower())
        if clause is None:
            raise InvalidKeys(f"modificatore ignoto: {m!r}")
        if clause not in clauses:
            clauses.append(clause)
    clauses.sort(key=_MOD_ORDER.index)

    nome = _CODE_NAMES.get(key_code)
    if nome is None:
        c = (chars or "").strip()
        if len(c) != 1:
            raise InvalidKeys(
                f"tasto non riconosciuto: key code {key_code}")
        nome = c.lower()

    return "+".join([_MOD_CANON[c] for c in clauses] + [nome])
