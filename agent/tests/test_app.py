import io
import time

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


class _AnnouncerFinto:
    def __init__(self, stato):
        self._stato = stato

    def status(self):
        return self._stato


@pytest.fixture
def client_con_announcer(ctx):
    client, *_ = ctx
    client.app.state.announcer = _AnnouncerFinto({
        "deck": "192.168.0.174",
        "annunciato": "192.168.0.165",
        "ultimo_errore": None,
        "ultimo_giro": time.time(),
    })
    return client


@pytest.fixture
def client_con_announcer_fermo(ctx):
    client, *_ = ctx
    client.app.state.announcer = _AnnouncerFinto({
        "deck": None, "annunciato": None,
        "ultimo_errore": None, "ultimo_giro": 0.0,
    })
    return client


def test_health_senza_announcer_non_esplode(ctx):
    # create_app nei test non ha un announcer attaccato: l'endpoint deve
    # ammettere di non sapere, non sollevare
    client, *_ = ctx
    r = client.get("/api/health", headers=LOCAL)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["deck"] is None
    assert corpo["last_round"] is None


def test_health_riporta_quello_che_l_announcer_sa(client_con_announcer):
    r = client_con_announcer.get("/api/health", headers=LOCAL)
    corpo = r.json()
    assert corpo["deck"] == "192.168.0.174"
    assert corpo["announced"] == "192.168.0.165"
    # secondi trascorsi, non un orario: distingue "non trovato" da
    # "non ha ancora guardato"
    assert 0.0 <= corpo["last_round"] < 5.0


def test_health_distingue_non_trovato_da_non_ancora_guardato(client_con_announcer_fermo):
    corpo = client_con_announcer_fermo.get("/api/health", headers=LOCAL).json()
    assert corpo["deck"] is None
    assert corpo["last_round"] is None       # non ha mai fatto un giro


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
    client, *_ = ctx
    r = client.get("/tile/0/0.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (154, 101)


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
        assert im.size == (154, 101)
    assert store.version == prima          # l'anteprima non salva nulla


def test_tile_preview_rispetta_la_griglia_richiesta(ctx):
    client, *_ = ctx
    r = client.post("/api/tile-preview", headers=LOCAL, json={
        "grid": {"cols": 2, "rows": 2},
        "slot": {"pos": [0, 0], "label": "Grande", "icon": "text:G"},
    })
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (234, 154)


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


def test_le_tile_sono_alte_uguale_con_una_pagina_o_con_due(ctx):
    """La barra in fondo non c'e' piu': si cambia pagina con lo swipe.

    Prima i suoi 28 px comparivano e sparivano a seconda di quante pagine
    erano visibili, e le tile si alzavano e abbassavano sotto il dito.
    """
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_RIGA_CONDIZIONALE)
    _con_player(fake_ex, False)
    probe.refresh()
    una = client.get("/layout", headers=AUTH).json()
    assert "nav" not in una
    assert una["slots"][0]["h"] == 101

    store.save({"grid": {"cols": 3, "rows": 3}, "pages": [
        {"name": "Uno", "slots": [{"pos": [0, 0], "label": "A", "icon": "text:A",
                                   "action": {"type": "noop"}}]},
        {"name": "Due", "slots": []},
    ]})
    probe.refresh()
    due = client.get("/layout", headers=AUTH).json()
    assert "nav" not in due
    assert due["slots"][0]["h"] == 101


def test_l_elenco_app_non_viene_troncato(ctx):
    # Il selettore scarica l'elenco intero e filtra nel browser: se il server
    # ne manda solo una fetta, le app in fondo all'alfabeto sono
    # irraggiungibili e sembrano non installate.
    from macdeck import icons
    client = ctx[0]
    installate = {b.stem for d in icons.app_dirs() for b in d.glob("*.app")}
    r = client.get("/api/icons", headers=LOCAL)
    assert r.status_code == 200
    restituite = {a["disk"] for a in r.json()["apps"]}
    mancanti = installate - restituite
    assert not mancanti, f"{len(mancanti)} app non elencate, es. {sorted(mancanti)[:5]}"


