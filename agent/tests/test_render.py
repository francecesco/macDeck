import io

from PIL import Image

from macdeck import layout as L
from macdeck import render
from macdeck.render import TileCache, render_tile, resolve_font, tile_png

SLOT = {
    "pos": [0, 0], "index": 0,
    "box": {"x": 4, "y": 4, "w": 154, "h": 98},
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
    assert im.size == (154, 98)
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
    assert render_tile(slot, L.DEFAULT_THEME).size == (154, 98)


def test_etichetta_con_accenti_e_simboli():
    slot = {**SLOT, "label": "Città 21°"}
    assert render_tile(slot, L.DEFAULT_THEME).size == (154, 98)


def test_tile_png_e_un_png_valido():
    data = tile_png(SLOT, L.DEFAULT_THEME)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (154, 98)


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


def _icona_visibile(im):
    """Altezza in pixel della porzione non uniforme in alto: l'icona."""
    import itertools
    px = im.convert("RGB").load()
    w, h = im.size
    fondo = px[2, 2]
    righe = [y for y in range(h) if any(px[x, y] != fondo for x in range(0, w, 3))]
    return len(righe)


def test_icon_scale_ingrandisce_davvero_licona():
    slot = {**SLOT, "icon": "text:W", "label": "Prova"}
    piccola = render_tile(slot, {**L.DEFAULT_THEME, "icon_scale": 0.6})
    grande = render_tile(slot, {**L.DEFAULT_THEME, "icon_scale": 1.0})
    assert _icona_visibile(grande) >= _icona_visibile(piccola)


def test_licona_non_sborda_mai_dalla_tile():
    for h in (60, 80, 122, 200):
        slot = {**SLOT, "box": {"x": 0, "y": 0, "w": 154, "h": h},
                "label": "Etichetta lunga che va a capo di sicuro"}
        for scale in (0.5, 1.0, 1.5, 3.0):
            im = render_tile(slot, {**L.DEFAULT_THEME, "icon_scale": scale})
            assert im.size == (154, h)


BOXES = L.slot_boxes({"cols": 3, "rows": 2})


def _info(**s):
    return {"kind": "info", "label": "Anagrafe", "caption": "Marlene Kuntz",
            "icon": None, "box": L.span_box(BOXES, 0, 3), **s}


def test_info_ha_la_dimensione_del_box_allargato():
    im = render.render_tile(_info(), L.DEFAULT_THEME)
    assert im.size == (3 * BOXES[0]["w"] + 2 * L.GUTTER, BOXES[0]["h"])


def test_info_disegna_qualcosa():
    im = render.render_tile(_info(), L.DEFAULT_THEME)
    fondo = im.getpixel((0, 0))
    assert any(im.getpixel((x, im.height // 2)) != fondo for x in range(im.width))


def test_info_valore_vuoto_mostra_solo_la_didascalia_senza_sollevare():
    im_vuota = render.render_tile(_info(label=""), L.DEFAULT_THEME)
    im_niente = render.render_tile(_info(label="", caption=""), L.DEFAULT_THEME)
    assert im_vuota.tobytes() != im_niente.tobytes()


def test_info_valore_lunghissimo_resta_nel_box():
    lungo = "Un titolo di brano davvero interminabile " * 4
    im = render.render_tile(_info(label=lungo, box=BOXES[0]), L.DEFAULT_THEME)
    assert im.size == (BOXES[0]["w"], BOXES[0]["h"])


def test_info_con_icona_e_diversa_da_senza():
    con = render.render_tile(_info(icon="text:S"), L.DEFAULT_THEME)
    senza = render.render_tile(_info(), L.DEFAULT_THEME)
    assert con.tobytes() != senza.tobytes()


def test_cache_distingue_kind_e_caption():
    c = render.TileCache()
    a = c.png({**_info(), "kind": "button", "caption": None, "icon": "text:A",
               "label": "X"}, L.DEFAULT_THEME)
    b = c.png({**_info(), "label": "X", "icon": "text:A"}, L.DEFAULT_THEME)
    assert a != b
    assert c.size == 2
