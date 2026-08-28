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
    stato = client.get("/state", headers=AUTH).json()
    # /layout e /state devono annunciare LA STESSA versione, altrimenti il
    # display ricarica in continuazione o non ricarica mai.
    assert body["version"] == stato["layout_version"]
    assert body["page"] == 0
    assert body["pages"] == ["Prova", "Vuota"]
    slot = body["slots"][0]
    assert {"i", "x", "y", "w", "h", "url"} <= set(slot)
    assert slot["url"].startswith("/tile/0/")


def test_pagina_fuori_intervallo_viene_riportata_dentro(ctx):
    """Il display resterebbe altrimenti bloccato su una pagina sparita.

    Succede ogni volta che una pagina con `when:` smette di essere visibile
    mentre il display ci si trova sopra.
    """
    client, *_ = ctx
    r = client.get("/layout?page=99", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["page"] == 1          # ultima valida, non quella chiesta
    assert client.get("/layout?page=1", headers=AUTH).json()["page"] == 1
    assert client.get("/layout?page=-5", headers=AUTH).json()["page"] == 0


def test_tile_restituisce_un_png_della_dimensione_giusta(ctx):
    # Il layout di prova ha due pagine, quindi la navbar c'e' e le tile sono
    # alte 80. Con una pagina sola sarebbero 89.
    client, *_ = ctx
    r = client.get("/tile/0/0.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (154, 92)


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


def test_press_su_una_pagina_fuori_intervallo_agisce_sull_ultima(ctx):
    client, _store, fake_ex, _p = ctx
    r = client.post("/press", json={"page": 99, "slot": 0}, headers=AUTH)
    # la pagina 1 di prova e' vuota: nessuno slot 0, quindi 404 sullo SLOT
    # e non sulla pagina. Il punto e' che non esplode sull'indice di pagina.
    assert r.status_code == 404
    assert "slot" in r.json()["detail"]


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
    assert body["layout_version"] == client.get("/layout", headers=AUTH).json()["version"]
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
    assert store.version != prima


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
        assert im.size == (154, 98)
    assert store.version == prima          # l'anteprima non salva nulla


def test_tile_preview_rispetta_la_griglia_richiesta(ctx):
    client, *_ = ctx
    r = client.post("/api/tile-preview", headers=LOCAL, json={
        "grid": {"cols": 2, "rows": 2},
        "slot": {"pos": [0, 0], "label": "Grande", "icon": "text:G"},
    })
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (234, 149)


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


def test_screen_di_una_pagina_fuori_intervallo_ripiega_sull_ultima(ctx):
    client, *_ = ctx
    fuori = client.get("/screen/9.png", headers=AUTH)
    ultima = client.get("/screen/1.png", headers=AUTH)
    assert fuori.status_code == 200
    assert fuori.content == ultima.content


def test_layout_indica_lurl_della_schermata(ctx):
    client, *_ = ctx
    body = client.get("/layout", headers=AUTH).json()
    assert body["screen"] == f"/screen/0.png?v={body['version']}"


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


# ------------------------------------------------- pagine a comparsa (when:)

LAYOUT_CON_WHEN = {
    "pages": [
        {"name": "Sempre", "slots": [
            {"pos": [0, 0], "label": "A", "icon": "text:A",
             "action": {"type": "noop"}}]},
        {"name": "SoloConPlayer", "when": "media.app", "slots": [
            {"pos": [0, 0], "label": "B", "icon": "text:B",
             "action": {"type": "media", "op": "play_pause"}}]},
    ]
}


def _con_player(fake_ex, attivo: bool):
    if attivo:
        fake_ex.replies = {"running_apps": R(True, out="Spotify\ntrue\nX\nY\n")}
    else:
        fake_ex.replies = {"running_apps": R(True, out="none\n")}


def test_pagina_con_when_e_nascosta_se_la_condizione_e_falsa(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_CON_WHEN)
    _con_player(fake_ex, False)
    probe.refresh()
    body = client.get("/layout", headers=AUTH).json()
    assert body["pages"] == ["Sempre"]


def test_pagina_con_when_compare_quando_la_condizione_diventa_vera(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_CON_WHEN)
    _con_player(fake_ex, True)
    probe.refresh()
    body = client.get("/layout", headers=AUTH).json()
    assert body["pages"] == ["Sempre", "SoloConPlayer"]
    assert client.get("/layout?page=1", headers=AUTH).status_code == 200


def test_la_versione_cambia_quando_una_pagina_compare(ctx):
    """Senza questo, il display non si accorgerebbe della pagina nuova."""
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_CON_WHEN)
    _con_player(fake_ex, False)
    probe.refresh()
    senza = client.get("/state", headers=AUTH).json()["layout_version"]
    _con_player(fake_ex, True)
    probe.refresh()
    con = client.get("/state", headers=AUTH).json()["layout_version"]
    assert senza != con


def test_chi_e_fermo_su_una_pagina_sparita_viene_riportato_indietro(ctx):
    """Lo scenario reale: il display era sulla pagina Media, il player si
    chiude, la pagina sparisce. Deve ritrovarsi sulla pagina 0, non bloccato."""
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_CON_WHEN)
    _con_player(fake_ex, False)
    probe.refresh()
    body = client.get("/layout?page=1", headers=AUTH).json()
    assert body["page"] == 0
    assert body["pages"] == ["Sempre"]
    assert client.get("/screen/1.png", headers=AUTH).status_code == 200


