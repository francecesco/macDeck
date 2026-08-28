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


# I test dell'API non devono dipendere dal contenuto di DEFAULT_LAYOUT:
# altrimenti cambiare le app predefinite rompe i test del protocollo.
LAYOUT_DI_PROVA = {
    "pages": [
        {
            "name": "Prova",
            "slots": [
                {"pos": [0, 0], "label": "Uno", "icon": "text:1",
                 "action": {"type": "app", "target": "Finder"}},
                {"pos": [1, 0], "label": "Due", "icon": "text:2",
                 "action": {"type": "volume", "op": "mute_toggle"},
                 "state": "volume.muted"},
            ],
        },
        {"name": "Vuota", "slots": []},
    ]
}


@pytest.fixture
def ctx(tmp_path, fake_ex):
    store = L.LayoutStore(tmp_path / "layout.yaml")
    store.load()
    store.save(LAYOUT_DI_PROVA)
    probe = StateProbe(fake_ex)
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
    assert body["pages"] == ["Prova", "Vuota"]
    slot = body["slots"][0]
    assert {"i", "x", "y", "w", "h", "url"} <= set(slot)
    assert slot["url"].startswith("/tile/0/")


def test_layout_pagina_inesistente(ctx):
    client, *_ = ctx
    assert client.get("/layout?page=99", headers=AUTH).status_code == 404
    assert client.get("/layout?page=1", headers=AUTH).status_code == 200


def test_tile_restituisce_un_png_della_dimensione_giusta(ctx):
    client, *_ = ctx
    r = client.get("/tile/0/0.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (154, 80)


def test_tile_di_uno_slot_vuoto(ctx):
    client, *_ = ctx
    assert client.get("/tile/0/5.png", headers=AUTH).status_code == 404


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
    r = client.post("/press", json={"page": 0, "slot": 5}, headers=AUTH)
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


def test_layout_espone_la_chiave_di_stato_dichiarata(ctx):
    client, *_ = ctx
    slots = client.get("/layout", headers=AUTH).json()["slots"]
    per_indice = {s["i"]: s for s in slots}
    assert per_indice[1]["state"] == "volume.muted"
    assert per_indice[0]["state"] is None


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


def test_api_icons_include_i_glifi_mdi(ctx, tmp_path):
    client, *_ = ctx
    body = client.get("/api/icons?q=volume", headers=LOCAL).json()
    assert "mdi" in body and "mdi_total" in body
    # senza font MDI installato nella root di prova la lista e' vuota, ma la
    # chiave deve esistere comunque perche' la GUI non deve gestire assenze
    assert isinstance(body["mdi"], list)


def test_icon_preview_rende_un_png(ctx):
    client, *_ = ctx
    r = client.get("/api/icon-preview?spec=text:AB&size=48", headers=LOCAL)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (48, 48)


def test_icon_preview_su_spec_rotta_da_il_ripiego_non_un_errore(ctx):
    client, *_ = ctx
    r = client.get("/api/icon-preview?spec=boh:niente", headers=LOCAL)
    assert r.status_code == 200


def test_icon_preview_limita_la_dimensione(ctx):
    client, *_ = ctx
    r = client.get("/api/icon-preview?spec=text:X&size=9999", headers=LOCAL)
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (256, 256)


def test_icon_preview_e_solo_loopback(ctx):
    client, *_ = ctx
    assert client.get("/api/icon-preview?spec=text:X").status_code == 403


def test_tile_preview_rende_una_tile_non_salvata(ctx, tmp_path):
    client, store, *_ = ctx
    prima = store.version
    r = client.post("/api/tile-preview", headers=LOCAL, json={
        "grid": {"cols": 3, "rows": 3},
        "slot": {"pos": [1, 1], "label": "Bozza", "icon": "text:B"},
    })
    assert r.status_code == 200
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (154, 80)
    assert store.version == prima          # l'anteprima non salva nulla


def test_tile_preview_rispetta_la_griglia_richiesta(ctx):
    client, *_ = ctx
    r = client.post("/api/tile-preview", headers=LOCAL, json={
        "grid": {"cols": 2, "rows": 2},
        "slot": {"pos": [0, 0], "label": "Grande", "icon": "text:G"},
    })
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (234, 122)


def test_tile_preview_rifiuta_una_griglia_impossibile(ctx):
    client, *_ = ctx
    r = client.post("/api/tile-preview", headers=LOCAL, json={
        "grid": {"cols": 9, "rows": 9},
        "slot": {"pos": [0, 0], "label": "X", "icon": "text:X"},
    })
    assert r.status_code == 422


def test_screen_e_un_png_a_schermo_intero(ctx):
    client, *_ = ctx
    r = client.get("/screen/0.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (L.DISPLAY_W, L.DISPLAY_H)


def test_screen_richiede_il_token(ctx):
    client, *_ = ctx
    assert client.get("/screen/0.png").status_code == 401


def test_screen_pagina_inesistente(ctx):
    client, *_ = ctx
    assert client.get("/screen/9.png", headers=AUTH).status_code == 404


def test_layout_indica_lurl_della_schermata(ctx):
    client, store, *_ = ctx
    body = client.get("/layout", headers=AUTH).json()
    assert body["screen"] == f"/screen/0.png?v={store.version}"


def test_screen_cambia_dopo_un_salvataggio(ctx):
    client, *_ = ctx
    prima = client.get("/screen/0.png", headers=AUTH).content
    client.put("/api/config", headers=LOCAL, json={"pages": [{
        "name": "Altro", "slots": [{"pos": [0, 0], "label": "Z", "icon": "text:Z",
                                    "action": {"type": "noop"}}]}]})
    dopo = client.get("/screen/0.png", headers=AUTH).content
    assert prima != dopo


def test_screen_di_una_pagina_vuota_non_esplode(ctx):
    client, *_ = ctx
    r = client.get("/screen/1.png", headers=AUTH)
    assert r.status_code == 200
