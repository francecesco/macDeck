"""Lo stato del Mac che il display mostra nell'header.

Le sonde girano in un THREAD DI SFONDO e /state restituisce l'ultima
istantanea dalla memoria, sempre in ~1 ms.

Non e' un'ottimizzazione: e' una correzione. La prima versione calcolava lo
stato sul percorso della richiesta, con tre osascript da ~0.3-0.6 s ciascuno,
e una cache a TTL di 1.5 s piu' corta dei 2 s di polling del display: la
cache non serviva mai il dispositivo e ogni poll pagava 1-2 s. `http_request`
di ESPHome e' bloccante, quindi quel tempo diventava loop principale fermo, e
i blocchi accumulati facevano scattare il watchdog del display. Misurato:
`interval took a long time for an operation (8013 ms)`.

Nota sul dominio: su macOS recente MediaRemote e' chiuso, quindi non esiste
un "now playing" di sistema leggibile senza helper esterni. Si interrogano i
player noti. L'audio da browser non e' visibile: e' un limite dichiarato,
non un bug.
"""

from __future__ import annotations

import threading
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

# Come si riconosce un diniego VERO. Serve perche' "la sonda non e' riuscita a
# girare" e "il permesso e' negato" sono cose diverse: con il Mac sotto carico
# osascript va in timeout, e trattare quel timeout come diniego faceva
# comparire sul display un allarme rosso falso proprio nei momenti peggiori.
ACCESS_DENIED_MARKERS = (
    "-1743",            # errAEEventNotPermitted
    "not allowed",
    "not authorized",
    "assistive",
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


def value_at(data: dict, path: str):
    """Legge un percorso puntato dentro uno snapshot: "media.app".

    Stessa sintassi usata da `state:` sugli slot e da `when:` sulle pagine,
    perche' un solo modo di indicare un valore e' meglio di due.
    """
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


EMPTY_SNAPSHOT = {
    "volume": dict(_EMPTY_VOLUME),
    "media": dict(_EMPTY_MEDIA),
    "system": {"cpu": None, "ram": None, "battery": None, "charging": None},
    "accessibility_ok": None,
}


class StateProbe:
    """Sonde in sfondo, lettura istantanea.

    `refresh()` fa il lavoro costoso, `snapshot()` non blocca MAI: e' quella
    la proprieta' che tiene vivo il loop del display.
    """

    def __init__(
        self,
        ex: Executor,
        interval: float = 1.0,
        accessibility_interval: float = 30.0,
    ) -> None:
        self.ex = ex
        self.interval = interval
        self.accessibility_interval = accessibility_interval
        self._lock = threading.Lock()
        self._data: dict = dict(EMPTY_SNAPSHOT)
        self._acc: bool | None = None
        self._acc_at = 0.0
        self._last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- lettura

    def note_error(self, msg: str | None) -> None:
        self._last_error = msg

    def snapshot(self) -> dict:
        with self._lock:
            data = dict(self._data)
        return {**data, "last_error": self._last_error}

    # ------------------------------------------------------------ aggiornamento

    def refresh(self) -> dict:
        """Interroga il Mac. Costoso: non va chiamato da un handler HTTP."""
        fresh = {
            "volume": self._volume(),
            "media": self._media(),
            "system": self._system(),
            "accessibility_ok": self._accessibility(time.monotonic()),
        }
        with self._lock:
            self._data = fresh
        return fresh

    # ---------------------------------------------------------------- ciclo

    def start(self) -> None:
        """Prima lettura sincrona, poi aggiornamenti in sfondo."""
        if self._thread is not None:
            return
        self.refresh()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="macdeck-state", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - il thread non deve morire mai
                pass

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
        """Vero se i tasti si possono inviare.

        In caso di esito incerto NON si dichiara il diniego: si tiene
        l'ultimo valore noto, e in mancanza si sta ottimisti. Un avviso rosso
        falso e' peggio di un avviso mancante, perche' insegna a ignorarlo.
        """
        if self._acc is not None and now - self._acc_at < self.accessibility_interval:
            return self._acc
        self._acc_at = now
        esito = self.ex.osascript(ACCESSIBILITY_SCRIPT, timeout=10.0)
        if esito.ok:
            self._acc = True
        elif any(m in (esito.error or "").lower() for m in ACCESS_DENIED_MARKERS):
            self._acc = False
        elif self._acc is None:
            self._acc = True
        return self._acc
