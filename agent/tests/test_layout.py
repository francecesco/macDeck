import pytest
import yaml

from macdeck import layout as L


def test_geometria_della_griglia_di_default():
    boxes = L.slot_boxes(L.DEFAULT_GRID)
    assert len(boxes) == 9
    assert boxes[0] == {"x": 5, "y": 4, "w": 154, "h": 101}
    last = boxes[max(boxes)]
    assert last["x"] + last["w"] <= L.DISPLAY_W
    assert last["y"] + last["h"] <= L.USABLE_H


def test_meno_righe_danno_tile_piu_alte():
    """L'altezza e' il vincolo che limita la dimensione dell'icona.

    Allargare la griglia (meno colonne) allarga le tile ma non le alza:
    per ingrandire davvero l'icona bisogna togliere una riga.
    """
    tre_righe = L.slot_boxes({"cols": 3, "rows": 3})[0]
    due_righe = L.slot_boxes({"cols": 3, "rows": 2})[0]
    assert due_righe["w"] == tre_righe["w"]
    assert due_righe["h"] > tre_righe["h"]


def test_slot_index_e_riga_per_colonne_piu_colonna():
    assert L.slot_index([0, 0], {"cols": 3, "rows": 3}) == 0
    assert L.slot_index([2, 0], {"cols": 3, "rows": 3}) == 2
    assert L.slot_index([1, 2], {"cols": 3, "rows": 3}) == 7


def test_layout_di_default_e_valido():
    assert L.validate(L.DEFAULT_LAYOUT)["pages"]


def test_validate_normalizza_i_default_mancanti():
    out = L.validate({"pages": [{"name": "X", "slots": []}]})
    assert out["schema"] == 1
    assert out["grid"] == L.DEFAULT_GRID
    assert out["theme"]["font"] == L.DEFAULT_THEME["font"]
    assert out["pages"][0]["grid"] == L.DEFAULT_GRID


def test_validate_calcola_lindice_ma_non_la_geometria():
    """Il box dipende da quante pagine sono visibili, che si sa solo a
    runtime: si calcola al momento di servire, non in validazione."""
    out = L.validate({
        "pages": [{"name": "X", "slots": [
            {"pos": [1, 0], "label": "A", "icon": "text:A",
             "action": {"type": "noop"}}]}]
    })
    slot = out["pages"][0]["slots"][0]
    assert slot["index"] == 1
    assert "box" not in slot


def test_la_geometria_non_dipende_dal_numero_di_pagine():
    """Non c'e' piu' una barra da riservare: le tile prendono tutto.

    Si cambia pagina con lo swipe, quindi slot_boxes ha un solo risultato
    possibile per una data griglia e non prende parametri oltre a quella.
    """
    boxes = L.slot_boxes(L.DEFAULT_GRID)
    assert boxes[0]["h"] == 101
    with pytest.raises(TypeError):
        L.slot_boxes(L.DEFAULT_GRID, navbar=True)


@pytest.mark.parametrize("grid", [
    {"cols": 3, "rows": 3},
    {"cols": 3, "rows": 2},
    {"cols": 4, "rows": 3},
    {"cols": 2, "rows": 2},
])
def test_i_margini_intorno_alla_griglia_sono_uguali(grid):
    """L'avanzo della divisione si divide fra i due bordi, non si accumula.

    Prima l'origine era fissa a 4 e tutti i pixel che l'intero non copriva
    finivano in fondo e a destra: sulla 3x3 il margine destro era 6 contro 4
    a sinistra, e in basso restava una fascia vuota molto piu' larga degli
    spazi fra le tile, che si notava.
    """
    boxes = L.slot_boxes(grid)
    primo, ultimo = boxes[0], boxes[max(boxes)]
    sinistra, destra = primo["x"], L.DISPLAY_W - (ultimo["x"] + ultimo["w"])
    sopra, sotto = primo["y"] - L.HEADER_H, L.USABLE_H - (ultimo["y"] + ultimo["h"])
    assert abs(sinistra - destra) <= 1
    assert abs(sopra - sotto) <= 1
    # e nessun margine e' piu' stretto dello spazio fra due tile
    assert min(sinistra, destra, sopra, sotto) >= L.GUTTER


