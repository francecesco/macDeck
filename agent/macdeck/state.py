"""Lo stato del Mac che il display mostra.

Le sonde girano in un THREAD DI SFONDO e /state restituisce l'ultima
istantanea dalla memoria, sempre in ~1 ms.

Non e' un'ottimizzazione: e' una correzione. La prima versione calcolava lo
stato sul percorso della richiesta, con tre osascript da ~0.3-0.6 s ciascuno,
e una cache a TTL di 1.5 s piu' corta dei 2 s di polling del display: la
cache non serviva mai il dispositivo e ogni poll pagava 1-2 s. `http_request`
di ESPHome e' bloccante, quindi quel tempo diventava loop principale fermo, e
i blocchi accumulati facevano scattare il watchdog del display. Misurato:
`interval took a long time for an operation (8013 ms)`.

Le sonde stanno in sources.py, una funzione decorata ciascuna. Qui c'e' il
ciclo che le esegue con la loro cadenza, la politica di fallimento, e la
lettura del permesso Accessibilita', che ha una logica sua.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import replace

from . import sources as S
from .executor import Executor

# Compatibilita': altri moduli e i test importano questi nomi da qui.
MEDIA_PLAYERS = S.MEDIA_PLAYERS
VOLUME_SCRIPT = S.VOLUME_SCRIPT
MEDIA_SCRIPT = S.MEDIA_SCRIPT

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

# Dopo quanti fallimenti consecutivi una sonda smette di mostrare l'ultimo
# valore noto. Uno o due sono un Mac sotto carico; tre di fila (con cadenze
# da 1 a 60 s) sono un'app chiusa male o uno script rotto, e una tile che
# mostra per ore un brano finito e' peggio di una tile vuota.
MAX_FAILURES = 3


def value_at(data: dict, path: str):
    """Legge un percorso puntato dentro uno snapshot: "media.app".

    Stessa sintassi usata da `state:` sugli slot, da `when:` su pagine e
    slot, e dai segnaposto `{media.app}` nelle etichette: un solo modo di
    indicare un valore e' meglio di due.
    """
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)(\|int)?\}")


def placeholders(template: str) -> list[str]:
    """Le chiavi citate in un'etichetta, nell'ordine: serve alla GUI."""
    return [m.group(1) for m in _PLACEHOLDER.finditer(template or "")]


def fill(template: str, data: dict) -> str:
    """Sostituisce `{a.b}` e `{a.b|int}` con i valori dello snapshot.

    Un valore assente diventa stringa vuota, non un errore: il layout resta
    valido anche se una sonda tace. L'unico filtro e' `|int`, per non
    mostrare "38.4%" a chi vuole "38%". Niente formule: se serve logica, la
    fa la sonda.
    """
    if not template:
        return ""

    def sub(m: re.Match) -> str:
        v = value_at(data, m.group(1))
        if v is None:
            return ""
        if m.group(2):
            try:
                return str(int(float(v)))
            except (TypeError, ValueError):
                return ""
        return str(v)

    return _PLACEHOLDER.sub(sub, template)


def snapshot_vuoto(registry: dict | None = None) -> dict:
    return {**S.empty_snapshot(registry), "accessibility_ok": None}


EMPTY_SNAPSHOT = snapshot_vuoto()


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
        root=None,
        sources: dict[str, S.Source] | None = None,
        clock=time.monotonic,
    ) -> None:
        self.ex = ex
        self.interval = interval
        self.accessibility_interval = accessibility_interval
        self.root = root
        self.sources = S.REGISTRY if sources is None else sources
        self.clock = clock
        self._lock = threading.Lock()
        self._data: dict = snapshot_vuoto(self.sources)
        self._values: dict[str, dict] = {}      # ultimo valore per sonda
        self._ran_at: dict[str, float] = {}     # ultimo giro per sonda
        self._failures: dict[str, int] = {}
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

    def refresh(self, due_only: bool = False) -> dict:
        """Interroga il Mac. Costoso: non va chiamato da un handler HTTP.

        Con `due_only` gira solo chi ha la cadenza scaduta: e' cosi' che lo
        chiama il thread. Senza, gira tutto: e' cosi' che lo chiamano i test,
        `doctor` e il primo avvio.
        """
        now = self.clock()
        dovute = [
            s for s in self.sources.values()
            if not due_only or now - self._ran_at.get(s.name, -1e9) >= s.every
        ]
        running = (
            S.running_apps(self.ex) if any(s.app for s in dovute) else frozenset()
        )
        base = S.ProbeContext(running=running, now=now, root=self.root)
        for src in dovute:
            self._ran_at[src.name] = now
            self._values[src.name] = self._run_source(src, base)

        fresh = {
            **{name: dict(v) for name, v in self._values.items()},
            "accessibility_ok": self._accessibility(now),
        }
        for src in self.sources.values():
            fresh.setdefault(src.name, dict(src.empty))
        with self._lock:
            self._data = fresh
        return fresh

    def _run_source(self, src: S.Source, base: S.ProbeContext) -> dict:
        if src.app and not any(a in base.running for a in src.app):
            self._failures[src.name] = 0
            return dict(src.empty)
        ctx = replace(base, last=dict(self._values.get(src.name) or src.empty))
        try:
            out = src.fn(self.ex, ctx)
        except Exception:  # noqa: BLE001 - una sonda rotta non ferma le altre
            out = None
        if out is None:
            n = self._failures.get(src.name, 0) + 1
            self._failures[src.name] = n
            if n >= MAX_FAILURES:
                return dict(src.empty)
            return dict(self._values.get(src.name) or src.empty)
        self._failures[src.name] = 0
        return {**src.empty, **out}

    # ---------------------------------------------------------------- ciclo

    def start(self) -> None:
        """Prima lettura sincrona, poi aggiornamenti in sfondo."""
        if self._thread is not None:
            return
        try:
            self.refresh()
        except Exception:  # noqa: BLE001
            pass
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
                self.refresh(due_only=True)
            except Exception:  # noqa: BLE001 - il thread non deve morire mai
                pass

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
