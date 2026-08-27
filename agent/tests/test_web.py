import pytest
from fastapi.testclient import TestClient

from macdeck import layout as L
from macdeck.app import WEB_DIR, create_app
from macdeck.render import TileCache
from macdeck.state import StateProbe

HTML = (WEB_DIR / "index.html").read_text()


def test_la_pagina_esiste_ed_e_autosufficiente():
    assert "<title>" in HTML
    # nessuna risorsa esterna: la web UI deve funzionare a Mac offline
    for vietato in ("http://cdn", "https://cdn", "unpkg.com", "jsdelivr",
                    "fonts.googleapis", "<script src"):
        assert vietato not in HTML


def test_la_pagina_usa_gli_endpoint_previsti():
    for endpoint in ("/api/config", "/api/icons", "/api/test",
                     "/api/tile-preview", "/api/icon-preview", "/state"):
        assert endpoint in HTML


def test_la_geometria_nella_gui_combacia_con_quella_del_server():
    # Se qualcuno cambia le costanti in layout.py senza aggiornare la GUI,
    # l'anteprima mente. Questo test lo impedisce.
    assert f"DW={L.DISPLAY_W}" in HTML
    assert f"DH={L.DISPLAY_H}" in HTML
    assert f"HEADER={L.HEADER_H}" in HTML
    assert f"NAVBAR={L.NAVBAR_H}" in HTML
    assert f"GUT={L.GUTTER}" in HTML
    assert f"MAX_SLOTS={L.MAX_SLOTS}" in HTML


@pytest.fixture
def client(tmp_path, fake_ex):
    store = L.LayoutStore(tmp_path / "layout.yaml")
    store.load()
    app = create_app(
        store=store, cache=TileCache(), probe=StateProbe(fake_ex),
        executor=fake_ex, token="t", root=tmp_path,
        trust_loopback_header=True,
    )
    return TestClient(app)


def test_lindex_viene_servito(client):
    r = client.get("/", headers={"X-Forwarded-Loopback": "1"})
    assert r.status_code == 200
    assert "MacDeck" in r.text


def test_lindex_non_e_servito_dalla_rete(client):
    assert client.get("/").status_code == 403
