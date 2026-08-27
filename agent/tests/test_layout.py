import pytest
import yaml

from macdeck import layout as L


def test_geometria_della_griglia_di_default():
    boxes = L.slot_boxes(L.DEFAULT_GRID)
    assert len(boxes) == 12
    assert boxes[0] == {"x": 4, "y": 40, "w": 101, "h": 99}
    last = boxes[11]
    assert last["x"] + last["w"] <= L.DISPLAY_W
    assert last["y"] + last["h"] <= L.DISPLAY_H - L.NAVBAR_H


def test_griglia_piu_rada_da_tile_piu_grandi():
    boxes = L.slot_boxes({"cols": 2, "rows": 2})
    assert len(boxes) == 4
    assert boxes[0]["w"] > 101
    assert boxes[0]["h"] > 99


def test_slot_index_e_riga_per_colonne_piu_colonna():
    assert L.slot_index([0, 0], {"cols": 3, "rows": 4}) == 0
    assert L.slot_index([2, 0], {"cols": 3, "rows": 4}) == 2
    assert L.slot_index([1, 2], {"cols": 3, "rows": 4}) == 7


def test_layout_di_default_e_valido():
    assert L.validate(L.DEFAULT_LAYOUT)["pages"]


def test_validate_normalizza_i_default_mancanti():
    out = L.validate({"pages": [{"name": "X", "slots": []}]})
    assert out["schema"] == 1
    assert out["grid"] == L.DEFAULT_GRID
    assert out["theme"]["font"] == L.DEFAULT_THEME["font"]
    assert out["pages"][0]["grid"] == L.DEFAULT_GRID


def test_validate_calcola_geometria_e_indice_per_ogni_slot():
    out = L.validate({
        "pages": [{"name": "X", "slots": [
            {"pos": [1, 0], "label": "A", "icon": "text:A",
             "action": {"type": "noop"}}]}]
    })
    slot = out["pages"][0]["slots"][0]
    assert slot["index"] == 1
    assert slot["box"] == {"x": 109, "y": 40, "w": 101, "h": 99}


@pytest.mark.parametrize("raw,atteso", [
    ({"schema": 99, "pages": []}, "schema"),
    ({"pages": []}, "almeno una pagina"),
    ({"pages": [{"slots": []}]}, "name"),
    ({"grid": {"cols": 9, "rows": 9}, "pages": [{"name": "X", "slots": []}]}, "12"),
    ({"pages": [{"name": "X", "slots": [{"pos": [5, 0], "label": "A",
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


def test_save_incrementa_la_versione(tmp_path):
    store = L.LayoutStore(tmp_path / "layout.yaml")
    store.load()
    prima = store.version
    store.save({"pages": [{"name": "Nuova", "slots": []}]})
    assert store.version > prima
    assert store.layout["pages"][0]["name"] == "Nuova"
    assert store.error is None
