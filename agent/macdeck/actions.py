"""Registry dei tipi di azione.

Aggiungere un tipo = una funzione decorata con @action. Nessun altro punto
del codice va toccato: la validazione del layout interroga known_types() e
la web UI interroga lo stesso elenco.

Nessun handler solleva verso l'esterno: run() converte qualunque eccezione
in un Result fallito, perche' un errore di configurazione non deve poter
abbattere il server.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from .executor import Executor, Result
from .keymap import to_applescript

Handler = Callable[[dict, Executor], Result]

REGISTRY: dict[str, Handler] = {}
ASYNC_TYPES: set[str] = set()

_BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+){2,}$")

MEDIA_PLAYERS = ("Spotify", "Music")

_MEDIA_OPS = {
    "play_pause": "playpause",
    "next": "next track",
    "prev": "previous track",
}


def action(name: str, *, is_async: bool = False):
    def deco(fn: Handler) -> Handler:
        REGISTRY[name] = fn
        if is_async:
            ASYNC_TYPES.add(name)
        return fn

    return deco


def known_types() -> set[str]:
    return set(REGISTRY)


def is_async(spec: dict) -> bool:
    return spec.get("type") in ASYNC_TYPES


def run(spec: dict, ex: Executor) -> Result:
    kind = spec.get("type")
    handler = REGISTRY.get(kind)
    if handler is None:
        return Result(False, error=f"tipo di azione ignoto: {kind!r}")
    try:
        return handler(spec, ex)
    except Exception as e:  # noqa: BLE001 - confine deliberato
        return Result(False, error=f"{type(e).__name__}: {e}")


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ------------------------------------------------------------------ sincrone


@action("noop")
def _noop(spec: dict, ex: Executor) -> Result:
    return Result(True)


@action("page")
def _page(spec: dict, ex: Executor) -> Result:
    # Il firmware cambia pagina da se': qui non c'e' niente da eseguire.
    return Result(True)


@action("app")
def _app(spec: dict, ex: Executor) -> Result:
    target = spec.get("target")
    if not target:
        return Result(False, error="l'azione 'app' richiede 'target'")
    flag = "-b" if _BUNDLE_ID.match(target) and "/" not in target else "-a"
    return ex.run(["/usr/bin/open", flag, target])


@action("url")
def _url(spec: dict, ex: Executor) -> Result:
    url = spec.get("url")
    if not url:
        return Result(False, error="l'azione 'url' richiede 'url'")
    return ex.run(["/usr/bin/open", url])


@action("keys")
def _keys(spec: dict, ex: Executor) -> Result:
    combo = spec.get("keys")
    if not combo:
        return Result(False, error="l'azione 'keys' richiede 'keys'")
    return ex.osascript(to_applescript(combo, spec.get("to")))


@action("text")
def _text(spec: dict, ex: Executor) -> Result:
    text = spec.get("text")
    if text is None:
        return Result(False, error="l'azione 'text' richiede 'text'")
    return ex.osascript(
        f'tell application "System Events" to keystroke "{_escape(text)}"'
    )


@action("volume")
def _volume(spec: dict, ex: Executor) -> Result:
    op = spec.get("op", "set")
    if op == "set":
        value = spec.get("value")
        if not isinstance(value, int) or not 0 <= value <= 100:
            return Result(
                False, error="'volume' con op 'set' richiede 'value' fra 0 e 100"
            )
        return ex.osascript(f"set volume output volume {value}")
    if op == "mute_toggle":
        return ex.osascript(
            "set m to output muted of (get volume settings)\n"
            "set volume output muted (not m)"
        )
    if op not in ("up", "down"):
        return Result(False, error=f"op di volume ignota: {op!r}")
    step = int(spec.get("step", 6))
    sign = "+" if op == "up" else "-"
    return ex.osascript(
        "set v to output volume of (get volume settings)\n"
        f"set nv to v {sign} {step}\n"
        "if nv > 100 then set nv to 100\n"
        "if nv < 0 then set nv to 0\n"
        "set volume output volume nv"
    )


@action("media")
def _media(spec: dict, ex: Executor) -> Result:
    op = spec.get("op", "play_pause")
    command = _MEDIA_OPS.get(op)
    if command is None:
        return Result(False, error=f"op media ignota: {op!r}")
    branches = []
    for i, player in enumerate(MEDIA_PLAYERS):
        keyword = "if" if i == 0 else "else if"
        branches.append(
            f'{keyword} running_apps contains "{player}" then\n'
            f'    tell application "{player}" to {command}'
        )
    return ex.osascript(
        'tell application "System Events" to '
        "set running_apps to name of every process\n"
        + "\n".join(branches)
        + "\nelse\n"
        '    error "nessun player supportato in esecuzione"\n'
        "end if"
    )


# ----------------------------------------------------------------- asincrone


@action("shell", is_async=True)
def _shell(spec: dict, ex: Executor) -> Result:
    cmd = spec.get("cmd")
    if not cmd:
        return Result(False, error="l'azione 'shell' richiede 'cmd'")
    timeout = float(spec.get("timeout_ms", 5000)) / 1000
    return ex.shell(cmd, cwd=spec.get("cwd"), timeout=timeout)


@action("applescript", is_async=True)
def _applescript(spec: dict, ex: Executor) -> Result:
    script = spec.get("script")
    if not script:
        return Result(False, error="l'azione 'applescript' richiede 'script'")
    timeout = float(spec.get("timeout_ms", 5000)) / 1000
    return ex.osascript(script, timeout=timeout)


@action("shortcut", is_async=True)
def _shortcut(spec: dict, ex: Executor) -> Result:
    name = spec.get("name")
    if not name:
        return Result(False, error="l'azione 'shortcut' richiede 'name'")
    timeout = float(spec.get("timeout_ms", 30000)) / 1000
    return ex.run(["/usr/bin/shortcuts", "run", name], timeout=timeout)


@action("delay")
def _delay(spec: dict, ex: Executor) -> Result:
    seconds = min(float(spec.get("ms", 0)) / 1000, 10.0)
    time.sleep(seconds)
    return Result(True)


@action("sequence", is_async=True)
def _sequence(spec: dict, ex: Executor) -> Result:
    steps = spec.get("steps") or []
    for i, step in enumerate(steps, start=1):
        result = run(step, ex)
        if not result.ok:
            kind = step.get("type")
            return Result(False, error=f"passo {i} ({kind}): {result.error}")
    return Result(True)
