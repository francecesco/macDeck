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

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

from . import paths
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


# Nota sul dominio: su macOS recente MediaRemote e' chiuso, quindi non
# esiste un 'now playing' di sistema leggibile senza helper esterni.
# Si interrogano i player noti. L'audio da browser non e' visibile:
# e' un limite dichiarato, non un bug.
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


# ------------------------------------------------------------------ Mail

MAIL_SCRIPT = 'tell application "Mail" to return unread count of inbox'


@source("mail", empty={"unread": None}, every=5.0, app=("com.apple.mail",))
def mail(ex: Executor, ctx: ProbeContext) -> dict | None:
    r = ex.osascript(MAIL_SCRIPT)
    if not r.ok:
        return None
    try:
        return {"unread": int(r.out.strip())}
    except ValueError:
        return None


# ----------------------------------------------------------------- Slack

# Slack non ha un dizionario AppleScript per i non letti. Il numero rosso
# sull'icona del Dock e' pero' un attributo di Accessibilita' leggibile:
# "missing value" quando il badge non c'e'. Best effort dichiarato.
SLACK_BADGE_SCRIPT = (
    'tell application "System Events" to tell process "Dock" to '
    'return value of attribute "AXStatusLabel" of UI element "Slack" of list 1'
)


@source("slack", empty={"badge": None}, every=5.0,
        app=("com.tinyspeck.slackmacgap",))
def slack(ex: Executor, ctx: ProbeContext) -> dict | None:
    r = ex.osascript(SLACK_BADGE_SCRIPT)
    if not r.ok:
        return None
    badge = r.out.strip()
    if not badge or badge == "missing value":
        return {"badge": None}
    return {"badge": badge}


# -------------------------------------------------------------- Calendar

# Gira solo con Calendar aperto (app=), e la pagina Calendar si vede solo
# con Calendar davanti: quindi in pratica sempre. Il confronto testuale
# "HH:MM<tab>titolo" trova il primo evento perche' HH:MM ordina bene come
# stringa. Gli eventi in corso e quelli di tutto il giorno (inizio 00:00)
# restano fuori: "prossimo" vuol dire che deve ancora cominciare.
CALENDAR_SCRIPT = """\
tell application "Calendar"
    set adesso to current date
    set fine to adesso - (time of adesso) + 1 * days
    set n to 0
    set primo to ""
    repeat with c in calendars
        set evs to (every event of c whose start date >= adesso and start date < fine)
        repeat with e in evs
            set n to n + 1
            set t to start date of e
            set hh to text -2 thru -1 of ("0" & (hours of t))
            set mm to text -2 thru -1 of ("0" & (minutes of t))
            set riga to hh & ":" & mm & tab & (summary of e)
            if primo is "" or riga < primo then set primo to riga
        end repeat
    end repeat
    return (n as text) & linefeed & primo
end tell"""


@source("calendar", empty={"next": None, "next_at": None, "count_today": None},
        every=60.0, app=("com.apple.ical",))
def calendar(ex: Executor, ctx: ProbeContext) -> dict | None:
    r = ex.osascript(CALENDAR_SCRIPT, timeout=10.0)
    if not r.ok:
        return None
    lines = r.out.split("\n")
    try:
        count = int(lines[0].strip())
    except (IndexError, ValueError):
        return None
    primo = lines[1] if len(lines) > 1 else ""
    if "\t" not in primo:
        return {"next": None, "next_at": None, "count_today": count}
    ora, titolo = primo.split("\t", 1)
    return {"next": titolo.strip() or None, "next_at": ora.strip() or None,
            "count_today": count}


# ----------------------------------------------------------- Claude Code

CLAUDE_STALE_S = 30 * 60      # oltre, la sessione non conta come viva
CLAUDE_PURGE_S = 24 * 3600    # oltre, il file si cancella
PGREP = "/usr/bin/pgrep"
GIT = "/usr/bin/git"


def newest_claude_file(dir_: Path) -> Path | None:
    files = [p for p in dir_.glob("*.json") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _purge_old(dir_: Path, now: float) -> None:
    for p in dir_.glob("*.json"):
        try:
            if now - p.stat().st_mtime > CLAUDE_PURGE_S:
                p.unlink()
        except OSError:
            pass


CLAUDE_EMPTY = {"alive": False, "model": None, "remaining": None,
                "dir": None, "branch": None, "session": None,
                "session_used": None, "week_used": None, "session_resets": None}


def _percent(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _local_hhmm(epoch) -> str | None:
    """Un istante Unix come ora locale 'HH:MM', per la didascalia del reset."""
    try:
        return time.strftime("%H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


@source("claude", empty=CLAUDE_EMPTY, every=5.0)
def claude(ex: Executor, ctx: ProbeContext) -> dict | None:
    """Modello, contesto rimanente, cartella e utilizzo del piano dell'ultima
    sessione di Claude Code con cui si e' parlato.

    I dati li scrive la statusLine dell'utente in un file per sessione (vedi
    README, "Il ponte con Claude Code"): l'agent non legge i transcript, che
    sono grandi e privati. Vince il file piu' recente: con piu' sessioni
    aperte e' quasi sempre quella davanti.
    """
    dir_ = paths.claude_dir(ctx.root)
    now = time.time()
    _purge_old(dir_, now)
    f = newest_claude_file(dir_)
    if f is None:
        return dict(CLAUDE_EMPTY)
    try:
        data = json.loads(f.read_text())
        eta = now - f.stat().st_mtime
    except (OSError, ValueError):
        return dict(CLAUDE_EMPTY)
    if not isinstance(data, dict):
        return dict(CLAUDE_EMPTY)

    processo = ex.run([PGREP, "-x", "claude"], timeout=2.0)
    alive = processo.ok and eta <= CLAUDE_STALE_S

    cwd = ((data.get("workspace") or {}).get("current_dir")
           or data.get("cwd") or None)
    branch = None
    if cwd:
        g = ex.run([GIT, "-C", cwd, "branch", "--show-current"], timeout=2.0)
        if g.ok and g.out.strip():
            branch = g.out.strip()
    home = str(Path.home())
    if cwd and (cwd == home or cwd.startswith(home + "/")):
        cwd = "~" + cwd[len(home):]

    remaining = _percent(
        (data.get("context_window") or {}).get("remaining_percentage"))

    # Il blocco rate_limits e' l'utilizzo del piano: la finestra di cinque
    # ore ("la sessione") e quella di sette giorni. Manca nelle versioni
    # vecchie di Claude Code: allora le chiavi restano vuote.
    limiti = data.get("rate_limits") or {}
    cinque = limiti.get("five_hour") or {}
    sette = limiti.get("seven_day") or {}

    return {
        "alive": bool(alive),
        "model": (data.get("model") or {}).get("display_name") or None,
        "remaining": remaining,
        "dir": cwd,
        "branch": branch,
        "session": data.get("session_id") or None,
        "session_used": _percent(cinque.get("used_percentage")),
        "week_used": _percent(sette.get("used_percentage")),
        "session_resets": _local_hhmm(cinque.get("resets_at"))
        if cinque.get("resets_at") is not None else None,
    }
