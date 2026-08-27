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

import hmac
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import actions, icons
from . import layout as L
from .executor import Executor
from .layout import LayoutStore
from .render import TileCache
from .state import StateProbe

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

    def _page(index: int) -> dict:
        pages = store.layout["pages"]
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
        p = _page(page)
        theme = store.layout["theme"]
        return {
            "version": store.version,
            "page": page,
            "pages": [q["name"] for q in store.layout["pages"]],
            "grid": p["grid"],
            "background": theme["background"],
            "accent": theme["accent"],
            "slots": [
                {
                    "i": s["index"],
                    "x": s["box"]["x"],
                    "y": s["box"]["y"],
                    "w": s["box"]["w"],
                    "h": s["box"]["h"],
                    "url": f"/tile/{page}/{s['index']}.png?v={store.version}",
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
            "layout_version": store.version,
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
        return {
            "apps": apps[:120],
            "action_types": sorted(actions.known_types()),
            "mdi_available": icons.mdi_available(root),
        }

    return app
