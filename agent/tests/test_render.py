import io

from PIL import Image, ImageColor, ImageDraw

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
    w, h = int(BOXES[0]["w"]), int(BOXES[0]["h"])
    im = render.render_tile(_info(label=lungo, box=BOXES[0]), L.DEFAULT_THEME)
    assert im.size == (w, h)
    # Controlla che il margine destro contenga solo il colore di fondo
    bg_color = ImageColor.getrgb(L.DEFAULT_THEME["tile"])
    px = im.load()
    pad = render.PAD
    for x in range(w - pad * 2, w - 2):
        for y in range(h // 4, 3 * h // 4):
            assert px[x, y] == bg_color, f"Testo spillato oltre il box a ({x}, {y})"


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


def test_dim_accetta_nomi_e_hex_corti():
    # Test con colore nominale e hex corto nel tema
    im1 = render.render_tile(_info(color="red", caption="Y"), L.DEFAULT_THEME)
    assert im1.size == (3 * BOXES[0]["w"] + 2 * L.GUTTER, BOXES[0]["h"])

    # Test con hex corto nel tema di testo
    im2 = render.render_tile(_info(caption="Z"), {**L.DEFAULT_THEME, "text": "#fff"})
    assert im2.size == (3 * BOXES[0]["w"] + 2 * L.GUTTER, BOXES[0]["h"])


def test_dim_attenua_verso_lo_sfondo():
    # _dim("#ffffff", "#000000") dovrebbe tornare un grigio fra i due
    result = render._dim("#ffffff", "#000000")
    # Risultato è una tupla (r, g, b)
    assert isinstance(result, tuple)
    r, g, b = result
    # Ogni canale deve essere strettamente fra 0 e 255 esclusivi
    for c in (r, g, b):
        assert 0 < c < 255


def test_ellipsize_tronca_con_ellissi_dentro_la_larghezza():
    im = Image.new("RGB", (300, 100), "white")
    draw = ImageDraw.Draw(im)
    font = render.resolve_font("SFNS", 14)

    # Stringa lunga da troncare
    long_string = "X" * 200
    result = render._ellipsize(draw, long_string, font, max_w=100)

    # Deve terminare con ellissi
    assert result.endswith("…")

    # Deve stare dentro max_w
    assert draw.textlength(result, font=font) <= 100

    # Stringa corta ritornata intatta
    short_string = "short"
    result_short = render._ellipsize(draw, short_string, font, max_w=100)
    assert result_short == short_string


def test_wrap_tronca_con_ellissi_quando_supera_le_righe():
    im = Image.new("RGB", (300, 100), "white")
    draw = ImageDraw.Draw(im)
    font = render.resolve_font("SFNS", 12)

    # Esempio: etichetta che avrebbe 3+ righe, tronca a 2 con ellissi
    text = "uno due tre quattro cinque sei sette otto nove dieci undici dodici"
    out = render._wrap(draw, text, font, max_w=80, max_lines=2)

    assert len(out) == 2
    assert out[-1].endswith("…"), f"Ultima riga deve finire con ellissi, ma è: {out[-1]}"
    assert draw.textlength(out[-1], font=font) <= 80

    # Etichetta che sta in 2 righe: ritornata senza ellissi
    short_text = "uno due tre"
    out_short = render._wrap(draw, short_text, font, max_w=80, max_lines=2)
    assert all(not line.endswith("…") for line in out_short), \
        f"Testo che entra in 2 righe non deve avere ellissi: {out_short}"
