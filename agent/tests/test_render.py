import io

from PIL import Image

from macdeck import layout as L
from macdeck.render import TileCache, render_tile, resolve_font, tile_png

SLOT = {
    "pos": [0, 0], "index": 0,
    "box": {"x": 4, "y": 40, "w": 115, "h": 80},
    "label": "DataGrip", "icon": "text:DG", "color": None,
    "state": None, "action": {"type": "noop"},
}


def test_font_alias_e_path_assoluto_funzionano_entrambi():
    assert resolve_font("SFNS", 14)
    assert resolve_font("/System/Library/Fonts/SFNS.ttf", 14)


def test_font_ignoto_ripiega_senza_sollevare():
    assert resolve_font("FontCheNonEsiste", 14)


def test_tile_ha_la_dimensione_del_box():
    im = render_tile(SLOT, L.DEFAULT_THEME)
    assert im.size == (115, 80)
    assert im.mode == "RGB"


def test_tile_di_una_griglia_diversa_e_piu_grande():
    slot = {**SLOT, "box": {"x": 4, "y": 40, "w": 154, "h": 122}}
    assert render_tile(slot, L.DEFAULT_THEME).size == (154, 122)


def test_tile_disegna_qualcosa_di_diverso_dal_fondo():
    im = render_tile(SLOT, L.DEFAULT_THEME)
    colori = {c for _n, c in im.getcolors(maxcolors=100000)}
    assert len(colori) > 3


def test_color_override_cambia_il_risultato():
    a = tile_png(SLOT, L.DEFAULT_THEME)
    b = tile_png({**SLOT, "color": "#B03030"}, L.DEFAULT_THEME)
    assert a != b


def test_etichetta_lunga_non_solleva_e_resta_nel_box():
    slot = {**SLOT, "label": "Etichetta davvero molto lunga che non ci sta"}
    assert render_tile(slot, L.DEFAULT_THEME).size == (115, 80)


def test_etichetta_con_accenti_e_simboli():
    slot = {**SLOT, "label": "Città 21°"}
    assert render_tile(slot, L.DEFAULT_THEME).size == (115, 80)


def test_tile_png_e_un_png_valido():
    data = tile_png(SLOT, L.DEFAULT_THEME)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (115, 80)


def test_cache_riusa_lo_stesso_risultato():
    cache = TileCache()
    primo = cache.png(SLOT, L.DEFAULT_THEME)
    assert cache.size == 1
    secondo = cache.png(SLOT, L.DEFAULT_THEME)
    assert primo is secondo
    assert cache.size == 1


def test_cache_distingue_slot_diversi():
    cache = TileCache()
    cache.png(SLOT, L.DEFAULT_THEME)
    cache.png({**SLOT, "label": "Altro"}, L.DEFAULT_THEME)
    assert cache.size == 2


def test_cache_distingue_temi_diversi():
    cache = TileCache()
    cache.png(SLOT, L.DEFAULT_THEME)
    cache.png(SLOT, {**L.DEFAULT_THEME, "tile": "#000000"})
    assert cache.size == 2


def test_clear_svuota():
    cache = TileCache()
    cache.png(SLOT, L.DEFAULT_THEME)
    cache.clear()
    assert cache.size == 0