def test_due_slot_sulla_stessa_casella_se_almeno_uno_ha_when():
    out = L.validate({"pages": [{"name": "X", "slots": [
        {"pos": [0, 0], "label": "Normale", "icon": "text:N",
         "action": {"type": "noop"}},
        {"pos": [0, 0], "label": "Condizionale", "icon": "text:C",
         "when": "media.app", "action": {"type": "noop"}},
    ]}]})
    assert len(out["pages"][0]["slots"]) == 2


def test_due_slot_incondizionati_sulla_stessa_casella_sono_un_errore():
    with pytest.raises(L.LayoutError) as e:
        L.validate({"pages": [{"name": "X", "slots": [
            {"pos": [0, 0], "label": "A", "icon": "text:A",
             "action": {"type": "noop"}},
            {"pos": [0, 0], "label": "B", "icon": "text:B",
             "action": {"type": "noop"}},
        ]}]})
    assert "occupato" in str(e.value)


def test_when_sullo_slot_non_stringa_viene_rifiutato():
    with pytest.raises(L.LayoutError) as e:
        L.validate({"pages": [{"name": "X", "slots": [
            {"pos": [0, 0], "label": "A", "icon": "text:A",
             "when": 7, "action": {"type": "noop"}}]}]})
    assert "when" in str(e.value)


@pytest.mark.parametrize("raw,atteso", [
    ({"schema": 99, "pages": []}, "schema"),
    ({"pages": []}, "almeno una pagina"),
    ({"pages": [{"slots": []}]}, "name"),
    ({"grid": {"cols": 9, "rows": 9}, "pages": [{"name": "X", "slots": []}]}, "12"),
    ({"pages": [{"name": "X", "slots": [{"pos": [7, 0], "label": "A",
      "icon": "text:A", "action": {"type": "noop"}}]}]}, "fuori dalla griglia"),
    ({"pages": [{"name": "X", "slots": [
        {"pos": [0, 0], "label": "A", "icon": "text:A", "action": {"type": "noop"}},
        {"pos": [0, 0], "label": "B", "icon": "text:B", "action": {"type": "noop"}}]}]},
     "occupato"),
    ({"pages": [{"name": "X", "slots": [{"pos": [0, 0], "label": "A",
      "icon": "text:A", "action": {"type": "inventato"}}]}]}, "ignoto"),
])
def test_validate_rifiuta_e_spiega_dove(raw, atteso):
    with pytest.raises(L.LayoutError) as e:
        L.validate(raw)
    assert atteso in str(e.value)


def test_validate_controlla_i_passi_di_una_sequence():
    raw = {"pages": [{"name": "X", "slots": [{
        "pos": [0, 0], "label": "A", "icon": "text:A",
        "action": {"type": "sequence", "steps": [{"type": "boh"}]},
    }]}]}
    with pytest.raises(L.LayoutError) as e:
        L.validate(raw)
    assert "ignoto" in str(e.value)


def test_store_crea_il_default_se_il_file_manca(tmp_path):
    f = tmp_path / "layout.yaml"
    store = L.LayoutStore(f)
    store.load()
    assert f.exists()
    assert store.error is None
    assert store.layout["pages"]


def test_store_mantiene_lultimo_valido_se_il_file_si_rompe(tmp_path):
    f = tmp_path / "layout.yaml"
    store = L.LayoutStore(f)
    store.load()
    buono = store.layout
    f.write_text("questo: [non chiude")
    store.load()
    assert store.layout == buono
    assert store.error is not None
    assert "layout" in store.error.lower() or "yaml" in store.error.lower()


def test_store_mantiene_lultimo_valido_anche_se_il_file_e_invalido(tmp_path):
    f = tmp_path / "layout.yaml"
    store = L.LayoutStore(f)
    store.load()
    buono = store.layout
    f.write_text(yaml.safe_dump({"pages": []}))
    store.load()
    assert store.layout == buono
    assert "almeno una pagina" in store.error


