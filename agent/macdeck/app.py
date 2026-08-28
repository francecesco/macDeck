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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import actions, icons, render
from . import layout as L
from .executor import Executor
from .layout import LayoutStore
from .render import TileCache
from .state import StateProbe, value_at

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

    def _visible_pages() -> list[dict]:
        """Le pagine effettivamente mostrate adesso.

        Una pagina con `when:` compare solo se quel percorso dentro /state e'
        veritiero: e' cosi' che i comandi multimediali appaiono soltanto
        quando c'e' davvero un player in esecuzione. Se il filtro non lascia
        nulla si mostra tutto, perche' un deck vuoto e' peggio di un deck
        con una pagina di troppo.
        """
        stato = probe.snapshot()
        pages = store.layout["pages"]
        visibili = [
            p for p in pages
            if not p.get("when") or bool(value_at(stato, p["when"]))
        ]
        return visibili or pages

    def _effective_version(visibili: list[dict]) -> int:
        """Cambia sia quando cambia il layout sia quando cambia cosa e' visibile.

        Senza il secondo pezzo, l'apparire della pagina Media non farebbe
        ricaricare il display.

        NON si usa hash() di Python: il suo hash delle stringhe e'
        randomizzato a ogni processo, quindi la versione cambierebbe a ogni
        riavvio dell'agent anche a layout identico — esattamente il difetto
        che content_version() serviva a togliere.
        """
        nomi = "|".join(p["name"] for p in visibili)
        marchio = int(
            hashlib.sha256(nomi.encode()).hexdigest()[:8], 16
        )
        return (store.version ^ marchio) & 0x7FFFFFFF

    def _page(index: int) -> dict:
        pages = _visible_pages()
        if not 0 <= index < len(pages):
            raise HTTPException(status_code=404, detail=f"pagina {index} inesistente")
        return pages[index]

    def _slot(page_index: int, slot_index: int) -> dict:
        page = _page(page_index)
        for slot in page["slots"]:
            if slot["index"] == slot_index:
                return slot
        raise HTTPException(
            status_code=404,
            detail=f"slot {slot_index} vuoto nella pagina {page_index}",
        )

    # ------------------------------------------------ endpoint del display

    @app.get("/layout", dependencies=[Depends(require_token)])
    def get_layout(page: int = 0) -> dict:
        visibili = _visible_pages()
        p = _page(page)
        theme = store.layout["theme"]
        version = _effective_version(visibili)
        return {
            "version": version,
            "page": page,
            "pages": [q["name"] for q in visibili],
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
        visibili = _visible_pages()
        p = _page(page)
        key = (page, _effective_version(visibili))
        png = screens.get(key)
        if png is None:
            png = render.screen_png(
                p, store.layout["theme"],
                page_index=page, page_count=len(visibili),
                root=root,
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
            "layout_version": _effective_version(_visible_pages()),
            "layout_error": store.error,
        }

    # ------------------------------------------------ endpoint del browser

    @app.get("/", dependencies=[Depends(require_local)], response_class=HTMLResponse)
    def index() -> HTMLResponse:
        page = WEB_DIR / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>MacDeck</h1><p>web UI non installata</p>")
        return HTMLResponse(page.read_text())

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

    @app.get("/api/icons", dependencies=[Depends(require_local)])
    def list_icons(q: str = "") -> dict:
        needle = q.lower()
        apps = []
        seen: set[str] = set()
        for folder in icons.app_dirs():
            for bundle in sorted(folder.glob("*.app")):
                name = bundle.stem
                if name in seen:
                    continue
                if needle and needle not in name.lower():
                    continue
                seen.add(name)
                apps.append({"name": name, "icon": f"app:{bundle}"})
        mdi = [n for n in icons.mdi_names(root) if not needle or needle in n]
        return {
            "apps": apps[:120],
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
        slot["box"] = boxes.get(index) or next(iter(boxes.values()))
        theme = {**store.layout["theme"], **(body.get("theme") or {})}
        return Response(
            content=render.tile_png(slot, theme, root=root),
            media_type="image/png",
        )

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
