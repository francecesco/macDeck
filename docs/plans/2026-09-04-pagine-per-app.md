# Pagine per app — piano di implementazione

> **Per chi esegue:** SKILL RICHIESTA: superpowers:subagent-driven-development (consigliata) oppure superpowers:executing-plans, un task alla volta. I passi usano caselle `- [ ]` per il tracciamento.

**Obiettivo:** il deck segue l'app in primo piano sul Mac: quando davanti c'è un'app con una pagina dedicata in `layout.yaml`, salta a quella pagina e ne mostra comandi e dati vivi.

**Architettura:** l'agent acquisisce un registro di sonde (`sources.py`), una per sorgente di dati, eseguite nel thread di sfondo con la propria cadenza e solo se l'app è in esecuzione. Le pagine di `layout.yaml` acquistano `app:`; gli slot acquistano `kind: info`, `caption`, `span` e segnaposto `{chiave}` nelle etichette. `_resolve()` ordina il mazzo con le pagine dell'app davanti per prime e il server risponde `page: 0` a `/layout` quando l'app davanti è cambiata: il firmware, che già adotta quel campo, non si tocca.

**Stack:** Python 3.12, FastAPI, Pillow, pytest; `lsappinfo`, `osascript`, `pgrep`, `git` via `Executor`. Nessuna dipendenza nuova.

**Spec:** `docs/specs/2026-09-04-pagine-per-app-design.md`

## Vincoli globali

- I `layout.yaml` esistenti devono validare identici a prima (`schema: 1` invariato).
- Il firmware (`firmware/macdeck.yaml`) non si modifica e non si riflasha.
- `/state` non calcola nulla sul percorso della richiesta: le sonde girano solo nel thread di sfondo.
- Nessuna sonda deve poter **lanciare** un'app chiusa: chi parla a Mail, Calendar, Slack controlla prima che l'app sia in esecuzione.
- `executor.py` resta l'unico modulo che tocca `subprocess`; nei test si usa `FakeExecutor` di `tests/conftest.py`.
- Commit in italiano, senza prefissi tipo `feat:`, senza trailer né riferimenti all'assistente (regola dell'utente).
- Comandi di test: `cd agent && .venv/bin/python -m pytest` (tutti) oppure `.venv/bin/python -m pytest tests/test_x.py::nome -v`.
- Testi in italiano, apostrofi come nel resto del codice (`e'` nei commenti Python va bene, `è` nei testi utente).

---

## Struttura dei file

| file | responsabilità |
|---|---|
| **Crea** `agent/macdeck/sources.py` | registro `@source`, `ProbeContext`, elenco app in esecuzione, e le sonde: `volume`, `media`, `system` (migrate da `state.py`), `front`, `mail`, `slack`, `calendar`, `claude` |
| **Modifica** `agent/macdeck/state.py` | `StateProbe` esegue il registro con cadenza e politica di fallimento; tiene `accessibility_ok` (logica speciale); `value_at`, `fill`, `placeholders` |
| **Modifica** `agent/macdeck/layout.py` | validazione di `app:`, `kind`, `caption`, `span`, azione facoltativa; `normalize_app`, `app_matches`, `span_box`; cinque pagine nel `DEFAULT_LAYOUT` |
| **Modifica** `agent/macdeck/render.py` | `render_info_tile`, dispatch su `kind`, chiave di cache |
| **Modifica** `agent/macdeck/app.py` | `_resolve` con ordine del mazzo, segnaposto, span; salto a pagina 0; `/api/tile-preview` e `/api/config` estesi; `bundle` in `/api/icons` |
| **Modifica** `agent/macdeck/paths.py` | `claude_dir()` |
| **Modifica** `agent/macdeck/cli.py` | `doctor`: ponte Claude Code, app delle pagine non installate |
| **Modifica** `agent/macdeck/web/index.html` | campi App/when di pagina; Pulsante/Informativa; caption, span, inserisci valore; icona sulle schede; `toRaw` completo |
| **Crea** `agent/tests/test_sources.py` | registro, elenco app, ogni sonda su output finti |
| **Modifica** `agent/tests/test_state.py`, `test_layout.py`, `test_render.py`, `test_app.py`, `test_cli.py` | nuovi comportamenti |
| **Modifica** `README.md`, `NOTE-TECNICHE.md` | documentazione |

---

### Task 1: registro delle sonde e `StateProbe` a cadenza

**Files:**
- Create: `agent/macdeck/sources.py`
- Modify: `agent/macdeck/state.py`
- Test: `agent/tests/test_sources.py`, `agent/tests/test_state.py`

