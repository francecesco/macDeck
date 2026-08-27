import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from macdeck import layout as L
from macdeck.app import create_app
from macdeck.executor import Result as R
from macdeck.render import TileCache
from macdeck.state import StateProbe

TOKEN = "tokendiprova"
AUTH = {"X-Deck-Token": TOKEN}
LOCAL = {"X-Forwarded-Loopback": "1"}


@pytest.fixture
def ctx(tmp_path, fake_ex):
    store = L.LayoutStore(tmp_path / "layout.yaml")
    store.load()
    probe = StateProbe(fake_ex, ttl=0.0)
    app = create_app(
        store=store, cache=TileCache(), probe=probe,
        executor=fake_ex, token=TOKEN, root=tmp_path,
        trust_loopback_header=True,
    )
    return TestClient(app), store, fake_ex, probe


def test_layout_richiede_il_token(ctx):
    client, *_ = ctx
    assert client.get("/layout").status_code == 401
    assert client.get("/layout", headers={"X-Deck-Token": "sbagliato"}).status_code == 401
    assert client.get("/layout", headers=AUTH).status_code == 200


def test_layout_espone_geometria_e_url_per_ogni_slot(ctx):
    client, store, *_ = ctx
    body = client.get("/layout", headers=AUTH).json()
    assert body["version"] == store.version
    assert body["page"] == 0
    assert body["pages"] == ["Home"]
    slot = body["slots"][0]
    assert {"i", "x", "y", "w", "h", "url"} <= set(slot)
    assert slot["url"].startswith("/tile/0/")


def test_layout_pagina_inesistente(ctx):
    client, *_ = ctx
    assert client.get("/layout?page=99", headers=AUTH).status_code == 404


def test_tile_restituisce_un_png_della_dimensione_giusta(ctx):
    client, *_ = ctx
    r = client.get("/tile/0/0.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (115, 80)


def test_tile_di_uno_slot_vuoto(ctx):
    client, *_ = ctx
    assert client.get("/tile/0/7.png", headers=AUTH).status_code == 404


def test_press_esegue_lazione_sincrona(ctx):
    client, _store, fake_ex, _p = ctx
    r = client.post("/press", json={"page": 0, "slot": 0}, headers=AUTH)
    assert r.json()["ok"] is True
    assert any("open" in c[0] for c in fake_ex.calls)


def test_press_riporta_lerrore_di_unazione_sincrona(ctx):
    client, _store, fake_ex, _p = ctx
    fake_ex.replies = {"open": R(False, error="app assente")}
    body = client.post("/press", json={"page": 0, "slot": 0}, headers=AUTH).json()
    assert body["ok"] is False
    assert "app assente" in body["error"]


def test_press_su_slot_vuoto(ctx):
    client, *_ = ctx
    r = client.post("/press", json={"page": 0, "slot": 7}, headers=AUTH)
    assert r.status_code == 404


def test_press_azione_asincrona_risponde_subito_come_accettata(ctx):
    client, store, _ex, _p = ctx
    store.save({"pages": [{"name": "X", "slots": [{
        "pos": [0, 0], "label": "S", "icon": "text:S",
        "action": {"type": "shell", "cmd": "true"}}]}]})
    body = client.post("/press", json={"page": 0, "slot": 0}, headers=AUTH).json()
    assert body["ok"] is True
    assert body["accepted"] is True


def test_state_ha_le_chiavi_attese_e_la_versione(ctx):
    client, store, *_ = ctx
    body = client.get("/state", headers=AUTH).json()
    assert body["layout_version"] == store.version
    for k in ("volume", "media", "system", "accessibility_ok", "last_error"):
        assert k in body


def test_state_riporta_lerrore_di_layout(ctx):
    client, store, *_ = ctx
    store.path.write_text("rotto: [")
    store.load()
    assert client.get("/state", headers=AUTH).json()["layout_error"]


def test_api_config_e_solo_loopback(ctx):
    client, *_ = ctx
    assert client.get("/api/config").status_code == 403


def test_api_config_da_loopback_legge_e_scrive(ctx):
    client, store, *_ = ctx
    r = client.get("/api/config", headers=LOCAL)
    assert r.status_code == 200
    assert r.json()["token"] == TOKEN
    prima = store.version
    r = client.put("/api/config", json={"pages": [{"name": "Nuova", "slots": []}]},
                   headers=LOCAL)
    assert r.status_code == 200
    assert store.version > prima


def test_api_config_rifiuta_un_layout_invalido(ctx):
    client, store, *_ = ctx
    prima = store.version
    r = client.put("/api/config", json={"pages": []}, headers=LOCAL)
    assert r.status_code == 422
    assert "almeno una pagina" in r.json()["detail"]
    assert store.version == prima


def test_api_test_esegue_senza_salvare(ctx):
    client, store, fake_ex, _p = ctx
    prima = store.version
    r = client.post("/api/test", json={"type": "app", "target": "Slack"},
                    headers=LOCAL)
    assert r.json()["ok"] is True
    assert store.version == prima
    assert fake_ex.calls


def test_api_icons_elenca_app_e_tipi(ctx):
    client, *_ = ctx
    body = client.get("/api/icons?q=find", headers=LOCAL).json()
    assert "apps" in body and "action_types" in body
    assert "app" in body["action_types"]


def test_salvataggio_svuota_la_cache_delle_tile(ctx):
    client, store, *_ = ctx
    client.get("/tile/0/0.png", headers=AUTH)
    client.put("/api/config", json={"pages": [{"name": "X", "slots": []}]},
               headers=LOCAL)
    assert client.get("/tile/0/0.png", headers=AUTH).status_code == 404
