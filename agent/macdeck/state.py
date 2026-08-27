"""Lo stato del Mac che il display mostra nell'header.

Interrogato ogni 2 s, quindi con cache a TTL: ogni osascript costa decine di
millisecondi e non c'e' ragione di pagarli due volte nello stesso secondo.

Nota sul dominio: su macOS recente MediaRemote e' chiuso, quindi non esiste
un "now playing" di sistema leggibile senza helper esterni. Si interrogano i
player noti. L'audio da browser non e' visibile: e' un limite dichiarato,
non un bug.
"""

from __future__ import annotations

import time

import psutil

from .executor import Executor

MEDIA_PLAYERS = ("Spotify", "Music")

VOLUME_SCRIPT = (
    "set s to (get volume settings)\n"
    "return (output volume of s as text) & linefeed & "
    "(output muted of s as text)"
)

ACCESSIBILITY_SCRIPT = (
    'tell application "System Events" to return name of first process'
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

_EMPTY_VOLUME = {"level": None, "muted": None}
_EMPTY_MEDIA = {"app": None, "playing": False, "title": None, "artist": None}


class StateProbe:
    def __init__(
        self,
        ex: Executor,
        ttl: float = 1.5,
        accessibility_ttl: float = 30.0,
    ) -> None:
        self.ex = ex
        self.ttl = ttl
        self.accessibility_ttl = accessibility_ttl
        self._cache: dict | None = None
        self._cache_at = 0.0
        self._acc: bool | None = None
        self._acc_at = 0.0
        self._last_error: str | None = None

    def note_error(self, msg: str | None) -> None:
        self._last_error = msg

    def snapshot(self) -> dict:
        now = time.monotonic()
        if self._cache is None or now - self._cache_at >= self.ttl:
            self._cache = {
                "volume": self._volume(),
                "media": self._media(),
                "system": self._system(),
                "accessibility_ok": self._accessibility(now),
            }
            self._cache_at = now
        return {**self._cache, "last_error": self._last_error}

    def _volume(self) -> dict:
        r = self.ex.osascript(VOLUME_SCRIPT)
        if not r.ok:
            return dict(_EMPTY_VOLUME)
        parts = r.out.strip().splitlines()
        if len(parts) < 2:
            return dict(_EMPTY_VOLUME)
        try:
            level = int(float(parts[0].strip()))
        except ValueError:
            return dict(_EMPTY_VOLUME)
        return {"level": level, "muted": parts[1].strip().lower() == "true"}

    def _media(self) -> dict:
        r = self.ex.osascript(MEDIA_SCRIPT)
        if not r.ok:
            return dict(_EMPTY_MEDIA)
        lines = r.out.strip().splitlines()
        if not lines or lines[0].strip() == "none":
            return dict(_EMPTY_MEDIA)
        padded = (lines + ["", "", "", ""])[:4]
        return {
            "app": padded[0].strip() or None,
            "playing": padded[1].strip().lower() == "true",
            "title": padded[2].strip() or None,
            "artist": padded[3].strip() or None,
        }

    def _system(self) -> dict:
        battery = psutil.sensors_battery()
        return {
            "cpu": round(psutil.cpu_percent(interval=None), 1),
            "ram": round(psutil.virtual_memory().percent, 1),
            "battery": round(battery.percent) if battery else None,
            "charging": bool(battery.power_plugged) if battery else None,
        }

    def _accessibility(self, now: float) -> bool:
        if self._acc is None or now - self._acc_at >= self.accessibility_ttl:
            self._acc = self.ex.osascript(ACCESSIBILITY_SCRIPT, timeout=3.0).ok
            self._acc_at = now
        return self._acc
