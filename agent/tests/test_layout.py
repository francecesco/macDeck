import pytest
import yaml

from macdeck import layout as L


def test_geometria_della_griglia_di_default():
    # navbar=True e' il default della funzione; il caso reale a pagina unica
    # e' coperto da test_senza_navbar_le_tile_sono_piu_alte.
    boxes = L.slot_boxes(L.DEFAULT_GRID, navbar=True)
    assert len(boxes) == 9
    assert boxes[0] == {"x": 4, "y": 4, "w": 154, "h": 92}
    last = boxes[max(boxes)]
    assert last["x"] + last["w"] <= L.DISPLAY_W
    assert last["y"] + last["h"] <= L.DISPLAY_H - L.NAVBAR_H


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


def test_senza_navbar_le_tile_sono_piu_alte():
    """Con una pagina sola i 28 px della barra vanno alle tile."""
    con = L.slot_boxes(L.DEFAULT_GRID, navbar=True)[0]
    senza = L.slot_boxes(L.DEFAULT_GRID, navbar=False)[0]
    assert senza["w"] == con["w"]
    assert con["h"] == 92
    assert senza["h"] == 98
    # e l'ultima riga non tocca il bordo inferiore
    ultima = L.slot_boxes(L.DEFAULT_GRID, navbar=False)[8]
    assert ultima["y"] + ultima["h"] <= L.DISPLAY_H - 8


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