def test_se_nessuna_pagina_e_visibile_si_mostrano_tutte(ctx):
    """Un deck vuoto e' peggio di un deck con una pagina di troppo."""
    client, store, fake_ex, probe = ctx
    store.save({"pages": [{"name": "Solo", "when": "media.app", "slots": []}]})
    _con_player(fake_ex, False)
    probe.refresh()
    assert client.get("/layout", headers=AUTH).json()["pages"] == ["Solo"]


def test_la_schermata_riflette_la_pagina_visibile_giusta(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_CON_WHEN)
    _con_player(fake_ex, True)
    probe.refresh()
    con_player = client.get("/screen/1.png", headers=AUTH)
    assert con_player.status_code == 200
    _con_player(fake_ex, False)
    probe.refresh()
    senza = client.get("/screen/1.png", headers=AUTH)
    assert senza.status_code == 200
    assert senza.content != con_player.content   # ripiega sulla pagina 0


def test_la_versione_efficace_e_stabile_fra_processi(tmp_path, fake_ex):
    """hash() di Python e' randomizzato per processo: usarlo qui rimetterebbe
    il difetto che content_version() serviva a togliere."""
    from macdeck.app import create_app as crea
    from macdeck.render import TileCache as Cache
    from macdeck.state import StateProbe as Probe

    def versione():
        store = L.LayoutStore(tmp_path / "layout.yaml")
        store.load()
        app = crea(store=store, cache=Cache(), probe=Probe(fake_ex),
                   executor=fake_ex, token=TOKEN, root=tmp_path,
                   trust_loopback_header=True)
        return TestClient(app).get("/layout", headers=AUTH).json()["version"]

    assert versione() == versione()


# --------------------------------- slot a comparsa: la riga che si trasforma

LAYOUT_RIGA_CONDIZIONALE = {
    "grid": {"cols": 3, "rows": 3},
    "pages": [{"name": "Dev", "slots": [
        {"pos": [0, 0], "label": "App", "icon": "text:A",
         "action": {"type": "app", "target": "Finder"}},
        # stessa casella, due contenuti: quello condizionale vince quando
        # la sua condizione e' vera, indipendentemente dall'ordine nel file
        {"pos": [0, 2], "label": "Slack", "icon": "text:S",
         "action": {"type": "app", "target": "Slack"}},
        {"pos": [0, 2], "label": "Indietro", "icon": "text:<",
         "when": "media.app", "action": {"type": "media", "op": "prev"}},
    ]}]
}


def test_senza_player_la_casella_mostra_lapp(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_RIGA_CONDIZIONALE)
    _con_player(fake_ex, False)
    probe.refresh()
    slots = {s["i"]: s for s in client.get("/layout", headers=AUTH).json()["slots"]}
    assert 6 in slots
    r = client.post("/press", json={"page": 0, "slot": 6}, headers=AUTH)
    assert r.json()["ok"]
    assert any("Slack" in " ".join(c) for c in fake_ex.calls)


def test_con_player_la_stessa_casella_diventa_un_comando_media(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_RIGA_CONDIZIONALE)
    _con_player(fake_ex, True)
    probe.refresh()
    fake_ex.calls.clear()
    client.post("/press", json={"page": 0, "slot": 6}, headers=AUTH)
    scripts = " ".join(fake_ex.scripts)
    assert "previous track" in scripts
    assert not any("Slack" in " ".join(c) for c in fake_ex.calls)


def test_lo_scambio_della_casella_cambia_la_versione(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_RIGA_CONDIZIONALE)
    _con_player(fake_ex, False)
    probe.refresh()
    a = client.get("/state", headers=AUTH).json()["layout_version"]
    _con_player(fake_ex, True)
    probe.refresh()
    b = client.get("/state", headers=AUTH).json()["layout_version"]
    assert a != b


def test_una_pagina_sola_non_ha_navbar_e_le_tile_sono_piu_alte(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_RIGA_CONDIZIONALE)
    _con_player(fake_ex, False)
    probe.refresh()
    body = client.get("/layout", headers=AUTH).json()
    assert body["nav"] is False
    assert body["slots"][0]["h"] == 98


def test_con_due_pagine_la_navbar_torna_e_le_tile_si_abbassano(ctx):
    client, store, fake_ex, probe = ctx
    store.save({"grid": {"cols": 3, "rows": 3}, "pages": [
        {"name": "Uno", "slots": [{"pos": [0, 0], "label": "A", "icon": "text:A",
                                   "action": {"type": "noop"}}]},
        {"name": "Due", "slots": []},
    ]})
    probe.refresh()
    body = client.get("/layout", headers=AUTH).json()
    assert body["nav"] is True
    assert body["slots"][0]["h"] == 92