def test_save_valida_prima_di_scrivere(tmp_path):
    f = tmp_path / "layout.yaml"
    store = L.LayoutStore(f)
    store.load()
    originale = f.read_text()
    with pytest.raises(L.LayoutError):
        store.save({"pages": []})
    assert f.read_text() == originale


def test_save_cambia_la_versione(tmp_path):
    store = L.LayoutStore(tmp_path / "layout.yaml")
    store.load()
    prima = store.version
    store.save({"pages": [{"name": "Nuova", "slots": []}]})
    assert store.version != prima
    assert store.layout["pages"][0]["name"] == "Nuova"
    assert store.error is None


def test_la_versione_dipende_dal_contenuto_non_dai_riavvii(tmp_path):
    """Il bug che ha lasciato la pagina 1 ferma alla griglia vecchia.

    Con un contatore che riparte da 1 a ogni avvio, un agent riavviato con un
    layout DIVERSO annunciava la stessa versione di prima, e il display
    concludeva che non ci fosse nulla da ricaricare.
    """
    f = tmp_path / "layout.yaml"
    primo = L.LayoutStore(f)
    primo.load()
    v1 = primo.version

    # stesso contenuto, processo nuovo -> stessa versione
    secondo = L.LayoutStore(f)
    secondo.load()
    assert secondo.version == v1

    # contenuto diverso, processo nuovo -> versione diversa
    f.write_text(yaml.safe_dump({"pages": [{"name": "Altro", "slots": []}]}))
    terzo = L.LayoutStore(f)
    terzo.load()
    assert terzo.version != v1


def test_when_sulle_pagine_viene_accettato_e_normalizzato():
    out = L.validate({"pages": [
        {"name": "Sempre", "slots": []},
        {"name": "A volte", "when": "media.app", "slots": []},
    ]})
    assert out["pages"][0]["when"] is None
    assert out["pages"][1]["when"] == "media.app"


def test_when_non_stringa_viene_rifiutato():
    with pytest.raises(L.LayoutError) as e:
        L.validate({"pages": [{"name": "X", "when": 42, "slots": []}]})
    assert "when" in str(e.value)


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


def test_il_default_ha_una_pagina_per_ogni_app_della_home():
    out = L.validate(L.DEFAULT_LAYOUT)
    per_app = {p["name"]: p["app"] for p in out["pages"] if p["app"]}
    assert set(per_app) == {"WhatsApp", "Mail", "Slack", "DataGrip", "Claude",
                            "Calendar", "Spotify", "Chrome", "Claude Code"}
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


def test_la_pagina_claude_ha_lutilizzo_della_sessione_in_terza_casella():
    out = L.validate(L.DEFAULT_LAYOUT)
    claude = next(p for p in out["pages"] if p["name"] == "Claude Code")
    per_pos = {tuple(s["pos"]): s for s in claude["slots"]}
    assert per_pos[(2, 0)]["kind"] == "info"
    assert "{claude.session_used|int}%" in per_pos[(2, 0)]["label"]
    assert per_pos[(0, 1)]["kind"] == "info" and "{claude.dir}" in per_pos[(0, 1)]["label"]
    assert per_pos[(1, 1)]["label"] == "Esc"
    assert per_pos[(2, 2)]["label"] == "/clear"
    assert len(claude["slots"]) == 9


def test_ogni_app_della_home_ha_la_sua_pagina_di_dettaglio():
    out = L.validate(L.DEFAULT_LAYOUT)
    per_app = {p["name"]: p for p in out["pages"] if p["app"]}
    attese = {"WhatsApp", "Mail", "Slack", "DataGrip", "Claude", "Calendar",
              "Spotify", "Chrome", "Claude Code"}
    assert attese <= set(per_app)
    for nome in attese - {"Claude Code"}:
        info = [s for s in per_app[nome]["slots"] if s["kind"] == "info"]
        assert info, nome          # almeno un dato vivo per pagina
    spotify = per_app["Spotify"]
    assert any(s.get("state") == "media.shuffle" for s in spotify["slots"])