**Interfacce:**
- Produce: `sources.source(name, *, empty, every=1.0, app=())` decoratore; `sources.Source` (campi `name, fn, empty, every, app`); `sources.ProbeContext(running, now, root, last)`; `sources.REGISTRY: dict[str, Source]`; `sources.known_keys() -> list[str]`; `sources.empty_snapshot() -> dict`; `sources.running_apps(ex) -> frozenset[str]`; `StateProbe(ex, interval=1.0, accessibility_interval=30.0, root=None, sources=None, clock=time.monotonic)`; `StateProbe.refresh(due_only=False) -> dict`.
- Una sonda è `fn(ex: Executor, ctx: ProbeContext) -> dict | None`: `None` (o un'eccezione) significa "fallita"; il valore restituito viene fuso su `empty`.

- [ ] **Passo 1: test del registro e dell'elenco app**

Crea `agent/tests/test_sources.py`:

```python
from macdeck import sources
from macdeck.executor import Result as R

LSAPPINFO_LIST = '''\
 1) "loginwindow" ASN:0x0-0x3003:
    bundleID="com.apple.loginwindow"
    pid = 410 type="UIElement"
 2) "Mail" ASN:0x0-0x1a01a:
    bundleID="com.apple.mail"
    bundle path="/System/Applications/Mail.app"
 3) "Slack" ASN:0x0-0x6ba0d9a2:
    bundleID="com.tinyspeck.slackmacgap"
'''


def test_il_registro_conosce_le_sonde_di_base():
    assert {"volume", "media", "system"} <= set(sources.REGISTRY)


def test_known_keys_elenca_chiave_punto_campo():
    keys = sources.known_keys()
    assert "volume.level" in keys
    assert "media.title" in keys
    assert keys == sorted(keys)


def test_empty_snapshot_ha_una_voce_per_sonda_con_tutti_i_campi():
    snap = sources.empty_snapshot()
    assert snap["volume"] == {"level": None, "muted": None}
    assert snap["media"]["app"] is None


def test_running_apps_legge_nomi_e_bundle_id_in_minuscolo(fake_ex):
    fake_ex.replies = {"lsappinfo list": R(True, out=LSAPPINFO_LIST)}
    running = sources.running_apps(fake_ex)
    assert "mail" in running
    assert "com.apple.mail" in running
    assert "com.tinyspeck.slackmacgap" in running
    assert "slack" in running


def test_running_apps_su_errore_e_vuoto(fake_ex):
    fake_ex.replies = {"lsappinfo list": R(False, error="boom")}
    assert sources.running_apps(fake_ex) == frozenset()
```

- [ ] **Passo 2: esegui i test e verifica che falliscano**

Run: `cd agent && .venv/bin/python -m pytest tests/test_sources.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'macdeck.sources'`

- [ ] **Passo 3: scrivi `sources.py` con registro, elenco app e le tre sonde migrate**

Crea `agent/macdeck/sources.py`:

```python
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
```

- [ ] **Passo 4: esegui i test del registro**

Run: `cd agent && .venv/bin/python -m pytest tests/test_sources.py -v`
Atteso: PASS (5 test)

- [ ] **Passo 5: test di `StateProbe` sul registro (cadenza, gate, fallimenti)**

Aggiungi in fondo a `agent/tests/test_state.py`:

```python
# ------------------------------------------------- registro delle sonde

from macdeck import sources as S


def _registro(*fns):
    """Un registro isolato: i test non devono sporcare quello globale."""
    reg = {}
    for name, fn, kw in fns:
        reg[name] = S.Source(name=name, fn=fn, empty=dict(kw.get("empty", {"v": None})),
                             every=float(kw.get("every", 1.0)),
                             app=tuple(a.lower() for a in kw.get("app", ())))
    return reg


class Orologio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_una_sonda_con_app_non_gira_se_lapp_e_chiusa(fake_ex):
    chiamate = {"n": 0}

    def sonda(ex, ctx):
        chiamate["n"] += 1
        return {"v": 1}

    reg = _registro(("mail", sonda, {"app": ("com.apple.mail",)}))
    snap = StateProbe(fake_ex, sources=reg).refresh()
    assert chiamate["n"] == 0
    assert snap["mail"] == {"v": None}


def test_una_sonda_con_app_gira_se_lapp_e_aperta(fake_ex):
    fake_ex.replies = {"lsappinfo list": R(True, out=' 1) "Mail" ASN:0x0-0x1:\n    bundleID="com.apple.mail"\n')}

    def sonda(ex, ctx):
        assert "com.apple.mail" in ctx.running
        return {"v": 7}

    reg = _registro(("mail", sonda, {"app": ("com.apple.mail",)}))
    assert StateProbe(fake_ex, sources=reg).refresh()["mail"] == {"v": 7}


def test_la_cadenza_viene_rispettata_solo_con_due_only(fake_ex):
    chiamate = {"n": 0}

    def sonda(ex, ctx):
        chiamate["n"] += 1
        return {"v": chiamate["n"]}

    clock = Orologio()
    reg = _registro(("lenta", sonda, {"every": 5.0}))
    p = StateProbe(fake_ex, sources=reg, clock=clock)
    p.refresh(due_only=True)
    clock.t += 1.0
    p.refresh(due_only=True)
    assert chiamate["n"] == 1            # non e' ancora il suo turno
    clock.t += 4.5
    p.refresh(due_only=True)
    assert chiamate["n"] == 2
    p.refresh()                          # senza due_only gira sempre
    assert chiamate["n"] == 3


def test_un_fallimento_tiene_lultimo_valore_noto(fake_ex):
    esiti = iter([{"v": 3}, None, None])
    reg = _registro(("x", lambda ex, ctx: next(esiti), {}))
    p = StateProbe(fake_ex, sources=reg)
    assert p.refresh()["x"] == {"v": 3}
    assert p.refresh()["x"] == {"v": 3}
    assert p.refresh()["x"] == {"v": 3}


def test_tre_fallimenti_consecutivi_riportano_al_vuoto(fake_ex):
    esiti = iter([{"v": 3}, None, None, None])
    reg = _registro(("x", lambda ex, ctx: next(esiti), {}))
    p = StateProbe(fake_ex, sources=reg)
    for _ in range(4):
        snap = p.refresh()
    assert snap["x"] == {"v": None}


def test_uneccezione_nella_sonda_vale_come_fallimento(fake_ex):
    def esplode(ex, ctx):
        raise RuntimeError("rotta")

    reg = _registro(("x", esplode, {}))
    snap = StateProbe(fake_ex, sources=reg).refresh()
    assert snap["x"] == {"v": None}


def test_la_sonda_riceve_il_proprio_ultimo_valore(fake_ex):
    visti = []

    def sonda(ex, ctx):
        visti.append(dict(ctx.last))
        return {"v": len(visti)}

    reg = _registro(("x", sonda, {}))
    p = StateProbe(fake_ex, sources=reg)
    p.refresh()
    p.refresh()
    assert visti[0] == {"v": None}
    assert visti[1] == {"v": 1}


def test_il_valore_restituito_si_fonde_sul_vuoto(fake_ex):
    reg = _registro(("x", lambda ex, ctx: {"a": 1}, {"empty": {"a": None, "b": None}}))
    assert StateProbe(fake_ex, sources=reg).refresh()["x"] == {"a": 1, "b": None}
```

Poi sostituisci il test `test_una_sonda_che_esplode_non_uccide_il_thread` con:

```python
def test_una_sonda_che_esplode_non_uccide_il_thread(fake_ex):
    boom = {"n": 0}

    def esplode(ex, ctx):
        boom["n"] += 1
        raise RuntimeError("sonda rotta")

    # every=0: deve girare a OGNI giro del thread, altrimenti con la cadenza
    # di default (1 s) esploderebbe una volta sola in 0.15 s.
    reg = _registro(("rotta", esplode, {"every": 0.0}))
    p = StateProbe(fake_ex, interval=0.02, sources=reg)
    p.start()
    time.sleep(0.15)
    assert p._thread.is_alive()
    assert boom["n"] > 1
    p.stop()
```

(`_registro` è definito più in basso nel file: spostalo sopra, insieme a `Orologio` e all'import `from macdeck import sources as S`, subito dopo gli import in testa.)

- [ ] **Passo 6: esegui i test e verifica che falliscano**

Run: `cd agent && .venv/bin/python -m pytest tests/test_state.py -v`
Atteso: FAIL con `TypeError: ... unexpected keyword argument 'sources'`

- [ ] **Passo 7: riscrivi `StateProbe` sul registro**

Sostituisci il contenuto di `agent/macdeck/state.py` con:

```python
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


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)(\|int)?\}")


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
```

Attenzione a `_accessibility`: usa `now` dal `clock` iniettato, quindi con l'orologio finto dei test la cache dei 30 s si controlla a mano; i test esistenti sull'accessibilità usano `time.monotonic` vero e restano validi.

- [ ] **Passo 8: esegui tutti i test**

Run: `cd agent && .venv/bin/python -m pytest`
Atteso: PASS. Se `test_snapshot_prima_di_qualunque_refresh_e_valido_e_non_blocca` confronta con `EMPTY_SNAPSHOT`, il valore è lo stesso di prima (`volume`, `media`, `system`, `accessibility_ok`) perché il registro globale in questo task ha solo quelle tre sonde.

- [ ] **Passo 9: commit**

```bash
git add agent/macdeck/sources.py agent/macdeck/state.py agent/tests/test_sources.py agent/tests/test_state.py
git commit -m "Le sonde diventano un registro: una funzione decorata per sorgente, con cadenza propria"
```

---

### Task 2: sonda `front` — l'app in primo piano

**Files:**
- Modify: `agent/macdeck/sources.py`
- Test: `agent/tests/test_sources.py`

**Interfacce:**
- Produce: chiave `front` nello snapshot con `app` (nome dell'eseguibile, es. `iTerm2`), `name` (nome visibile, es. `iTerm`), `bundle` (es. `com.googlecode.iterm2`), `changed` (bool). Funzione pura `parse_lsappinfo_info(text) -> dict[str, str]`.

- [ ] **Passo 1: test**

Aggiungi a `agent/tests/test_sources.py`:

```python
LSAPPINFO_FRONT = 'ASN:0x0-0x6ba0d9a2:\n'
LSAPPINFO_INFO = '''\
"LSDisplayName"="iTerm"
"CFBundleIdentifier"="com.googlecode.iterm2"
"CFBundleExecutablePath"="/Applications/iTerm.app/Contents/MacOS/iTerm2"
'''


def _ctx(last=None):
    return sources.ProbeContext(running=frozenset(), now=0.0, last=last or {})


def test_parse_lsappinfo_info_legge_le_coppie_chiave_valore():
    d = sources.parse_lsappinfo_info(LSAPPINFO_INFO)
    assert d["LSDisplayName"] == "iTerm"
    assert d["CFBundleIdentifier"] == "com.googlecode.iterm2"


def test_front_espone_eseguibile_nome_visibile_e_bundle(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out=LSAPPINFO_INFO),
    }
    f = sources.front(fake_ex, _ctx())
    assert f == {"app": "iTerm2", "name": "iTerm",
                 "bundle": "com.googlecode.iterm2", "changed": True}


def test_front_passa_lasn_a_info(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out=LSAPPINFO_INFO),
    }
    sources.front(fake_ex, _ctx())
    info = [c for c in fake_ex.calls if c[1] == "info"][0]
    assert "ASN:0x0-0x6ba0d9a2:" in info


def test_front_changed_e_falso_se_lapp_e_la_stessa_di_prima(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out=LSAPPINFO_INFO),
    }
    prima = {"app": "iTerm2", "name": "iTerm",
             "bundle": "com.googlecode.iterm2", "changed": True}
    assert sources.front(fake_ex, _ctx(last=prima))["changed"] is False


def test_front_senza_bundle_usa_il_nome_delleseguibile(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out='"LSDisplayName"="Boh"\n"CFBundleExecutablePath"="/x/Boh.app/Contents/MacOS/Boh"\n'),
    }
    f = sources.front(fake_ex, _ctx())
    assert f["bundle"] is None and f["app"] == "Boh"


def test_front_fallisce_se_lsappinfo_fallisce(fake_ex):
    fake_ex.replies = {"lsappinfo front": R(False, error="boh")}
    assert sources.front(fake_ex, _ctx()) is None


def test_front_e_registrato_con_cadenza_di_un_secondo():
    assert sources.REGISTRY["front"].every == 1.0
    assert sources.REGISTRY["front"].app == ()
```

- [ ] **Passo 2: esegui e verifica il fallimento**

Run: `cd agent && .venv/bin/python -m pytest tests/test_sources.py -k front -v`
Atteso: FAIL con `AttributeError: module 'macdeck.sources' has no attribute 'parse_lsappinfo_info'`

- [ ] **Passo 3: implementa**

Aggiungi in fondo a `agent/macdeck/sources.py`:

```python
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
```

- [ ] **Passo 4: esegui i test**

Run: `cd agent && .venv/bin/python -m pytest tests/test_sources.py tests/test_state.py -v`
Atteso: PASS. Nota: `EMPTY_SNAPSHOT` ora contiene anche `front`; se un test di `test_state.py` o `test_app.py` confronta l'intero snapshot con un dizionario letterale, aggiornalo.

- [ ] **Passo 5: commit**

```bash
git add agent/macdeck/sources.py agent/tests/test_sources.py agent/tests/test_state.py
git commit -m "Sonda front: l'app in primo piano letta con lsappinfo, con i suoi tre nomi"
```

---

### Task 3: sonde `mail`, `slack`, `calendar`

**Files:**
- Modify: `agent/macdeck/sources.py`
- Test: `agent/tests/test_sources.py`

**Interfacce:**
- Produce: `mail.unread` (int|None); `slack.badge` (str|None); `calendar.next` (str|None), `calendar.next_at` (str `HH:MM`|None), `calendar.count_today` (int|None). Costanti `MAIL_SCRIPT`, `SLACK_BADGE_SCRIPT`, `CALENDAR_SCRIPT`.

- [ ] **Passo 1: test**

Aggiungi a `agent/tests/test_sources.py`:

```python
def test_mail_legge_le_non_lette(fake_ex):
    fake_ex.replies = {"unread count": R(True, out="12\n")}
    assert sources.mail(fake_ex, _ctx()) == {"unread": 12}


def test_mail_con_output_strano_fallisce(fake_ex):
    fake_ex.replies = {"unread count": R(True, out="boh\n")}
    assert sources.mail(fake_ex, _ctx()) is None


def test_mail_e_vincolata_allapp_in_esecuzione():
    assert sources.REGISTRY["mail"].app == ("com.apple.mail",)
    assert sources.REGISTRY["mail"].every == 5.0


def test_slack_badge_presente(fake_ex):
    fake_ex.replies = {"AXStatusLabel": R(True, out="3\n")}
    assert sources.slack(fake_ex, _ctx()) == {"badge": "3"}


def test_slack_senza_badge_da_null_non_fallimento(fake_ex):
    fake_ex.replies = {"AXStatusLabel": R(True, out="missing value\n")}
    assert sources.slack(fake_ex, _ctx()) == {"badge": None}


def test_slack_errore_di_accessibilita_e_un_fallimento(fake_ex):
    fake_ex.replies = {"AXStatusLabel": R(False, error="-1719")}
    assert sources.slack(fake_ex, _ctx()) is None


def test_slack_e_vincolata_allapp():
    assert sources.REGISTRY["slack"].app == ("com.tinyspeck.slackmacgap",)


def test_calendar_legge_conteggio_e_prossimo_evento(fake_ex):
    fake_ex.replies = {"every event of c": R(True, out="3\n14:30\tRiunione sprint\n")}
    assert sources.calendar(fake_ex, _ctx()) == {
        "next": "Riunione sprint", "next_at": "14:30", "count_today": 3}


def test_calendar_senza_eventi(fake_ex):
    fake_ex.replies = {"every event of c": R(True, out="0\n\n")}
    assert sources.calendar(fake_ex, _ctx()) == {
        "next": None, "next_at": None, "count_today": 0}


def test_calendar_ha_cadenza_lenta_e_app_vincolata():
    src = sources.REGISTRY["calendar"]
    assert src.every == 60.0
    assert src.app == ("com.apple.ical",)


def test_calendar_timeout_e_un_fallimento(fake_ex):
    fake_ex.replies = {"every event of c": R(False, error="timeout dopo 10.0s")}
    assert sources.calendar(fake_ex, _ctx()) is None
```

- [ ] **Passo 2: verifica il fallimento**

Run: `cd agent && .venv/bin/python -m pytest tests/test_sources.py -k "mail or slack or calendar" -v`
Atteso: FAIL con `AttributeError`

- [ ] **Passo 3: implementa**

Aggiungi in fondo a `agent/macdeck/sources.py`:

```python
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
```

- [ ] **Passo 4: esegui**

Run: `cd agent && .venv/bin/python -m pytest tests/test_sources.py -v`
Atteso: PASS

- [ ] **Passo 5: verifica manuale rapida degli script veri (non automatizzabile)**

```bash
osascript -e 'tell application "Mail" to return unread count of inbox'
```
Atteso: un numero. Per Calendar, con Calendar **aperto**, incolla `CALENDAR_SCRIPT` in un file `/tmp/cal.applescript` ed esegui `time osascript /tmp/cal.applescript`: annota il tempo nel commit se supera 2 s.

- [ ] **Passo 6: commit**

```bash
git add agent/macdeck/sources.py agent/tests/test_sources.py
git commit -m "Sonde Mail, Slack e Calendar: non lette, badge del Dock, prossimo evento"
```

---

### Task 4: sonda `claude` — il ponte con Claude Code

**Files:**
- Modify: `agent/macdeck/paths.py`, `agent/macdeck/sources.py`
- Test: `agent/tests/test_paths.py`, `agent/tests/test_sources.py`

**Interfacce:**
- Produce: `paths.claude_dir(root=None) -> Path` (`<config>/claude`); chiave `claude` con `alive` (bool), `model`, `remaining` (float|None), `dir` (str con `~`), `branch`, `session`. Costanti `CLAUDE_STALE_S = 1800`, `CLAUDE_PURGE_S = 86400`. Funzione `newest_claude_file(dir) -> Path | None`.

- [ ] **Passo 1: test**

Aggiungi a `agent/tests/test_paths.py`:

```python
def test_claude_dir_sta_sotto_la_config(tmp_path):
    from macdeck import paths
    d = paths.claude_dir(tmp_path)
    assert d == tmp_path / "macdeck" / "claude"
    assert d.is_dir()
```

Aggiungi a `agent/tests/test_sources.py`:

```python
import json
import os
import time
from pathlib import Path

from macdeck import paths

STATUS = {
    "session_id": "abc-123",
    "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
    "workspace": {"current_dir": str(Path.home() / "macdeck")},
    "context_window": {"remaining_percentage": 38.4},
}


def _scrivi(dir_, nome, dati, eta_s=0):
    p = dir_ / f"{nome}.json"
    p.write_text(json.dumps(dati))
    if eta_s:
        t = time.time() - eta_s
        os.utime(p, (t, t))
    return p


def _ctx_root(root, running=()):
    return sources.ProbeContext(running=frozenset(running), now=0.0, root=root)


def test_claude_vivo_legge_modello_percentuale_cartella_e_branch(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS)
    fake_ex.replies = {
        "pgrep": R(True, out="4242\n"),
        "branch --show-current": R(True, out="main\n"),
    }
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c == {"alive": True, "model": "Fable 5.1", "remaining": 38.4,
                 "dir": "~/macdeck", "branch": "main", "session": "abc-123"}


def test_claude_senza_processo_non_e_vivo_ma_i_dati_restano(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS)
    fake_ex.replies = {"pgrep": R(False, error="exit 1"),
                       "branch --show-current": R(True, out="main\n")}
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["alive"] is False
    assert c["model"] == "Fable 5.1"


def test_claude_con_file_vecchio_non_e_vivo(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS, eta_s=sources.CLAUDE_STALE_S + 60)
    fake_ex.replies = {"pgrep": R(True, out="4242\n")}
    assert sources.claude(fake_ex, _ctx_root(tmp_path))["alive"] is False


def test_claude_sceglie_il_file_piu_recente(fake_ex, tmp_path):
    d = paths.claude_dir(tmp_path)
    _scrivi(d, "vecchia", {**STATUS, "session_id": "vecchia",
                           "model": {"display_name": "Vecchio"}}, eta_s=300)
    _scrivi(d, "nuova", {**STATUS, "session_id": "nuova"})
    fake_ex.replies = {"pgrep": R(True, out="1\n")}
    assert sources.claude(fake_ex, _ctx_root(tmp_path))["session"] == "nuova"


def test_claude_cancella_i_file_piu_vecchi_di_un_giorno(fake_ex, tmp_path):
    d = paths.claude_dir(tmp_path)
    stantio = _scrivi(d, "stantio", STATUS, eta_s=sources.CLAUDE_PURGE_S + 10)
    _scrivi(d, "nuova", STATUS)
    sources.claude(fake_ex, _ctx_root(tmp_path))
    assert not stantio.exists()


def test_claude_senza_file_e_il_valore_vuoto_non_un_fallimento(fake_ex, tmp_path):
    paths.claude_dir(tmp_path)
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c == {"alive": False, "model": None, "remaining": None,
                 "dir": None, "branch": None, "session": None}


def test_claude_file_malformato_vale_come_assente(fake_ex, tmp_path):
    (paths.claude_dir(tmp_path) / "rotto.json").write_text("{non json")
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["alive"] is False and c["model"] is None


def test_claude_senza_branch_lascia_null(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS)
    fake_ex.replies = {"pgrep": R(True, out="1\n"),
                       "branch --show-current": R(False, error="not a git repository")}
    assert sources.claude(fake_ex, _ctx_root(tmp_path))["branch"] is None


def test_claude_non_e_vincolata_a_unapp_gui():
    assert sources.REGISTRY["claude"].app == ()
    assert sources.REGISTRY["claude"].every == 5.0
```

- [ ] **Passo 2: verifica il fallimento**

Run: `cd agent && .venv/bin/python -m pytest tests/test_paths.py tests/test_sources.py -k claude -v`
Atteso: FAIL con `AttributeError: module 'macdeck.paths' has no attribute 'claude_dir'`

- [ ] **Passo 3: implementa**

In `agent/macdeck/paths.py`, dopo `cache_dir`:

```python
def claude_dir(root: Path | None = None) -> Path:
    """Dove la statusLine di Claude Code lascia il suo JSON, un file per sessione."""
    return _subdir("claude", root)
```

In testa a `agent/macdeck/sources.py`, fra gli import esistenti, aggiungi `import json`, `import time` e `from . import paths` (`Path` e `re` ci sono già). Poi in fondo al modulo:

```python
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


@source("claude", empty={"alive": False, "model": None, "remaining": None,
                         "dir": None, "branch": None, "session": None},
        every=5.0)
def claude(ex: Executor, ctx: ProbeContext) -> dict | None:
    """Modello, contesto rimanente e cartella dell'ultima sessione di Claude
    Code con cui si e' parlato.

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
        return {}
    try:
        data = json.loads(f.read_text())
        eta = now - f.stat().st_mtime
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

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
    if cwd and cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    remaining = (data.get("context_window") or {}).get("remaining_percentage")
    try:
        remaining = float(remaining) if remaining is not None else None
    except (TypeError, ValueError):
        remaining = None

    return {
        "alive": bool(alive),
        "model": (data.get("model") or {}).get("display_name") or None,
        "remaining": remaining,
        "dir": cwd,
        "branch": branch,
        "session": data.get("session_id") or None,
    }
```

- [ ] **Passo 4: esegui**

Run: `cd agent && .venv/bin/python -m pytest tests/test_sources.py tests/test_paths.py tests/test_state.py -v`
Atteso: PASS

- [ ] **Passo 5: passa `root` alla sonda dal server**

In `agent/macdeck/cli.py`, funzione `build_serve_app`: dove viene costruito `StateProbe(ex)` (o simile) aggiungi `root=root`. Verifica con `grep -n "StateProbe(" agent/macdeck/cli.py`. In `_doctor` la chiamata `StateProbe(ex).refresh()` diventa `StateProbe(ex, root=args.root).refresh()`.

- [ ] **Passo 6: esegui tutto e commit**

Run: `cd agent && .venv/bin/python -m pytest`
Atteso: PASS

```bash
git add agent/macdeck/paths.py agent/macdeck/sources.py agent/macdeck/cli.py agent/tests/test_paths.py agent/tests/test_sources.py
git commit -m "Sonda claude: modello, contesto e cartella dal file che lascia la statusLine"
```

---

### Task 5: `layout.yaml` — `app:`, `kind`, `caption`, `span`, segnaposto

**Files:**
- Modify: `agent/macdeck/layout.py`
- Test: `agent/tests/test_layout.py`, `agent/tests/test_state.py`

**Interfacce:**
- Produce: `layout.normalize_app(value) -> list[str]` (minuscolo, percorsi ridotti allo stem, `.app` tolto); `layout.app_matches(apps: list[str], front: dict) -> bool`; `layout.span_box(boxes, index, span) -> dict`; pagine validate con `app: list[str] | None`; slot validati con `kind: "button"|"info"`, `caption: str|None`, `span: int`, `action: dict|None`, `icon: str|None` (default `text:?` solo sui pulsanti).
- `state.fill` e `state.placeholders` sono già in Task 1: qui si testano.

- [ ] **Passo 1: test di `fill` e `placeholders`**

Aggiungi a `agent/tests/test_state.py`:

```python
from macdeck.state import fill, placeholders

SNAP = {"media": {"title": "Anagrafe", "artist": None},
        "claude": {"remaining": 38.4}, "mail": {"unread": 0}}


def test_fill_sostituisce_i_valori():
    assert fill("{media.title} — {mail.unread}", SNAP) == "Anagrafe — 0"


def test_fill_valore_assente_diventa_vuoto():
    assert fill("[{media.artist}] [{boh.niente}]", SNAP) == "[] []"


def test_fill_filtro_int():
    assert fill("{claude.remaining|int}%", SNAP) == "38%"


def test_fill_filtro_int_su_non_numero_da_vuoto():
    assert fill("{media.title|int}", SNAP) == ""


def test_fill_senza_segnaposto_e_identita():
    assert fill("Play / Pausa", SNAP) == "Play / Pausa"
    assert fill("", SNAP) == ""


def test_placeholders_elenca_le_chiavi():
    assert placeholders("{a.b} e {c.d|int}") == ["a.b", "c.d"]
    assert placeholders("niente") == []
```

- [ ] **Passo 2: esegui**

Run: `cd agent && .venv/bin/python -m pytest tests/test_state.py -k "fill or placeholders" -v`
Atteso: PASS (l'implementazione è del Task 1). Se fallisce, correggi `fill` finché passa.

- [ ] **Passo 3: test della validazione**

Aggiungi a `agent/tests/test_layout.py`:

```python
# ------------------------------------------------------------- pagine per app

def _pagina(**extra):
    return {"pages": [{"name": "P", "slots": [], **extra}]}


def test_app_stringa_diventa_lista_minuscola():
    out = L.validate(_pagina(app="Spotify"))
    assert out["pages"][0]["app"] == ["spotify"]


def test_app_lista_e_percorsi_si_normalizzano():
    out = L.validate(_pagina(app=["/Applications/iTerm.app", "com.apple.Terminal"]))
    assert out["pages"][0]["app"] == ["iterm", "com.apple.terminal"]


def test_pagina_senza_app_ha_app_none():
    assert L.validate(_pagina())["pages"][0]["app"] is None


def test_app_di_tipo_sbagliato_viene_rifiutato():
    with pytest.raises(L.LayoutError) as e:
        L.validate(_pagina(app=42))
    assert "app" in str(e.value)


def test_app_matches_su_uno_dei_tre_nomi():
    front = {"app": "iTerm2", "name": "iTerm", "bundle": "com.googlecode.iterm2"}
    assert L.app_matches(["iterm"], front)
    assert L.app_matches(["iterm2"], front)
    assert L.app_matches(["com.googlecode.iterm2"], front)
    assert not L.app_matches(["terminal"], front)
    assert not L.app_matches(["iterm"], {"app": None, "name": None, "bundle": None})


# ------------------------------------------------------------ tile informative

def _slot(**s):
    base = {"pos": [0, 0], "label": "x", "icon": "text:x", "action": {"type": "noop"}}
    return {"pages": [{"name": "P", "slots": [{**base, **s}]}]}


def test_kind_manca_vale_button_e_span_uno():
    s = L.validate(_slot())["pages"][0]["slots"][0]
    assert s["kind"] == "button" and s["span"] == 1 and s["caption"] is None


def test_info_senza_azione_e_lecita():
    raw = _slot(kind="info", caption="{media.artist}")
    del raw["pages"][0]["slots"][0]["action"]
    s = L.validate(raw)["pages"][0]["slots"][0]
    assert s["kind"] == "info" and s["action"] is None
    assert s["caption"] == "{media.artist}"


def test_info_senza_icona_non_riceve_il_punto_di_domanda():
    raw = _slot(kind="info")
    del raw["pages"][0]["slots"][0]["icon"]
    assert L.validate(raw)["pages"][0]["slots"][0]["icon"] is None


def test_button_senza_azione_resta_un_errore():
    raw = _slot()
    del raw["pages"][0]["slots"][0]["action"]
    with pytest.raises(L.LayoutError):
        L.validate(raw)


def test_kind_ignoto_viene_rifiutato():
    with pytest.raises(L.LayoutError) as e:
        L.validate(_slot(kind="banner"))
    assert "kind" in str(e.value)


def test_span_che_esce_dalla_griglia_viene_rifiutato():
    with pytest.raises(L.LayoutError) as e:
        L.validate(_slot(pos=[2, 0], span=2))
    assert "span" in str(e.value)


def test_span_occupa_le_caselle_coperte():
    raw = _slot(span=2)
    raw["pages"][0]["slots"].append(
        {"pos": [1, 0], "label": "y", "icon": "text:y", "action": {"type": "noop"}})
    with pytest.raises(L.LayoutError) as e:
        L.validate(raw)
    assert "occupat" in str(e.value)


def test_span_non_intero_o_zero_viene_rifiutato():
    for cattivo in (0, "2", 1.5):
        with pytest.raises(L.LayoutError):
            L.validate(_slot(span=cattivo))


def test_span_box_allarga_di_span_colonne():
    boxes = L.slot_boxes({"cols": 3, "rows": 2})
    b = L.span_box(boxes, 0, 3)
    assert b["x"] == boxes[0]["x"] and b["y"] == boxes[0]["y"]
    assert b["w"] == 3 * boxes[0]["w"] + 2 * L.GUTTER
    assert L.span_box(boxes, 4, 1) == boxes[4]


def test_i_layout_di_ieri_validano_uguali():
    """Le pagine senza app: (quelle di ieri) devono validare come prima:
    tutti pulsanti, span 1, azione presente."""
    out = L.validate(L.DEFAULT_LAYOUT)
    for p in out["pages"]:
        if p["app"]:
            continue
        for s in p["slots"]:
            assert s["kind"] == "button" and s["span"] == 1
            assert s["action"] is not None
```

- [ ] **Passo 4: verifica il fallimento**

Run: `cd agent && .venv/bin/python -m pytest tests/test_layout.py -v`
Atteso: FAIL (`KeyError: 'app'`, `AttributeError: app_matches`, ...)

- [ ] **Passo 5: implementa in `layout.py`**

Dopo `slot_index`:

```python
def span_box(boxes: dict[int, dict], index: int, span: int) -> dict:
    """Il rettangolo di uno slot che occupa `span` colonne a partire da `index`."""
    b = boxes[index]
    if span <= 1:
        return dict(b)
    return {**b, "w": span * b["w"] + (span - 1) * GUTTER}


def normalize_app(value) -> list[str]:
    """`app:` accetta nome, bundle id o percorso, singolo o in lista.

    Si riduce tutto a minuscolo, e un percorso al solo nome del bundle senza
    `.app`, cosi' "/Applications/iTerm.app" e "iTerm" sono la stessa cosa.
    """
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise LayoutError("'app' deve essere una stringa o una lista di stringhe")
        s = item.strip()
        if "/" in s:
            s = s.rstrip("/").rsplit("/", 1)[-1]
        s = s.removesuffix(".app").lower()
        out.append(s)
    return out


def app_matches(apps: list[str] | None, front: dict | None) -> bool:
    """Vero se l'app davanti corrisponde a una voce di `app:`.

    Tre nomi perche' non coincidono (iTerm2 / iTerm / com.googlecode.iterm2):
    qualunque dei tre basta.
    """
    if not apps or not front:
        return False
    nomi = {
        str(v).lower() for v in (front.get("app"), front.get("name"), front.get("bundle"))
        if v
    }
    nomi |= {n.removesuffix(".app") for n in nomi}
    return any(a in nomi for a in apps)
```

In `validate`, per la pagina, dopo il controllo di `when`:

```python
        app_raw = page.get("app")
        try:
            app = normalize_app(app_raw) if app_raw is not None else None
        except LayoutError as e:
            raise LayoutError(f"{where_page}: {e}") from e
```

e nel dizionario finale della pagina aggiungi `"app": app`.

Nel ciclo degli slot, sostituisci il blocco da `index = slot_index(...)` fino all'`append` con:

```python
            index = slot_index(list(pos), page_grid)
            slot_when = slot.get("when")
            if slot_when is not None and not isinstance(slot_when, str):
                raise LayoutError(
                    f"{where_page}, slot {list(pos)}: 'when' deve essere una stringa"
                )
            where_slot = f"{where_page}, slot {list(pos)}"

            kind = slot.get("kind", "button")
            if kind not in ("button", "info"):
                raise LayoutError(f"{where_slot}: 'kind' deve essere button o info, non {kind!r}")
            caption = slot.get("caption")
            if caption is not None and not isinstance(caption, str):
                raise LayoutError(f"{where_slot}: 'caption' deve essere una stringa")
            span = slot.get("span", 1)
            if isinstance(span, bool) or not isinstance(span, int) or span < 1:
                raise LayoutError(f"{where_slot}: 'span' deve essere un intero >= 1")
            if col + span > page_grid["cols"]:
                raise LayoutError(
                    f"{where_slot}: span {span} esce dalla griglia "
                    f"{page_grid['cols']}x{page_grid['rows']}"
                )
            coperte = range(index, index + span)

            # Piu' slot possono condividere una posizione, purche' al massimo
            # uno sia incondizionato: e' cosi' che la riga in basso diventa i
            # comandi multimediali quando un player e' attivo, e torna alle
            # app quando non lo e'. Due slot incondizionati sulla stessa
            # casella sarebbero invece un errore di battitura. Le caselle
            # coperte da uno span contano come occupate.
            if slot_when is None:
                for i in coperte:
                    if i in occupati:
                        raise LayoutError(
                            f"{where_page}: slot {list(pos)} occupato da "
                            f"{occupati[i]!r} e nessuno dei due ha 'when'"
                        )
                for i in coperte:
                    occupati[i] = slot.get("label", "")

            action_raw = slot.get("action")
            if action_raw is None and kind == "info":
                action = None
            else:
                action = _check_action(action_raw, where_slot)

            icon = slot.get("icon")
            if not icon and kind == "button":
                icon = "text:?"

            slots.append(
                {
                    "pos": [col, row],
                    "index": index,
                    "when": slot_when,
                    "kind": kind,
                    "label": slot.get("label", ""),
                    "caption": caption,
                    "span": span,
                    "icon": icon or None,
                    "color": slot.get("color"),
                    "state": slot.get("state"),
                    "timeout_ms": slot.get("timeout_ms"),
                    "action": action,
                }
            )
```

- [ ] **Passo 6: esegui tutti i test**

Run: `cd agent && .venv/bin/python -m pytest`
Atteso: PASS. `test_validate_normalizza_i_default_mancanti` o test di `test_app.py` che confrontano uno slot intero con un dizionario letterale vanno aggiornati con le nuove chiavi (`kind`, `caption`, `span`, e `app` sulle pagine).

- [ ] **Passo 7: commit**

```bash
git add agent/macdeck/layout.py agent/tests/test_layout.py agent/tests/test_state.py
git commit -m "layout.yaml: pagine con app:, tile informative con caption e span, segnaposto nelle etichette"
```

---

### Task 6: rendering della tile informativa

**Files:**
- Modify: `agent/macdeck/render.py`
- Test: `agent/tests/test_render.py`

**Interfacce:**
- Produce: `render.render_info_tile(slot, theme, *, root=None) -> Image`; `render_tile` smista su `slot.get("kind")`; `_key` include `kind` e `caption`. Lo slot arriva **già risolto**: `label` e `caption` sono testo finito, `box` ha già la larghezza dello span.

- [ ] **Passo 1: test**

Aggiungi a `agent/tests/test_render.py` (gli import sono ripetuti apposta: se il file li ha già, tienine una copia sola):

```python
from macdeck import layout as L
from macdeck import render

BOXES = L.slot_boxes({"cols": 3, "rows": 2})


def _info(**s):
    return {"kind": "info", "label": "Anagrafe", "caption": "Marlene Kuntz",
            "icon": None, "box": L.span_box(BOXES, 0, 3), **s}


def test_info_ha_la_dimensione_del_box_allargato():
    im = render.render_tile(_info(), L.DEFAULT_THEME)
    assert im.size == (3 * BOXES[0]["w"] + 2 * L.GUTTER, BOXES[0]["h"])


def test_info_disegna_qualcosa():
    im = render.render_tile(_info(), L.DEFAULT_THEME)
    fondo = im.getpixel((0, 0))
    assert any(im.getpixel((x, im.height // 2)) != fondo for x in range(im.width))


def test_info_valore_vuoto_mostra_solo_la_didascalia_senza_sollevare():
    im_vuota = render.render_tile(_info(label=""), L.DEFAULT_THEME)
    im_niente = render.render_tile(_info(label="", caption=""), L.DEFAULT_THEME)
    assert im_vuota.tobytes() != im_niente.tobytes()


def test_info_valore_lunghissimo_resta_nel_box():
    lungo = "Un titolo di brano davvero interminabile " * 4
    im = render.render_tile(_info(label=lungo, box=BOXES[0]), L.DEFAULT_THEME)
    assert im.size == (BOXES[0]["w"], BOXES[0]["h"])


def test_info_con_icona_e_diversa_da_senza():
    con = render.render_tile(_info(icon="text:S"), L.DEFAULT_THEME)
    senza = render.render_tile(_info(), L.DEFAULT_THEME)
    assert con.tobytes() != senza.tobytes()


def test_cache_distingue_kind_e_caption():
    c = render.TileCache()
    a = c.png({**_info(), "kind": "button", "caption": None, "icon": "text:A",
               "label": "X"}, L.DEFAULT_THEME)
    b = c.png({**_info(), "label": "X", "icon": "text:A"}, L.DEFAULT_THEME)
    assert a != b
    assert c.size == 2
```

- [ ] **Passo 2: verifica il fallimento**

Run: `cd agent && .venv/bin/python -m pytest tests/test_render.py -k info -v`
Atteso: FAIL (una tile `info` oggi viene disegnata come pulsante, quindi `test_info_disegna_qualcosa` può passare per caso; devono fallire almeno `test_cache_distingue_kind_e_caption` e `test_info_con_icona_e_diversa_da_senza`... se passano tutti, verifica che `render_tile` non gestisca già `kind`).

- [ ] **Passo 3: implementa**

In `agent/macdeck/render.py`, rinomina l'attuale `render_tile` in `render_button_tile` e aggiungi:

```python
INFO_MIN_PX = 12
INFO_VALUE_RATIO = 0.45


def _dim(color: str, background: str) -> str:
    """Il colore del testo attenuato verso lo sfondo della tile."""
    def rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a, b = rgb(color), rgb(background)
    return "#%02x%02x%02x" % tuple((x * 3 + y * 2) // 5 for x, y in zip(a, b))


def _ellipsize(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
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


def render_tile(slot: dict, theme: dict, *, root: Path | None = None) -> Image.Image:
    if slot.get("kind") == "info":
        return render_info_tile(slot, theme, root=root)
    return render_button_tile(slot, theme, root=root)
```

In `render_button_tile` la riga `icon = icons.resolve(slot.get("icon") or "", ...)` resta com'è. In `_key`, aggiungi `"kind": slot.get("kind")` e `"caption": slot.get("caption")` al payload.

- [ ] **Passo 4: esegui**

Run: `cd agent && .venv/bin/python -m pytest tests/test_render.py -v`
Atteso: PASS

- [ ] **Passo 5: provino visivo (facoltativo ma consigliato)**

```bash
cd agent && .venv/bin/python - <<'EOF'
from macdeck import layout as L, render
b = L.slot_boxes({"cols": 3, "rows": 2})
s = {"kind": "info", "label": "Anagrafe", "caption": "Marlene Kuntz", "icon": "mdi:music", "box": L.span_box(b, 0, 3)}
render.render_tile(s, L.DEFAULT_THEME).save("/tmp/provino-info.png")
EOF
open /tmp/provino-info.png
```

Se il font MDI non è installato l'icona è il ripiego: va bene lo stesso.

- [ ] **Passo 6: commit**

```bash
git add agent/macdeck/render.py agent/tests/test_render.py
git commit -m "Tile informativa: valore grande su una riga, didascalia sotto, icona a lato"
```

---

### Task 7: `_resolve`, salto di pagina e API

**Files:**
- Modify: `agent/macdeck/app.py`
- Test: `agent/tests/test_app.py`

**Interfacce:**
- Consuma: `L.app_matches`, `L.span_box`, `state.fill`, `sources.known_keys`.
- Produce: `/layout` risponde `page: 0` quando l'app davanti è cambiata dall'ultimo `/layout`; `slots` contiene solo slot con `action`; `/api/config` espone `state_keys: list[str]`; `/api/tile-preview` accetta `kind`, `caption`, `span` e riempie i segnaposto; `/api/icons` aggiunge `bundle` a ogni app.

- [ ] **Passo 1: test**

Aggiungi a `agent/tests/test_app.py`:

```python
# ------------------------------------------------------- pagine per app

LSAPP_LIST = ' 1) "Spotify" ASN:0x0-0x1:\n    bundleID="com.spotify.client"\n'
LSAPP_INFO_SPOTIFY = ('"LSDisplayName"="Spotify"\n"CFBundleIdentifier"="com.spotify.client"\n'
                      '"CFBundleExecutablePath"="/Applications/Spotify.app/Contents/MacOS/Spotify"\n')
LSAPP_INFO_CHROME = ('"LSDisplayName"="Google Chrome"\n"CFBundleIdentifier"="com.google.Chrome"\n'
                     '"CFBundleExecutablePath"="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"\n')

LAYOUT_PER_APP = {
    "pages": [
        {"name": "Griglia", "slots": [
            {"pos": [0, 0], "label": "A", "icon": "text:A", "action": {"type": "noop"}}]},
        {"name": "Spotify", "app": "com.spotify.client", "grid": {"cols": 3, "rows": 2},
         "slots": [
            {"pos": [0, 0], "kind": "info", "label": "{media.title}",
             "caption": "{media.artist}", "span": 3},
            {"pos": [0, 1], "label": "Play", "icon": "text:P",
             "action": {"type": "media", "op": "play_pause"}},
            {"pos": [1, 1], "kind": "info", "label": "{volume.level}",
             "action": {"type": "volume", "op": "mute_toggle"}},
         ]},
        {"name": "Altra", "slots": []},
    ]
}


def _davanti(fake_ex, info, brano="Anagrafe"):
    fake_ex.replies = {
        "lsappinfo front": R(True, out="ASN:0x0-0x1:\n"),
        "lsappinfo info": R(True, out=info),
        "lsappinfo list": R(True, out=LSAPP_LIST),
        "running_apps": R(True, out=f"Spotify\ntrue\n{brano}\nMarlene Kuntz\n"),
        "output volume of": R(True, out="40\nfalse\n"),
    }


def test_con_lapp_davanti_la_sua_pagina_e_la_prima(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    body = client.get("/layout?page=0", headers=AUTH).json()
    assert body["pages"] == ["Spotify", "Griglia", "Altra"]


def test_senza_lapp_davanti_la_sua_pagina_non_ce(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_CHROME)
    probe.refresh()
    body = client.get("/layout?page=0", headers=AUTH).json()
    assert body["pages"] == ["Griglia", "Altra"]


def test_al_cambio_di_app_il_server_risponde_pagina_zero(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_CHROME)
    probe.refresh()
    client.get("/layout?page=0", headers=AUTH)               # ricordo: Chrome
    body = client.get("/layout?page=1", headers=AUTH).json()  # swipe su Altra
    assert body["page"] == 1
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    body = client.get("/layout?page=1", headers=AUTH).json()
    assert body["page"] == 0 and body["pages"][0] == "Spotify"


def test_un_brano_nuovo_non_fa_saltare_pagina(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY, brano="Uno")
    probe.refresh()
    client.get("/layout?page=0", headers=AUTH)
    v1 = client.get("/layout?page=1", headers=AUTH).json()["version"]
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY, brano="Due")
    probe.refresh()
    body = client.get("/layout?page=1", headers=AUTH).json()
    assert body["page"] == 1                 # resto sulla griglia
    assert body["version"] != v1             # ma la versione e' cambiata


def test_le_etichette_con_segnaposto_arrivano_riempite_nella_firma(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY, brano="Uno")
    probe.refresh()
    a = client.get("/screen/0.png", headers=AUTH).content
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY, brano="Due")
    probe.refresh()
    b = client.get("/screen/0.png", headers=AUTH).content
    assert a != b


def test_le_tile_info_senza_azione_non_sono_aree_di_tocco(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    body = client.get("/layout?page=0", headers=AUTH).json()
    indici = sorted(s["i"] for s in body["slots"])
    assert indici == [3, 4]                  # Play e la info con azione


def test_press_su_una_info_senza_azione_e_404(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    client.get("/layout?page=0", headers=AUTH)
    r = client.post("/press", headers=AUTH, json={"page": 0, "slot": 0})
    assert r.status_code == 404


def test_lo_span_allarga_larea_di_tocco(ctx):
    client, store, fake_ex, probe = ctx
    store.save({"pages": [{"name": "P", "grid": {"cols": 3, "rows": 2}, "slots": [
        {"pos": [0, 0], "span": 2, "label": "L", "icon": "text:L", "action": {"type": "noop"}}]}]})
    body = client.get("/layout", headers=AUTH).json()
    boxes = L.slot_boxes({"cols": 3, "rows": 2})
    assert body["slots"][0]["w"] == 2 * boxes[0]["w"] + L.GUTTER


def test_api_config_elenca_le_chiavi_di_stato(ctx):
    client, *_ = ctx
    keys = client.get("/api/config", headers=LOCAL).json()["state_keys"]
    assert "media.title" in keys and "front.app" in keys and "accessibility_ok" in keys


def test_tile_preview_riempie_i_segnaposto_e_rispetta_lo_span(ctx):
    client, store, fake_ex, probe = ctx
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY, brano="Uno")
    probe.refresh()
    corpo = {"grid": {"cols": 3, "rows": 2},
             "slot": {"pos": [0, 0], "kind": "info", "label": "{media.title}", "span": 3}}
    a = client.post("/api/tile-preview", headers=LOCAL, json=corpo)
    with Image.open(io.BytesIO(a.content)) as im:
        boxes = L.slot_boxes({"cols": 3, "rows": 2})
        assert im.size[0] == 3 * boxes[0]["w"] + 2 * L.GUTTER
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY, brano="Due")
    probe.refresh()
    b = client.post("/api/tile-preview", headers=LOCAL, json=corpo)
    assert a.content != b.content


def test_api_icons_espone_il_bundle_id(ctx):
    client, *_ = ctx
    apps = client.get("/api/icons", headers=LOCAL).json()["apps"]
    if apps:                                   # dipende dal Mac che esegue i test
        assert "bundle" in apps[0]
```

- [ ] **Passo 2: verifica il fallimento**

Run: `cd agent && .venv/bin/python -m pytest tests/test_app.py -k "app or segnaposto or info or span or state_keys or preview or bundle" -v`
Atteso: FAIL

- [ ] **Passo 3: implementa in `app.py`**

Aggiorna gli import:

```python
from . import actions, icons, keymap, render
from . import layout as L
from . import sources
from .executor import Executor
from .layout import LayoutStore
from .render import TileCache
from .state import StateProbe, fill, value_at
```

Sostituisci `_resolve` con:

```python
    def _front_key(stato: dict) -> str | None:
        front = stato.get("front") or {}
        return front.get("bundle") or front.get("app")

    def _resolve(stato: dict | None = None) -> list[dict]:
        """Cosa il display deve mostrare adesso: pagine e slot gia' risolti.

        Quattro cose si decidono qui, tutte dipendenti dallo stato vivo del
        Mac e quindi impossibili da precalcolare in validate():

        - l'ORDINE del mazzo: prima le pagine la cui `app:` e' in primo
          piano, poi quelle senza `app:`. Le pagine di app non davanti non
          ci sono: sarebbero comandi per una finestra che non c'e';
        - quali PAGINE sono visibili (`when:` sulla pagina);
        - quali SLOT occupano ciascuna casella (`when:` sullo slot);
        - il TESTO delle etichette: i segnaposto `{media.title}` diventano
          il valore corrente, cosi' la firma cambia quando cambia il valore.
        """
        if stato is None:
            stato = probe.snapshot()
        front = stato.get("front") or {}

        def visibile(p: dict) -> bool:
            return not p.get("when") or bool(value_at(stato, p["when"]))

        davanti = [p for p in store.layout["pages"]
                   if p.get("app") and visibile(p) and L.app_matches(p["app"], front)]
        base = [p for p in store.layout["pages"] if not p.get("app") and visibile(p)]
        pagine = (davanti + base) \
            or [p for p in store.layout["pages"] if not p.get("app")] \
            or store.layout["pages"]

        risolte = []
        for pagina in pagine:
            boxes = L.slot_boxes(pagina["grid"])
            scelti: dict[int, dict] = {}
            for slot in pagina["slots"]:
                cond = slot.get("when")
                attivo = bool(cond) and bool(value_at(stato, cond))
                if cond and not attivo:
                    continue
                prima = scelti.get(slot["index"])
                if prima is not None and not (attivo and prima.get("when") is None):
                    continue      # a parita' di casella vince il condizionale
                scelti[slot["index"]] = {
                    **slot,
                    "box": L.span_box(boxes, slot["index"], slot.get("span") or 1),
                    "label": fill(slot.get("label") or "", stato),
                    "caption": fill(slot.get("caption") or "", stato),
                }
            risolte.append({**pagina, "slots": [scelti[i] for i in sorted(scelti)]})
        return risolte

    # L'app che era davanti all'ultimo /layout servito. Se e' cambiata, la
    # risposta successiva porta il display a pagina 0, dove sta la pagina
    # dell'app nuova. Se non e' cambiata, si clampa e basta: un brano nuovo
    # non deve riportare alla pagina Spotify chi e' andato sulla griglia.
    ricordo = {"front": None}
```

In `_slot`, dopo aver trovato lo slot con l'indice giusto:

```python
        for slot in page["slots"]:
            if slot["index"] == slot_index:
                if slot.get("action") is None:
                    break             # tile informativa: niente da premere
                return slot
        raise HTTPException(...)
```

In `get_layout`:

```python
    def get_layout(page: int = 0) -> dict:
        stato = probe.snapshot()
        risolte = _resolve(stato)
        chiave = _front_key(stato)
        if chiave != ricordo["front"]:
            ricordo["front"] = chiave
            page = 0 if risolte else _page_index(page, risolte)
        else:
            page = _page_index(page, risolte)
        p = risolte[page]
        ...
        "slots": [
            {...}
            for s in p["slots"]
            if s.get("action") is not None
        ],
```

`_page_index` solleva 503 se `risolte` è vuoto: chiamalo comunque prima di indicizzare (`page = 0` è valido solo se `risolte` non è vuoto; con `or store.layout["pages"]` non lo è mai, ma lascia la guardia).

In `read_config` aggiungi:

```python
            "state_keys": sorted(sources.known_keys() + ["accessibility_ok"]),
```

In `tile_preview`, sostituisci il calcolo del box e del rendering con:

```python
        slot = dict(body.get("slot") or {})
        pos = slot.get("pos") or [0, 0]
        boxes = L.slot_boxes(grid)
        index = L.slot_index(pos, grid)
        span = slot.get("span") or 1
        if not isinstance(span, int) or span < 1 or pos[0] + span > grid["cols"]:
            span = 1
        base = boxes.get(index) or next(iter(boxes.values()))
        slot["box"] = L.span_box(boxes, index, span) if index in boxes else base
        stato = probe.snapshot()
        slot["label"] = fill(slot.get("label") or "", stato)
        slot["caption"] = fill(slot.get("caption") or "", stato)
```

In `list_icons`, nell'`append` di ogni app aggiungi `"bundle": icons._bundle_identifier(bundle)` (rendi pubblica la funzione rinominandola `bundle_identifier` in `icons.py` e aggiornando i suoi usi interni, oppure aggiungi un alias pubblico `bundle_identifier = _bundle_identifier`).

- [ ] **Passo 4: esegui tutto**

Run: `cd agent && .venv/bin/python -m pytest`
Atteso: PASS. Il test esistente `test_pagina_fuori_intervallo_viene_riportata_dentro` fa una sola chiamata a `/layout` con pagina fuori intervallo: alla prima chiamata il ricordo è `None` e la chiave è `None` (nessuna sonda `front` finta), quindi non c'è salto e il clamp vale come prima. Se un test esistente chiama `/layout?page=N` una sola volta aspettandosi `N` **con** una sonda `front` finta, aggiungi una chiamata preliminare a `/layout` per fissare il ricordo.

- [ ] **Passo 5: commit**

```bash
git add agent/macdeck/app.py agent/macdeck/icons.py agent/tests/test_app.py
git commit -m "Il server sceglie la pagina: l'app davanti va per prima e al cambio si salta a zero"
```

---

### Task 8: layout di default, `doctor` e README

**Files:**
- Modify: `agent/macdeck/layout.py` (`DEFAULT_LAYOUT`), `agent/macdeck/cli.py`, `README.md`
- Test: `agent/tests/test_layout.py`, `agent/tests/test_cli.py`

**Interfacce:**
- Produce: `cli.claude_bridge_status(root) -> tuple[bool, str]` (ok, messaggio); `cli.pagine_con_app_assente(layout) -> list[tuple[str, str]]` (nome pagina, app non trovata). Costante `cli.CLAUDE_STATUSLINE_SNIPPET`.

- [ ] **Passo 1: test**

Aggiungi a `agent/tests/test_layout.py`:

```python
def test_il_default_ha_le_cinque_pagine_per_app():
    out = L.validate(L.DEFAULT_LAYOUT)
    per_app = {p["name"]: p["app"] for p in out["pages"] if p["app"]}
    assert set(per_app) == {"Spotify", "Mail", "Claude Code", "Slack", "Calendar"}
    assert per_app["Claude Code"] == ["com.googlecode.iterm2", "com.apple.terminal"]
    claude = next(p for p in out["pages"] if p["name"] == "Claude Code")
    assert claude["when"] == "claude.alive"


def test_le_pagine_per_app_hanno_almeno_una_tile_info_con_segnaposto():
    from macdeck.state import placeholders
    out = L.validate(L.DEFAULT_LAYOUT)
    for p in out["pages"]:
        if not p["app"]:
            continue
        info = [s for s in p["slots"] if s["kind"] == "info"]
        assert info, p["name"]
        assert any(placeholders(s["label"]) for s in info), p["name"]


def test_i_segnaposto_del_default_esistono_nel_registro():
    from macdeck import sources
    from macdeck.state import placeholders
    note = set(sources.known_keys())
    for p in L.validate(L.DEFAULT_LAYOUT)["pages"]:
        for s in p["slots"]:
            for k in placeholders(s["label"] or "") + placeholders(s["caption"] or ""):
                assert k in note, f"{p['name']}: {k}"
```

Aggiungi a `agent/tests/test_cli.py`:

```python
import json
import os
import time

from macdeck import cli, paths


def test_ponte_claude_assente_spiega_la_riga_da_aggiungere(tmp_path):
    ok, msg = cli.claude_bridge_status(tmp_path)
    assert ok is False
    assert "statusLine" in msg and "macdeck/claude" in msg


def test_ponte_claude_fresco_e_ok(tmp_path):
    (paths.claude_dir(tmp_path) / "s.json").write_text(json.dumps({"session_id": "s"}))
    ok, msg = cli.claude_bridge_status(tmp_path)
    assert ok is True and "s.json" in msg


def test_ponte_claude_stantio_non_e_ok(tmp_path):
    f = paths.claude_dir(tmp_path) / "s.json"
    f.write_text("{}")
    t = time.time() - 3 * 3600
    os.utime(f, (t, t))
    ok, msg = cli.claude_bridge_status(tmp_path)
    assert ok is False and "ore" in msg


def test_pagine_con_app_assente_segnala_solo_le_introvabili(monkeypatch):
    from macdeck import layout as L
    monkeypatch.setattr(cli.icons, "locate_bundle",
                        lambda t: "/x/Spotify.app" if "spotify" in t else None)
    layout = L.validate({"pages": [
        {"name": "Griglia", "slots": []},
        {"name": "Spotify", "app": "com.spotify.client", "slots": []},
        {"name": "Boh", "app": ["Inesistente"], "slots": []},
    ]})
    assert cli.pagine_con_app_assente(layout) == [("Boh", "inesistente")]
```

- [ ] **Passo 2: verifica il fallimento**

Run: `cd agent && .venv/bin/python -m pytest tests/test_layout.py tests/test_cli.py -k "default or ponte or assente" -v`
Atteso: FAIL

- [ ] **Passo 3: le cinque pagine nel `DEFAULT_LAYOUT`**

In `agent/macdeck/layout.py`, dopo la pagina `Dev` dentro `"pages": [...]`, aggiungi:

```python
        # --- pagine per app: compaiono, e il deck ci salta, quando l'app e'
        #     in primo piano. Le scorciatoie sono quelle di default delle
        #     app: si correggono dalla GUI se una versione le cambia.
        {
            "name": "Spotify",
            "app": "com.spotify.client",
            "grid": {"cols": 3, "rows": 2},
            "slots": [
                {"pos": [0, 0], "kind": "info", "span": 3, "icon": "mdi:music",
                 "label": "{media.title}", "caption": "{media.artist}"},
                {"pos": [0, 1], "label": "Indietro", "icon": "mdi:skip-previous",
                 "action": {"type": "media", "op": "prev"}},
                {"pos": [1, 1], "label": "Play / Pausa", "icon": "mdi:play-pause",
                 "action": {"type": "media", "op": "play_pause"}},
                {"pos": [2, 1], "label": "Avanti", "icon": "mdi:skip-next",
                 "action": {"type": "media", "op": "next"}},
            ],
        },
        {
            "name": "Mail",
            "app": "com.apple.mail",
            "grid": {"cols": 3, "rows": 2},
            "slots": [
                {"pos": [0, 0], "kind": "info", "span": 3, "icon": "mdi:email",
                 "label": "{mail.unread}", "caption": "da leggere"},
                {"pos": [0, 1], "label": "Nuovo", "icon": "mdi:email-plus",
                 "action": {"type": "keys", "keys": "cmd+n"}},
                {"pos": [1, 1], "label": "Rispondi", "icon": "mdi:reply",
                 "action": {"type": "keys", "keys": "cmd+r"}},
                {"pos": [2, 1], "label": "Archivia", "icon": "mdi:archive-arrow-down",
                 "action": {"type": "keys", "keys": "ctrl+cmd+a"}},
            ],
        },
        {
            "name": "Claude Code",
            "app": ["com.googlecode.iterm2", "com.apple.Terminal"],
            "when": "claude.alive",
            "grid": {"cols": 3, "rows": 3},
            "slots": [
                {"pos": [0, 0], "kind": "info", "icon": "mdi:robot-outline",
                 "label": "{claude.model}", "caption": "modello"},
                {"pos": [1, 0], "kind": "info", "icon": "mdi:gauge",
                 "label": "{claude.remaining|int}%", "caption": "contesto rimanente"},
                {"pos": [2, 0], "kind": "info", "icon": "mdi:folder-outline",
                 "label": "{claude.dir}", "caption": "{claude.branch}"},
                {"pos": [0, 1], "label": "Esc", "icon": "mdi:keyboard-esc",
                 "action": {"type": "keys", "keys": "escape"}},
                {"pos": [1, 1], "label": "Invio", "icon": "mdi:keyboard-return",
                 "action": {"type": "keys", "keys": "return"}},
                {"pos": [2, 1], "label": "Modalità", "icon": "mdi:swap-horizontal",
                 "action": {"type": "keys", "keys": "shift+tab"}},
                {"pos": [0, 2], "label": "/compact", "icon": "mdi:arrow-collapse",
                 "action": {"type": "sequence", "steps": [
                     {"type": "text", "text": "/compact"},
                     {"type": "keys", "keys": "return"}]}},
                {"pos": [1, 2], "label": "/clear", "icon": "mdi:broom",
                 "action": {"type": "sequence", "steps": [
                     {"type": "text", "text": "/clear"},
                     {"type": "keys", "keys": "return"}]}},
            ],
        },
        {
            "name": "Slack",
            "app": "com.tinyspeck.slackmacgap",
            "grid": {"cols": 3, "rows": 2},
            "slots": [
                {"pos": [0, 0], "kind": "info", "span": 3, "icon": "mdi:slack",
                 "label": "{slack.badge}", "caption": "non letti"},
                {"pos": [0, 1], "label": "Non letti", "icon": "mdi:email-mark-as-unread",
                 "action": {"type": "keys", "keys": "shift+cmd+a"}},
                {"pos": [1, 1], "label": "Cerca", "icon": "mdi:magnify",
                 "action": {"type": "keys", "keys": "cmd+k"}},
                {"pos": [2, 1], "label": "Thread", "icon": "mdi:forum",
                 "action": {"type": "keys", "keys": "shift+cmd+t"}},
            ],
        },
        {
            "name": "Calendar",
            "app": "com.apple.iCal",
            "grid": {"cols": 3, "rows": 2},
            "slots": [
                {"pos": [0, 0], "kind": "info", "span": 3, "icon": "mdi:calendar-clock",
                 "label": "{calendar.next}", "caption": "{calendar.next_at}"},
                {"pos": [0, 1], "label": "Oggi", "icon": "mdi:calendar-today",
                 "action": {"type": "keys", "keys": "cmd+t"}},
                {"pos": [1, 1], "label": "Settimana", "icon": "mdi:calendar-week",
                 "action": {"type": "keys", "keys": "cmd+2"}},
                {"pos": [2, 1], "label": "Nuovo", "icon": "mdi:calendar-plus",
                 "action": {"type": "keys", "keys": "cmd+n"}},
            ],
        },
```

Verifica che `escape`, `return`, `shift+tab`, `ctrl+cmd+a` siano nomi accettati da `keymap.py` (`grep -n "escape\|return\|tab" agent/macdeck/keymap.py`); se un nome differisce (es. `esc`), usa quello del keymap. La pagina `Dev` esistente resta la prima e senza `app:`.

- [ ] **Passo 4: `doctor`**

In `agent/macdeck/cli.py` aggiungi, sopra `_doctor`:

```python
CLAUDE_STATUSLINE_SNIPPET = (
    'input=$(cat); mkdir -p ~/.config/macdeck/claude; '
    'printf \'%s\' "$input" > ~/.config/macdeck/claude/'
    '$(echo "$input" | jq -r .session_id).json; # ...poi il resto del comando'
)


def claude_bridge_status(root: Path | None) -> tuple[bool, str]:
    """Se la statusLine di Claude Code sta lasciando i suoi file."""
    from .sources import CLAUDE_STALE_S, newest_claude_file
    f = newest_claude_file(paths.claude_dir(root))
    if f is None:
        return False, (
            "ponte Claude Code assente: nessun file in ~/.config/macdeck/claude.\n"
            "       Aggiungi in testa al comando statusLine di ~/.claude/settings.json:\n"
            f"       {CLAUDE_STATUSLINE_SNIPPET}"
        )
    eta = time.time() - f.stat().st_mtime
    if eta > CLAUDE_STALE_S:
        return False, f"ponte Claude Code: ultimo file {f.name} di {eta / 3600:.1f} ore fa (nessuna sessione viva)"
    return True, f"ponte Claude Code: {f.name} aggiornato {int(eta)} s fa"


def pagine_con_app_assente(layout: dict) -> list[tuple[str, str]]:
    """Pagine con `app:` che non corrisponde a nessuna app installata."""
    fuori = []
    for p in layout["pages"]:
        for a in p.get("app") or []:
            if icons.locate_bundle(a) is None:
                fuori.append((p["name"], a))
    return fuori
```

(Assicurati che `time`, `Path`, `paths` e `icons` siano importati in `cli.py`.) In `_doctor`, dopo il blocco `layout.yaml`:

```python
        for nome, app in pagine_con_app_assente(store.layout):
            print(f"  --   pagina {nome!r}: app {app!r} non trovata, la pagina non comparira'")

    ok_ponte, msg = claude_bridge_status(args.root)
    print(f"  {'OK' if ok_ponte else '--'}   {msg}")
```

- [ ] **Passo 5: esegui**

Run: `cd agent && .venv/bin/python -m pytest`
Atteso: PASS

- [ ] **Passo 6: README**

In `README.md`:

1. Nel paragrafo introduttivo, dopo «mostra in tempo reale volume, brano in riproduzione e stato del sistema», aggiungi: «Quando davanti c'è Spotify, Mail, Slack, Calendar o un terminale con Claude Code, il deck **salta da solo alla pagina di quell'app**: comandi e dati vivi (brano, non lette, modello e contesto rimanente).»
2. In «Configurazione», dopo l'esempio YAML, aggiungi una sottosezione:

```markdown
### Pagine per app

Una pagina con `app:` compare, e il deck ci salta, quando quell'app è in primo
piano. Con lo swipe si torna alla griglia; al prossimo cambio di app si risalta.

```yaml
  - name: Spotify
    app: com.spotify.client          # nome, bundle id o percorso; anche una lista
    grid: {cols: 3, rows: 2}
    slots:
      - pos: [0, 0]
        kind: info                     # tile informativa: valore grande, didascalia
        label: "{media.title}"         # i segnaposto leggono /state
        caption: "{media.artist}"
        span: 3                        # occupa tre colonne
      - pos: [1, 1]
        label: Play / Pausa
        icon: "mdi:play-pause"
        action: {type: media, op: play_pause}
```

Le chiavi disponibili per i segnaposto (`{mail.unread}`, `{claude.model}`,
`{claude.remaining|int}`, `{calendar.next}`, `{slack.badge}`…) le elenca la GUI
nel menu «Inserisci valore». `app:` e `when:` si combinano: la pagina Claude Code
ha `app: [com.googlecode.iterm2, com.apple.Terminal]` e `when: claude.alive`.

Nomi che non coincidono e che conviene sapere: iTerm è `iTerm2` come processo,
`iTerm` come nome visibile, `com.googlecode.iterm2` come bundle. Il Terminale è
`Terminal` / `com.apple.Terminal`. Calendar è `com.apple.iCal`. Slack è
`com.tinyspeck.slackmacgap`.

### Il ponte con Claude Code

Modello, contesto rimanente e cartella li conosce solo Claude Code, che li passa
alla tua `statusLine`. Aggiungi in testa al comando in `~/.claude/settings.json`:

```sh
input=$(cat); mkdir -p ~/.config/macdeck/claude; printf '%s' "$input" > ~/.config/macdeck/claude/$(echo "$input" | jq -r .session_id).json; # ...poi il resto
```

`macdeck doctor` dice se i file arrivano. Con più sessioni aperte il deck mostra
l'ultima con cui hai parlato.
```

3. In «Limiti dichiarati» aggiungi: «**Il cambio pagina segue il polling:** da 1 a 4 s fra il cambio app e il display. Non c'è push, per scelta.» e «**Il badge di Slack** si legge dal Dock via Accessibilità: se non c'è o non è leggibile, la tile mostra la sola didascalia.»
4. Aggiorna il conteggio dei test in «Test» con il numero reale (`pytest -q | tail -1`).
5. In «Documenti» aggiungi la spec e il piano di oggi.

- [ ] **Passo 7: commit**

```bash
git add agent/macdeck/layout.py agent/macdeck/cli.py agent/tests/test_layout.py agent/tests/test_cli.py README.md
git commit -m "Cinque pagine per app nel layout di default, doctor controlla il ponte con Claude Code"
```

---

### Task 9: web UI — pagine con app, tile informative, inserisci valore

**Files:**
- Modify: `agent/macdeck/web/index.html`

**Interfacce:**
- Consuma: `/api/config` → `state_keys`; `/api/icons` → `apps[].bundle`; `/api/tile-preview` con `kind`, `caption`, `span`.
- Nessun test automatico: la pagina non ha una suite. Verifica manuale al Passo 6.

- [ ] **Passo 1: `toRaw` non deve perdere niente**

Sostituisci `toRaw` con:

```js
function toRaw(res){
  return {
    schema:1, grid:res.grid, theme:res.theme,
    pages: res.pages.map(p=>{
      const pg={name:p.name, grid:p.grid};
      if(p.app && p.app.length) pg.app=p.app;      // oggi si perdeva anche when:
      if(p.when) pg.when=p.when;
      pg.slots = p.slots.map(s=>{
        const o={pos:s.pos, label:s.label};
        if(s.icon) o.icon=s.icon;
        if(s.kind && s.kind!=='button') o.kind=s.kind;
        if(s.caption) o.caption=s.caption;
        if(s.span && s.span>1) o.span=s.span;
        if(s.action) o.action=s.action;
        if(s.color) o.color=s.color;
        if(s.state) o.state=s.state;
        if(s.when) o.when=s.when;
        if(s.timeout_ms) o.timeout_ms=s.timeout_ms;
        return o;
      });
      return pg;
    })
  };
}
```

- [ ] **Passo 2: pannello Pagina con App e `when:`**

Nel `<div class="card">` di «Pagina», dopo il `.hint` sulle 12 caselle e prima di `.actions`:

```html
      <label>App (opzionale)</label>
      <div class="row"><input id="pgApp" placeholder="com.spotify.client, iTerm2">
        <button class="ghost" id="pgAppPick" style="flex:0 0 auto">Scegli app…</button></div>
      <div class="picker" id="pgAppBox" style="display:none"></div>
      <div class="hint">La pagina compare, e il deck ci salta, quando una di queste app è
        in primo piano. Nome, bundle id o percorso; più voci separate da virgola.
        Vuoto = pagina base, sempre visibile.</div>
      <label>Mostra solo se (opzionale)</label>
      <input id="pgWhen" placeholder="claude.alive">
```

Nel blocco `// ---- pagine` in fondo allo script:

```js
document.getElementById('pgApp').oninput=e=>{
  const v=e.target.value.split(',').map(s=>s.trim()).filter(Boolean);
  if(v.length) curPage().app=v; else delete curPage().app;
  setDirty(true); render();
};
document.getElementById('pgWhen').oninput=e=>{
  const v=e.target.value.trim();
  if(v) curPage().when=v; else delete curPage().when;
  setDirty(true);
};
document.getElementById('pgAppPick').onclick=()=>{
  const box=document.getElementById('pgAppBox');
  if(box.style.display!=='none'){ box.style.display='none'; return; }
  box.style.display='block';
  mostraElencoApp(box, scelta=>{
    // Il bundle id e' il nome piu' stabile: non cambia con la lingua del Mac
    // e non dipende da dove sta il bundle.
    const id = scelta.bundle || scelta.path;
    const p=curPage(); p.app=[...(p.app||[]), id];
    box.style.display='none'; setDirty(true); render();
  });
};
```

In `render()`, dove si riempiono `pgName`/`pgCols`/`pgRows`, aggiungi:

```js
  document.getElementById('pgApp').value=(pg.app||[]).join(', ');
  document.getElementById('pgWhen').value=pg.when||'';
```

- [ ] **Passo 3: icona dell'app sulle schede**

Nel ciclo delle schede in `render()`, sostituisci `t.textContent=pg.name;` con:

```js
    if(pg.app && pg.app.length){
      const im=document.createElement('img');
      im.width=14; im.height=14; im.style.cssText='vertical-align:-2px;margin-right:5px';
      im.src='/api/icon-preview?spec='+encodeURIComponent('app:'+pg.app[0])+'&size=28';
      t.appendChild(im);
    }
    t.appendChild(document.createTextNode(pg.name));
```

- [ ] **Passo 4: pannello Slot — Pulsante / Informativa, caption, span, inserisci valore**

In `renderSlotPanel`, nel template `body.innerHTML=barra+\`...\``, sostituisci la prima `.row` (Etichetta + Colore) con:

```html
    <div class="row">
      <div><label>Tipo di tile</label>
        <select id="fKind"><option value="button">Pulsante</option>
          <option value="info">Informativa</option></select></div>
      <div><label>Colore tile</label>
        <div class="row"><input id="fColor" type="color" style="padding:2px">
        <button class="ghost" id="fColorNo">Tema</button></div></div>
    </div>
    <label id="lLabel">Etichetta</label>
    <div class="row"><input id="fLabel">
      <select id="fIns" style="flex:0 0 auto"><option value="">Inserisci valore…</option></select></div>
    <div class="hint" id="hKeys"></div>
    <div id="fInfoOnly">
      <label>Didascalia</label>
      <input id="fCaption" placeholder="{media.artist}">
      <div class="row">
        <div><label>Colonne occupate</label><input id="fSpan" type="number" min="1" max="12"></div>
      </div>
      <div class="hint">Una tile informativa mostra il valore in grande e la didascalia sotto.
        I segnaposto <code>{chiave}</code> leggono <code>/state</code>; <code>{chiave|int}</code>
        toglie i decimali. L'azione è facoltativa.</div>
    </div>
```

e sostituisci `<select id="fType"></select>` con lo stesso `select` ma popolato anche con l'opzione «nessuna» quando la tile è informativa (vedi sotto). Dopo `q('fColor').value=...` aggiungi:

```js
  q('fKind').value=s.kind||'button';
  q('fInfoOnly').style.display = (s.kind==='info') ? '' : 'none';
  q('lLabel').textContent = (s.kind==='info') ? 'Valore' : 'Etichetta';
  q('fCaption').value=s.caption||'';
  q('fSpan').value=s.span||1;
  q('fKind').onchange=e=>{
    s.kind=e.target.value;
    if(s.kind==='button'){ delete s.caption; delete s.span; if(!s.action) s.action={type:'noop'}; }
    setDirty(true); render();
  };
  q('fCaption').oninput=e=>{s.caption=e.target.value||undefined;setDirty(true);refreshTile();avvisaChiavi();};
  q('fSpan').onchange=e=>{
    const n=Math.max(1, Number(e.target.value)||1);
    if(s.pos[0]+n>g.cols){ banner('bErr',`Da colonna ${s.pos[0]} uno span di ${n} esce dalla griglia.`); e.target.value=s.span||1; return; }
    banner('bErr',''); s.span=n; setDirty(true); render();
  };

  // Le chiavi le conosce il registro delle sonde, non questa pagina: una
  // sorgente nuova compare qui da sola.
  const ins=q('fIns');
  (cfg.state_keys||[]).forEach(k=>{
    const o=document.createElement('option'); o.value=k; o.textContent=k; ins.appendChild(o);
  });
  ins.onchange=()=>{
    if(!ins.value) return;
    const inp=q('fLabel'), pos=inp.selectionStart??inp.value.length;
    inp.value=inp.value.slice(0,pos)+'{'+ins.value+'}'+inp.value.slice(pos);
    ins.value='';
    inp.dispatchEvent(new Event('input'));
  };

  function avvisaChiavi(){
    const note=new Set(cfg.state_keys||[]);
    const usate=[...((s.label||'')+' '+(s.caption||'')).matchAll(/\{([A-Za-z0-9_.]+)(\|int)?\}/g)].map(m=>m[1]);
    const ignote=usate.filter(k=>!note.has(k));
    q('hKeys').innerHTML = ignote.length
      ? '<span style="color:#e2b93b">Chiave sconosciuta: <code>'+ignote.join('</code>, <code>')+'</code>. '+
        'Non è un errore, ma resterà vuota.</span>' : '';
  }
  avvisaChiavi();
```

Modifica l'handler dell'etichetta in `q('fLabel').oninput=e=>{s.label=e.target.value;setDirty(true);refreshTile();avvisaChiavi();};`.

Per l'azione facoltativa, nel popolamento di `sel2`:

```js
  const sel2=q('fType');
  if((s.kind||'button')==='info'){
    const o=document.createElement('option'); o.value=''; o.textContent='— nessuna —';
    if(!s.action) o.selected=true; sel2.appendChild(o);
  }
  (cfg.action_types||[]).forEach(t=>{ /* come oggi */ });
  sel2.onchange=()=>{
    if(sel2.value==='') delete s.action; else s.action={type:sel2.value};
    setDirty(true); renderSlotPanel();
  };
  if(s.action) renderFields(q('fFields'), s.action); else q('fFields').innerHTML='';
  q('bTest').style.display = s.action ? '' : 'none';
```

`bTest` e `bDel` restano; il testo di `bDel` diventa «Elimina tile».

- [ ] **Passo 5: ordine delle varianti e drop nativo**

`slotsAt` e `moveSlot` funzionano senza modifiche perché lo slot resta identificato da `pos`. In `ponte.onDrop`, il pulsante creato ha `kind` assente (pulsante): nessun cambio.

- [ ] **Passo 6: verifica manuale**

```bash
cd agent && .venv/bin/python -m macdeck.cli serve
```

Apri `http://127.0.0.1:8765` e verifica:
1. le schede Spotify, Mail, Claude Code, Slack, Calendar hanno l'icona dell'app (se il tuo `layout.yaml` non le ha, prova con `mv ~/.config/macdeck/layout.yaml /tmp/` e riavvia: si rigenera dal default; poi rimettilo);
2. cliccando la tile grande di Spotify il pannello dice «Informativa», mostra Didascalia e Colonne occupate = 3, e l'azione è «— nessuna —»;
3. «Inserisci valore…» elenca `media.title`, `mail.unread`, `claude.model`… e inserisce `{…}` nel campo;
4. scrivere `{boh.x}` nel valore mostra l'avviso giallo;
5. con Spotify aperto e in riproduzione l'anteprima della tile mostra il brano vero;
6. salvare e ricaricare mantiene `app:` e `when:` della pagina (controlla `~/.config/macdeck/layout.yaml`).

- [ ] **Passo 7: commit**

```bash
git add agent/macdeck/web/index.html
git commit -m "Editor: pagine con app, tile informative, inserisci valore, e when: non si perde piu' al salvataggio"
```

---

### Task 10: note tecniche e verifica end‑to‑end

**Files:**
- Modify: `NOTE-TECNICHE.md`, `README.md` (conteggio test)

- [ ] **Passo 1: note tecniche**

In `NOTE-TECNICHE.md`, sezione «Agent», aggiungi prima di «Comandi utili»:

```markdown
### L'app davanti si legge con `lsappinfo`, non con System Events

`tell application "System Events" to get name of first process whose frontmost
is true` costa ~300 ms e passa dal permesso Accessibilità. `lsappinfo front` +
`lsappinfo info -only name,bundleid,executablepath <ASN>` costa ~10 ms e non
chiede nulla. Restituisce tre nomi che **non coincidono**: eseguibile
(`iTerm2`), nome visibile (`iTerm`, ma `Calendario` su un Mac in italiano),
bundle id (`com.googlecode.iterm2`). `app:` nel layout accetta uno qualunque
dei tre; per il gate «app in esecuzione» delle sonde si usa il bundle id, che
non dipende dalla lingua.

### Una sonda non deve mai aprire un'app

`tell application "Mail" to …` **lancia Mail** se è chiuso. Per questo le sonde
dichiarano `app=` e `StateProbe` le esegue solo se `lsappinfo list` riporta
quell'app. `media` non lo dichiara perché il suo script controlla da sé
l'elenco dei processi prima di parlare al player.

### Il server sceglie la pagina, il firmware non lo sa

Il campo `page` di `/layout` era già autoritativo (il display lo adotta quando
la pagina corrente sparisce). Per far saltare il deck alla pagina dell'app
davanti non serve altro: il server ricorda l'app davanti all'ultimo `/layout`
servito e, se è cambiata, risponde `0`. Se non è cambiata, clampa e basta —
altrimenti un brano nuovo riporterebbe alla pagina Spotify chi era andato
sulla griglia. Il ricordo si aggiorna **solo** in `/layout`: `/screen` e
`/press` non lo toccano.

### I segnaposto si risolvono in `_resolve()`, non nel renderer

`{media.title}` diventa testo prima del rendering. Così il renderer non
conosce lo stato, la cache delle tile funziona per chiave d'etichetta come
prima, e la firma del layout — che è già l'impronta del risultato risolto —
cambia da sola quando cambia un valore. Nessun codice per «invalidare quando
cambia il brano»: è gratis per costruzione.

### `toRaw` nella GUI perdeva `when:` di pagina

La funzione che riconverte il layout risolto in YAML da salvare elencava i
campi a mano e non includeva `when` sulle pagine: salvare dalla GUI cancellava
la condizione. Ora elenca anche `app`, `when`, `kind`, `caption`, `span`, e
omette `action` se manca. Regola: ogni campo nuovo di `validate()` va aggiunto
anche lì, o la GUI lo cancella in silenzio.
```

- [ ] **Passo 2: suite completa e conteggio**

Run: `cd agent && .venv/bin/python -m pytest -q | tail -3`
Atteso: tutti PASS. Riporta il numero in `README.md` («Test») al posto di «284 test».

- [ ] **Passo 3: verifica end‑to‑end sul deck (manuale)**

Con l'agent in esecuzione (`macdeck serve` o il LaunchAgent riavviato: `launchctl kickstart -k gui/$(id -u)/it.macdeck.agent` — controlla il label con `grep -A1 Label ~/Library/LaunchAgents/*macdeck*`):

1. Porta Spotify davanti → entro 4 s il deck mostra brano/artista e ⏮ ⏯ ⏭.
2. Swipe → griglia. Cambia brano da Spotify → il deck resta sulla griglia, ma tornando con lo swipe alla pagina Spotify il titolo è aggiornato.
3. Porta Mail davanti → salto alla pagina Mail con il numero di non lette.
4. iTerm davanti **senza** Claude Code → griglia. Avvia `claude` in iTerm (con la riga aggiunta alla statusLine) → dopo il primo messaggio la pagina Claude Code compare con modello e percentuale.
5. `macdeck doctor` → riga «OK ponte Claude Code».

Annota nel commit finale eventuali scorciatoie di default che non funzionano e correggile nel `DEFAULT_LAYOUT`.

- [ ] **Passo 4: commit**

```bash
git add NOTE-TECNICHE.md README.md
git commit -m "Note tecniche: lsappinfo, sonde che non aprono app, il server sceglie la pagina"
```

---

## Autoverifica del piano rispetto alla spec

| sezione della spec | task |
|---|---|
| §3 registro sonde, gate `app=`, cadenza, tre fallimenti, `empty` | 1 |
| §3 sorgenti `front`, `mail`, `slack`, `calendar`, `claude` | 2, 3, 4 |
| §4 `app:` (stringa/lista, tre nomi, AND con `when:`), segnaposto, `|int`, `kind`, `caption`, `span`, azione facoltativa, validazione | 5 |
| §4 layout di default con cinque pagine | 8 |
| §5 ponte Claude Code (file, più recente, `alive`, purge, branch, `doctor`, README) | 4, 8 |
| §6 ordine del mazzo, salto a 0, info senza azione fuori da `slots`, `/press` 404 | 7 |
| §7 rendering info, cache, anteprima con segnaposto e span | 6, 7 |
| §8 web UI (App, when, Pulsante/Informativa, inserisci valore, avviso, icone schede) | 9 |
| §9 casi limite (app non installata → `doctor`; fallimenti; segnaposto ignoti) | 8, 1, 9 |
| §10 test | ogni task; end‑to‑end in 10 |
| §11-12 note e rischi documentati | 8, 10 |

Nomi condivisi fra task, da tenere allineati: `sources.source / Source / ProbeContext / REGISTRY / known_keys / empty_snapshot / running_apps / newest_claude_file / CLAUDE_STALE_S / CLAUDE_PURGE_S`; `state.fill / placeholders / value_at / StateProbe(refresh(due_only))`; `layout.normalize_app / app_matches / span_box`; `render.render_info_tile / render_button_tile / render_tile`; `paths.claude_dir`; `cli.claude_bridge_status / pagine_con_app_assente / CLAUDE_STATUSLINE_SNIPPET`.