def test_i_glifi_mdi_restano_limitati(ctx):
    # Sono settemila: quelli vanno tagliati, ed e' il motivo per cui il
    # limite esisteva. Le app sono un paio di centinaia, e non c'entrano.
    r = ctx[0].get("/api/icons", headers=LOCAL)
    d = r.json()
    if not d["mdi_total"]:
        pytest.skip("font MDI non installato in questa radice di prova")
    assert len(d["mdi"]) <= 120


def test_forzare_il_ridisegno_cambia_la_versione(ctx):
    # Le icone vivono fuori dal layout: se cambia l'icona di un'app, o
    # migliora il modo in cui la risolviamo, il layout resta identico e il
    # display non ha motivo di riscaricare. Serve poterglielo dire.
    client = ctx[0]
    prima = client.get("/layout", headers=AUTH).json()["version"]
    r = client.post("/api/refresh", headers=LOCAL)
    assert r.status_code == 200
    dopo = client.get("/layout", headers=AUTH).json()["version"]
    assert dopo != prima


def test_il_ridisegno_svuota_la_cache_delle_tile(ctx):
    client, store, ex, probe = ctx
    client.get("/tile/0/0.png", headers=AUTH)
    client.post("/api/refresh", headers=LOCAL)
    # la cache viene svuotata: la tile successiva si rigenera
    assert client.get("/tile/0/0.png", headers=AUTH).status_code == 200


def test_il_ridisegno_e_solo_locale(ctx):
    assert ctx[0].post("/api/refresh").status_code == 403


def test_keys_canon_traduce_un_evento_vero(ctx):
    client, *_ = ctx
    r = client.post("/api/keys-canon", headers=LOCAL,
                    json={"keyCode": 118, "modifiers": ["cmd", "shift"]})
    assert r.status_code == 200
    assert r.json() == {"keys": "cmd+shift+f4"}


def test_keys_canon_accetta_un_tasto_stampabile(ctx):
    client, *_ = ctx
    r = client.post("/api/keys-canon", headers=LOCAL,
                    json={"keyCode": 21, "modifiers": ["cmd"], "chars": "4"})
    assert r.json() == {"keys": "cmd+4"}


def test_keys_canon_rifiuta_un_evento_che_non_sa_tradurre(ctx):
    client, *_ = ctx
    # 422 e non 500: e' un ingresso sbagliato, non un guasto del server
    r = client.post("/api/keys-canon", headers=LOCAL,
                    json={"keyCode": 9999, "modifiers": []})
    assert r.status_code == 422


# ------------------------------------------------------- pagine per app

LSAPP_LIST = ' 1) "Spotify" ASN:0x0-0x1:\n    bundleID="com.spotify.client"\n'
LSAPP_INFO_SPOTIFY = ('"LSDisplayName"="Spotify"\n"CFBundleIdentifier"="com.spotify.client"\n'
                      '"CFBundleExecutablePath"="/Applications/Spotify.app/Contents/MacOS/Spotify"\n')
LSAPP_INFO_CHROME = ('"LSDisplayName"="Google Chrome"\n"CFBundleIdentifier"="com.google.Chrome"\n'
                     '"CFBundleExecutablePath"="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"\n')
LSAPP_INFO_SAFARI = ('"LSDisplayName"="Safari"\n"CFBundleIdentifier"="com.apple.Safari"\n'
                     '"CFBundleExecutablePath"="/Applications/Safari.app/Contents/MacOS/Safari"\n')

