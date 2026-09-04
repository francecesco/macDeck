"""Le sorgenti dello stato che il display mostra: una funzione decorata per
ciascuna.

Aggiungere una sorgente = una funzione con @source. Nessun altro punto del
codice va toccato: StateProbe le esegue tutte con la loro cadenza, la GUI
elenca le chiavi con known_keys(), lo snapshot vuoto si compone da solo.

Due regole che valgono per tutte:

- una sonda con `app=` gira SOLO se una di quelle app e' in esecuzione.
  `tell application "Mail"` lancerebbe Mail se fosse chiuso, e un deck che
  apre applicazioni per conto suo non e' accettabile;
- una sonda che fallisce restituisce None (o solleva): StateProbe tiene
  l'ultimo valore noto, e dopo tre fallimenti torna al valore vuoto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

from .executor import Executor

LSAPPINFO = "/usr/bin/lsappinfo"


@dataclass(frozen=True)
class ProbeContext:
    """Quello che una sonda puo' sapere senza interrogare nulla.

    `running` contiene nomi e bundle id delle app GUI in esecuzione, in
    minuscolo. `last` e' l'ultimo valore restituito da QUESTA sonda.
    """

    running: frozenset[str]
    now: float
    root: Path | None = None
    last: dict = field(default_factory=dict)


Probe = Callable[[Executor, ProbeContext], "dict | None"]


@dataclass(frozen=True)
class Source:
    name: str
    fn: Probe
    empty: dict
    every: float
    app: tuple[str, ...]


REGISTRY: dict[str, Source] = {}


def source(name: str, *, empty: dict, every: float = 1.0,
           app: tuple[str, ...] | list[str] = ()):
    def deco(fn: Probe) -> Probe:
        REGISTRY[name] = Source(
            name=name, fn=fn, empty=dict(empty), every=float(every),
            app=tuple(a.lower() for a in app),
        )
        return fn

    return deco


def known_keys(registry: dict[str, Source] | None = None) -> list[str]:
    reg = REGISTRY if registry is None else registry
    return sorted(f"{s.name}.{k}" for s in reg.values() for k in s.empty)


def empty_snapshot(registry: dict[str, Source] | None = None) -> dict:
    reg = REGISTRY if registry is None else registry
    return {s.name: dict(s.empty) for s in reg.values()}


# --------------------------------------------------------- app in esecuzione

_LIST_NAME = re.compile(r'^\s*\d+\)\s+"([^"]+)"')
_LIST_BUNDLE = re.compile(r'bundleID="([^"]+)"')


def running_apps(ex: Executor) -> frozenset[str]:
    """Nomi e bundle id delle app GUI in esecuzione, in minuscolo.

    `lsappinfo list` costa ~40 ms e non passa da AppleScript ne' dal
    permesso Accessibilita'. I bundle id ci sono perche' i nomi visibili
    sono localizzati ("Calendario") e cambiano con la lingua del Mac.
    """
    r = ex.run([LSAPPINFO, "list"], timeout=3.0)
    if not r.ok:
        return frozenset()
    found: set[str] = set()
    for line in r.out.splitlines():
        m = _LIST_NAME.match(line)
        if m:
            found.add(m.group(1).lower())
        m = _LIST_BUNDLE.search(line)
        if m:
            found.add(m.group(1).lower())
    return frozenset(found)


# ------------------------------------------------------------ sonde di base

MEDIA_PLAYERS = ("Spotify", "Music")

VOLUME_SCRIPT = (
    "set s to (get volume settings)\n"
    "return (output volume of s as text) & linefeed & "
    "(output muted of s as text)"
)


def _media_script() -> str:
    branches = []
    for i, player in enumerate(MEDIA_PLAYERS):
        keyword = "if" if i == 0 else "else if"
        branches.append(
            f'{keyword} running_apps contains "{player}" then\n'
            f'    tell application "{player}"\n'
            f'        set out to "{player}" & linefeed\n'
            "        if player state is playing then\n"
            '            set out to out & "true" & linefeed\n'
            "        else\n"
            '            set out to out & "false" & linefeed\n'
            "        end if\n"
            "        set out to out & (name of current track) & linefeed & "
            "(artist of current track)\n"
            "        return out\n"
            "    end tell"
        )
    return (
        'tell application "System Events" to '
        "set running_apps to name of every process\n"
        + "\n".join(branches)
        + '\nelse\n    return "none"\nend if'
    )


MEDIA_SCRIPT = _media_script()


@source("volume", empty={"level": None, "muted": None}, every=1.0)
def volume(ex: Executor, ctx: ProbeContext) -> dict | None:
    r = ex.osascript(VOLUME_SCRIPT)
    if not r.ok:
        return None
    parts = r.out.strip().splitlines()
    if len(parts) < 2:
        return None
    try:
        level = int(float(parts[0].strip()))
    except ValueError:
        return None
    return {"level": level, "muted": parts[1].strip().lower() == "true"}


@source("media", empty={"app": None, "playing": False, "title": None,
                        "artist": None}, every=2.0)
def media(ex: Executor, ctx: ProbeContext) -> dict | None:
    # Nessun `app=`: lo script controlla da se' quali player sono aperti e
    # non ne lancia nessuno.
    r = ex.osascript(MEDIA_SCRIPT)
    if not r.ok:
        return None
    lines = r.out.strip().splitlines()
    if not lines or lines[0].strip() == "none":
        return {}
    padded = (lines + ["", "", "", ""])[:4]
    return {
        "app": padded[0].strip() or None,
        "playing": padded[1].strip().lower() == "true",
        "title": padded[2].strip() or None,
        "artist": padded[3].strip() or None,
    }


@source("system", empty={"cpu": None, "ram": None, "battery": None,
                         "charging": None}, every=2.0)
def system(ex: Executor, ctx: ProbeContext) -> dict:
    battery = psutil.sensors_battery()
    return {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "ram": round(psutil.virtual_memory().percent, 1),
        "battery": round(battery.percent) if battery else None,
        "charging": bool(battery.power_plugged) if battery else None,
    }


# ------------------------------------------------------- app in primo piano

_INFO_PAIR = re.compile(r'^"([^"]+)"="(.*)"\s*$')


def parse_lsappinfo_info(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _INFO_PAIR.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


@source("front", empty={"app": None, "name": None, "bundle": None,
                        "changed": False}, every=1.0)
def front(ex: Executor, ctx: ProbeContext) -> dict | None:
    """L'app in primo piano, con tre nomi.

    Tre perche' non coincidono: iTerm ha eseguibile `iTerm2`, nome visibile
    `iTerm`, bundle `com.googlecode.iterm2`; Calendar si presenta come
    "Calendario" su un Mac in italiano. `app:` nel layout puo' usare uno
    qualunque dei tre.

    `lsappinfo` invece di System Events: ~10 ms contro ~300, e non passa dal
    permesso Accessibilita'.
    """
    r = ex.run([LSAPPINFO, "front"], timeout=2.0)
    asn = r.out.strip() if r.ok else ""
    if not asn:
        return None
    r = ex.run([LSAPPINFO, "info", "-only", "name,bundleid,executablepath", asn],
               timeout=2.0)
    if not r.ok:
        return None
    info = parse_lsappinfo_info(r.out)
    exe = info.get("CFBundleExecutablePath") or ""
    name = info.get("LSDisplayName") or None
    app = exe.rsplit("/", 1)[-1] or name
    bundle = info.get("CFBundleIdentifier") or None
    if not app and not bundle:
        return None
    return {
        "app": app,
        "name": name,
        "bundle": bundle,
        "changed": (bundle or app) != (ctx.last.get("bundle") or ctx.last.get("app")),
    }
