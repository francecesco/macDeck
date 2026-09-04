"""API HTTP dell'agent.

Due regole di accesso, per motivi diversi:

- gli endpoint del display girano su 0.0.0.0 (il display e' un altro
  dispositivo) e richiedono X-Deck-Token;
- gli endpoint /api/* e la web UI accettano solo connessioni di loopback,
  perche' /api/test esegue azioni arbitrarie e non ha nemmeno il token.

L'ascolto e' su un solo socket e la restrizione di loopback e' applicata da
una dipendenza invece che dal bind: la garanzia e' la stessa e il processo
resta uno.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import actions, icons, keymap, render
from . import layout as L
from . import sources
from .executor import Executor
from .layout import LayoutStore
from .render import TileCache
from .state import StateProbe, fill, value_at

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

WEB_DIR = Path(__file__).parent / "web"


def create_app(
    *,
    store: LayoutStore,
    cache: TileCache,
    probe: StateProbe,
    executor: Executor,
    token: str,
    root: Path | None = None,
    trust_loopback_header: bool = False,
) -> FastAPI:
    app = FastAPI(title="MacDeck", docs_url=None, redoc_url=None)
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="macdeck")
    screens: dict[tuple[int, int], bytes] = {}   # (pagina, versione) -> PNG

    def require_token(x_deck_token: str | None = Header(default=None)) -> None:
        if not x_deck_token or not hmac.compare_digest(x_deck_token, token):
            raise HTTPException(status_code=401, detail="token assente o errato")

    def require_local(request: Request) -> None:
        host = request.client.host if request.client else ""
        if host in LOOPBACK:
            return
        if trust_loopback_header and request.headers.get("X-Forwarded-Loopback"):
            return
        raise HTTPException(status_code=403, detail="solo da localhost")

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

    # Le icone non stanno nel layout: vivono sul disco, dentro i bundle delle
    # app. Se cambia un'icona — o cambia il modo in cui la risolviamo — il
    # layout resta identico e il display non riscarica nulla. Questo contatore
    # entra nella firma apposta per poterglielo dire.
    nudge = {"n": 0}

    def _signature(risolte: list[dict]) -> int:
        """Versione = impronta di CIO' CHE IL DISPLAY RICEVEREBBE.

        Non del file, non dell'elenco delle pagine: del risultato risolto.
        Cosi' qualunque cosa cambi l'aspetto del deck — un layout salvato, una
        pagina che compare, uno slot condizionale che si attiva — cambia la
        versione, senza doverci pensare caso per caso.

        Niente hash() di Python: e' randomizzato per processo e la versione
        cambierebbe a ogni riavvio dell'agent a parita' di contenuto.
        """
        payload = json.dumps([risolte, nudge["n"]],
                             sort_keys=True, ensure_ascii=False)
        return int(hashlib.sha256(payload.encode()).hexdigest()[:8], 16)

    def _page_index(requested: int, risolte: list[dict]) -> int:
        """Riporta l'indice richiesto dentro l'intervallo valido.

        Non e' permissivita': e' il protocollo. La pagina corrente del display
        diventa invalida ogni volta che l'insieme delle pagine visibili si
        restringe, e con `when:` succede di continuo. Rispondere 404 lasciava
        il display bloccato per sempre su una pagina sparita, perche' non
        aveva modo di sapere dove andare. Il server lo riporta in carreggiata
        e glielo dice nel campo "page" della risposta.
        """
        if not risolte:
            raise HTTPException(status_code=503, detail="nessuna pagina")
        return max(0, min(requested, len(risolte) - 1))

    def _slot(page_index: int, slot_index: int) -> dict:
        risolte = _resolve()
        page = risolte[_page_index(page_index, risolte)]
        for slot in page["slots"]:
            if slot["index"] == slot_index:
                if slot.get("action") is None:
                    break             # tile informativa: niente da premere
                return slot
        raise HTTPException(
            status_code=404,
            detail=f"slot {slot_index} vuoto nella pagina {page_index}",
        )

    # ------------------------------------------------ endpoint del display

    @app.get("/layout", dependencies=[Depends(require_token)])
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
        theme = store.layout["theme"]
        version = _signature(risolte)
        return {
            "version": version,
            # Con una pagina sola il firmware deve nascondere le frecce.
            # Autoritativo: il display DEVE adottare questo valore, perche'
            # puo' differire da quello che ha chiesto.
            "page": page,
            "pages": [q["name"] for q in risolte],
            "grid": p["grid"],
            "background": theme["background"],
            "accent": theme["accent"],
            "screen": f"/screen/{page}.png?v={version}",
            "slots": [
                {
                    "i": s["index"],
                    "x": s["box"]["x"],
                    "y": s["box"]["y"],
                    "w": s["box"]["w"],
                    "h": s["box"]["h"],
                    "url": f"/tile/{page}/{s['index']}.png?v={version}",
                    "state": s.get("state"),
                }
                for s in p["slots"]
                if s.get("action") is not None
            ],
        }

    @app.get("/tile/{page}/{slot}.png", dependencies=[Depends(require_token)])
    def get_tile(page: int, slot: int) -> Response:
        s = _slot(page, slot)
        png = cache.png(s, store.layout["theme"], root=root)
        return Response(content=png, media_type="image/png")

    @app.get("/screen/{page}.png", dependencies=[Depends(require_token)])
    def get_screen(page: int) -> Response:
        """L'intera schermata come un unico PNG.

        E' cio' che il firmware scarica davvero: una richiesta invece di
        dodici. Vedi render.render_screen per il perche'.
        """
        risolte = _resolve()
        page = _page_index(page, risolte)
        p = risolte[page]
        key = (page, _signature(risolte))
        png = screens.get(key)
        if png is None:
            png = render.screen_png(
                p, store.layout["theme"], root=root,
            )
            screens.clear()          # una sola versione per volta in memoria
            screens[key] = png
        return Response(content=png, media_type="image/png")

    @app.post("/press", dependencies=[Depends(require_token)])
    def press(body: dict) -> JSONResponse:
        slot = _slot(int(body.get("page", 0)), int(body.get("slot", 0)))
        spec = slot["action"]
        if actions.is_async(spec):
            future = pool.submit(actions.run, spec, executor)

            def _report(f) -> None:
                r = f.result()
                probe.note_error(
                    None if r.ok else f"{spec.get('type')}: {r.error}"
                )

            future.add_done_callback(_report)
            return JSONResponse({"ok": True, "accepted": True, "error": None})
        result = pool.submit(actions.run, spec, executor).result()
        if not result.ok:
            probe.note_error(f"{spec.get('type')}: {result.error}")
        return JSONResponse({"ok": result.ok, "error": result.error})

    @app.get("/state", dependencies=[Depends(require_token)])
    def get_state() -> dict:
        return {
            **probe.snapshot(),
            "layout_version": _signature(_resolve()),
            "layout_error": store.error,
        }

    # ------------------------------------------------ endpoint del browser

    @app.get("/", dependencies=[Depends(require_local)], response_class=HTMLResponse)
    def index() -> HTMLResponse:
        page = WEB_DIR / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>MacDeck</h1><p>web UI non installata</p>")
        return HTMLResponse(page.read_text())

    @app.get("/api/health", dependencies=[Depends(require_local)])
    def health(request: Request) -> dict:
        """Cosa l'app nativa deve poter dire quando la pagina non puo'.

        Nessuna ricerca del deck qui dentro: si riporta solo quello che
        l'Announcer ha gia' trovato per conto suo. Una seconda opinione
        sulla presenza del deck sarebbe peggio di nessuna.
        """
        ann = getattr(request.app.state, "announcer", None)
        s = ann.status() if ann is not None else None
        giro = (s or {}).get("ultimo_giro") or 0.0
        return {
            "deck": (s or {}).get("deck"),
            "announced": (s or {}).get("annunciato"),
            "error": (s or {}).get("ultimo_errore"),
            # secondi dall'ultimo giro, None se non ne ha ancora fatti
            "last_round": (time.time() - giro) if giro else None,
            "accessibility_ok": probe.snapshot().get("accessibility_ok"),
        }

    @app.get("/api/config", dependencies=[Depends(require_local)])
    def read_config() -> dict:
        raw: dict = {}
        if store.path.exists():
            try:
                raw = yaml.safe_load(store.path.read_text()) or {}
            except yaml.YAMLError:
                raw = {}
        return {
            "raw": raw,
            "resolved": store.layout,
            "version": store.version,
            "error": store.error,
            "action_types": sorted(actions.known_types()),
            "state_keys": sorted(sources.known_keys() + ["accessibility_ok"]),
            # Lecito qui: questo endpoint e' gia' solo-loopback. Serve alla
            # web UI per interrogare /state, che richiede il token.
            "token": token,
        }

    @app.put("/api/config", dependencies=[Depends(require_local)])
    def write_config(body: dict) -> dict:
        try:
            store.save(body)
        except L.LayoutError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        cache.clear()
        screens.clear()
        return {"ok": True, "version": store.version}

    @app.post("/api/test", dependencies=[Depends(require_local)])
    def test_action(body: dict) -> dict:
        result = pool.submit(actions.run, body, executor).result()
        return {"ok": result.ok, "error": result.error, "out": result.out}

    @app.post("/api/refresh", dependencies=[Depends(require_local)])
    def force_refresh() -> dict:
        """Costringe il display a ridisegnare, a layout invariato."""
        nudge["n"] += 1
        cache.clear()
        icons.reset_display_names()
        return {"ok": True}

    @app.get("/api/icons", dependencies=[Depends(require_local)])
    def list_icons(q: str = "") -> dict:
        needle = q.lower()
        apps = []
        seen: set[str] = set()
        # Il nome che il Finder mostra e quello del bundle sul disco spesso
        # non coincidono ("Anteprima" contro "Preview"): si cerca su
        # entrambi, e si mostra quello che l'utente riconosce.
        mostrati = icons.display_names()
        for folder in icons.app_dirs():
            for bundle in sorted(folder.glob("*.app")):
                disco = bundle.stem
                if disco in seen:
                    continue
                nome = mostrati.get(str(bundle), disco)
                if needle and needle not in nome.lower() \
                        and needle not in disco.lower():
                    continue
                seen.add(disco)
                apps.append({"name": nome, "path": str(bundle),
                             "disk": disco, "icon": f"app:{bundle}",
                             "bundle": icons.bundle_identifier(bundle)})
        apps.sort(key=lambda a: a["name"].lower())
        mdi = [n for n in icons.mdi_names(root) if not needle or needle in n]
        return {
            # Le app NON si tagliano: il selettore scarica l'elenco intero
            # e filtra nel browser, quindi un taglio renderebbe invisibili
            # quelle in fondo all'alfabeto — Terminale spariva, iTerm no.
            # Sono qualche centinaio, e il JSON viaggia su loopback.
            "apps": apps,
            # I glifi MDI sono settemila: quelli si tagliano davvero.
            "mdi": mdi[:120],
            "mdi_total": len(mdi),
            "action_types": sorted(actions.known_types()),
            "mdi_available": icons.mdi_available(root),
        }

    @app.post("/api/tile-preview", dependencies=[Depends(require_local)])
    def tile_preview(body: dict) -> Response:
        """Rende una tile NON ancora salvata, esattamente come apparira'.

        E' cio' che rende la web UI davvero WYSIWYG: l'anteprima passa dallo
        stesso renderer del display, non da un'approssimazione in CSS.
        """
        try:
            grid = L.normalize_grid(body.get("grid") or store.layout["grid"])
        except L.LayoutError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
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
        theme = {**store.layout["theme"], **(body.get("theme") or {})}
        return Response(
            content=render.tile_png(slot, theme, root=root),
            media_type="image/png",
        )

    @app.post("/api/keys-canon", dependencies=[Depends(require_local)])
    def keys_canon(body: dict) -> dict:
        """Da un evento di tastiera alla combinazione canonica.

        Il registratore nativo manda il fatto grezzo — key code e
        modificatori — e la traduzione avviene qui, accanto alla tabella.
        L'app non impara i nomi dei tasti.
        """
        try:
            combo = keymap.from_event(
                int(body.get("keyCode", -1)),
                body.get("modifiers") or [],
                body.get("chars") or "",
            )
        except (keymap.InvalidKeys, TypeError, ValueError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {"keys": combo}

    @app.get("/api/icon-preview", dependencies=[Depends(require_local)])
    def icon_preview(spec: str, size: int = 64) -> Response:
        """Rende una qualunque icon spec come PNG.

        Serve al selettore di icone della web UI: senza questo endpoint la
        GUI mostrerebbe nomi di file invece delle icone, e sceglierebbe alla
        cieca.
        """
        import io

        size = max(16, min(size, 256))
        im = icons.resolve(spec, size, root=root)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")

    return app
