from PIL import Image

from macdeck import icons


def test_text_rende_un_immagine_non_vuota():
    im = icons.resolve("text:PR", 64)
    assert im.size == (64, 64)
    assert im.mode == "RGBA"
    assert im.getbbox() is not None


def test_file_esistente(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGB", (10, 10), "red").save(p)
    im = icons.resolve(f"file:{p}", 32)
    assert im.size == (32, 32)


def test_file_inesistente_da_il_ripiego_senza_sollevare():
    assert icons.resolve("file:/non/esiste/mai.png", 48).size == (48, 48)


def test_schema_ignoto_da_il_ripiego():
    assert icons.resolve("boh:qualcosa", 40).size == (40, 40)


def test_spec_vuota_da_il_ripiego():
    assert icons.resolve("", 40).size == (40, 40)


def _e_il_ripiego(im) -> bool:
    return im.tobytes() == icons.fallback(im.width).tobytes()


def test_app_di_sistema_sempre_presente():
    # Non basta controllare la dimensione: il ripiego ha la dimensione giusta
    # anche quando l'estrazione dal bundle e' fallita. Va verificato che
    # l'icona sia REALE e a colori.
    im = icons.resolve("app:/System/Library/CoreServices/Finder.app", 64)
    assert im.size == (64, 64)
    assert not _e_il_ripiego(im)
    assert len(set(im.convert("RGB").get_flattened_data())) > 100


def test_app_risolta_per_nome():
    im = icons.resolve("app:Finder", 64)
    assert not _e_il_ripiego(im)


def test_emoji_e_a_colori_non_il_ripiego():
    im = icons.resolve("emoji:\U0001F680", 64)
    assert not _e_il_ripiego(im)
    assert len(set(im.convert("RGB").get_flattened_data())) > 100


def test_app_inesistente_da_il_ripiego():
    assert icons.resolve("app:/Applications/MaiEsistita.app", 64).size == (64, 64)


def test_emoji():
    im = icons.resolve("emoji:\U0001F680", 64)
    assert im.size == (64, 64)


def test_mdi_senza_font_installato_da_il_ripiego(tmp_path):
    im = icons.resolve("mdi:camera", 64, root=tmp_path)
    assert im.size == (64, 64)
    assert not icons.mdi_available(root=tmp_path)


def test_fallback_e_visibile():
    im = icons.fallback(64)
    assert im.size == (64, 64)
    assert im.getbbox() is not None


def test_parse_mdi_scss():
    scss = '''$mdi-version: "7.4.47" !default;

$mdi-icons: (
  "ab-testing": F01C9,
  "abacus": F16E0,
  "volume-off": F0581,
);'''
    m = icons.parse_mdi_scss(scss)
    assert m["abacus"] == "F16E0"
    assert m["volume-off"] == "F0581"
    assert "mdi-version" not in m
    assert len(m) == 3


def test_parse_mdi_scss_su_input_vuoto():
    assert icons.parse_mdi_scss("") == {}


def test_mdi_names_vuoto_senza_font(tmp_path):
    assert icons.mdi_names(root=tmp_path) == []