LAYOUT_PER_APP = {
    "pages": [
        {"name": "Griglia", "slots": [
            {"pos": [0, 0], "label": "A", "icon": "text:A", "action": {"type": "noop"}},
            # Stesso indice (3) del Play di Spotify, sotto: e' apposta,
            # serve ai test sulla corsa fra /layout e /press.
            {"pos": [0, 1], "label": "G", "icon": "text:G",
             "action": {"type": "shell", "cmd": "echo GRIGLIA"}}]},
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


def test_un_cambio_fra_app_senza_pagina_non_mangia_lo_swipe(ctx):
    """Chrome e Safari non hanno una pagina propria in LAYOUT_PER_APP: il
    mazzo servito resta identico passando dall'uno all'altro, quindi il
    ricordo del salto non deve cambiare — e lo swipe successivo deve
    arrivare a destinazione invece di essere rispedito a pagina 0."""
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_CHROME)
    probe.refresh()
    client.get("/layout?page=0", headers=AUTH)     # fissa il ricordo: nessuna pagina d'app
    _davanti(fake_ex, LSAPP_INFO_SAFARI)
    probe.refresh()                                # cambio dal vivo, nessun /layout nuovo
    body = client.get("/layout?page=1", headers=AUTH).json()  # swipe su Altra
    assert body["page"] == 1


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


def test_tile_preview_accetta_una_posizione_in_stringa(ctx):
    client, *_ = ctx
    r = client.post("/api/tile-preview", headers=LOCAL, json={
        "grid": {"cols": 3, "rows": 2},
        "slot": {"pos": ["0", "0"], "span": 2, "label": "S", "icon": "text:S"},
    })
    assert r.status_code == 200


# ---------------------------------------- la corsa fra /layout e /press


def test_press_agisce_sulla_pagina_servita_non_su_quella_viva(ctx):
    """Il caso reale: Spotify davanti, tocco su Play. Prima del prossimo
    poll l'utente passa a Chrome con Alt-Tab: /press non deve eseguire
    l'azione della Griglia, che ha uno slot alla stessa casella."""
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    client.get("/layout?page=0", headers=AUTH)     # servito: Spotify, pagina 0
    _davanti(fake_ex, LSAPP_INFO_CHROME)
    probe.refresh()                                # cambio dal vivo, nessun /layout nuovo
    r = client.post("/press", headers=AUTH, json={"page": 0, "slot": 3})
    assert r.json()["ok"] is True
    assert any("playpause" in s for s in fake_ex.scripts)          # Play di Spotify
    assert not any("GRIGLIA" in " ".join(c) for c in fake_ex.calls)  # non la Griglia


def test_screen_con_la_versione_servita_mostra_il_mazzo_servito(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    body = client.get("/layout?page=0", headers=AUTH).json()
    v = body["version"]
    prima = client.get(f"/screen/0.png?v={v}", headers=AUTH).content
    _davanti(fake_ex, LSAPP_INFO_CHROME)
    probe.refresh()
    servita = client.get(f"/screen/0.png?v={v}", headers=AUTH).content
    assert servita == prima          # il mazzo servito non si muove sotto al display
    viva = client.get("/screen/0.png", headers=AUTH).content
    assert viva != prima             # senza 'v' combaciante si vede il mazzo dal vivo


def test_il_ricordo_si_aggiorna_solo_in_layout(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_CHROME)
    probe.refresh()
    client.get("/layout?page=0", headers=AUTH)                # fissa il ricordo: Chrome
    body = client.get("/layout?page=1", headers=AUTH).json()  # swipe manuale su Altra
    assert body["page"] == 1
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    client.get("/screen/1.png", headers=AUTH)                  # non deve consumare il salto
    client.post("/press", headers=AUTH, json={"page": 1, "slot": 0})  # nemmeno /press
    body = client.get("/layout?page=1", headers=AUTH).json()
    assert body["page"] == 0 and body["pages"][0] == "Spotify"


def test_pagina_fuori_intervallo_durante_un_salto_va_a_zero(ctx):
    client, store, fake_ex, probe = ctx
    store.save(LAYOUT_PER_APP)
    _davanti(fake_ex, LSAPP_INFO_CHROME)
    probe.refresh()
    client.get("/layout?page=0", headers=AUTH)     # fissa il ricordo: Chrome
    _davanti(fake_ex, LSAPP_INFO_SPOTIFY)
    probe.refresh()
    body = client.get("/layout?page=99", headers=AUTH).json()
    assert body["page"] == 0
