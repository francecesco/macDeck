from pathlib import Path

import pytest
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


# ------------------------------------------------- i nomi che mostra il Finder


def test_mappa_dei_nomi_visualizzati(fake_ex):
    from macdeck.executor import Result as R
    from macdeck import icons as I
    I.reset_display_names()
    # mdls con piu' file separa i valori con NUL
    fake_ex.replies = {"mdls": R(True, out="Anteprima\0Calcolatrice\0")}
    m = I.display_names([Path("/A/Preview.app"), Path("/A/Calculator.app")], fake_ex)
    assert m == {"/A/Preview.app": "Anteprima", "/A/Calculator.app": "Calcolatrice"}


def test_una_sola_chiamata_per_tutte_le_app(fake_ex):
    from macdeck.executor import Result as R
    from macdeck import icons as I
    I.reset_display_names()
    fake_ex.replies = {"mdls": R(True, out="A\0B\0C\0")}
    I.display_names([Path(f"/A/{n}.app") for n in "xyz"], fake_ex)
    # 224 app non devono diventare 224 processi
    assert len([c for c in fake_ex.calls if "mdls" in c[0]]) == 1


def test_mdls_che_fallisce_non_rompe_niente(fake_ex):
    from macdeck.executor import Result as R
    from macdeck import icons as I
    I.reset_display_names()
    fake_ex.replies = {"mdls": R(False, error="boom")}
    assert I.display_names([Path("/A/Preview.app")], fake_ex) == {}


def test_valori_mancanti_vengono_ignorati(fake_ex):
    from macdeck.executor import Result as R
    from macdeck import icons as I
    I.reset_display_names()
    # mdls restituisce "(null)" per i file che non conosce
    fake_ex.replies = {"mdls": R(True, out="Anteprima\0(null)\0")}
    m = I.display_names([Path("/A/Preview.app"), Path("/A/Ignota.app")], fake_ex)
    assert m == {"/A/Preview.app": "Anteprima"}


def test_l_estensione_app_non_finisce_nel_nome(fake_ex):
    from macdeck.executor import Result as R
    from macdeck import icons as I
    I.reset_display_names()
    fake_ex.replies = {"mdls": R(True, out="Anteprima.app\0")}
    assert I.display_names([Path("/A/Preview.app")], fake_ex) == {
        "/A/Preview.app": "Anteprima"}


def test_il_nome_italiano_trova_l_app_vera():
    # Test di sistema: su un Mac in italiano "Anteprima" deve arrivare a
    # Preview.app. Senza la mappa dei nomi visualizzati fallisce, ed e'
    # esattamente il caso che rendeva la configurazione frustrante.
    from macdeck import icons as I
    I.reset_display_names()
    b = I.locate_bundle("Anteprima")
    if b is None:
        pytest.skip("Mac non in italiano, o Anteprima non installata")
    assert b.stem == "Preview"


# ------------------------------------------- specifiche senza schema esplicito


def _vera(im, size=64):
    """Un'icona vera, cioe' diversa dal punto di domanda di ripiego.

    Contare i colori non basta: l'antialiasing del ripiego ne produce a
    sufficienza da farlo passare per un'icona. Si confronta con il ripiego.
    """
    if im is None:
        return False
    return im.tobytes() != icons.fallback(size).tobytes()


def test_un_percorso_a_un_app_funziona_senza_prefisso():
    # Chi configura incolla il percorso e basta. Pretendere "app:" davanti
    # significa fallire in silenzio proprio sul gesto piu' naturale.
    p = icons.locate_bundle("Terminal")
    if p is None:
        pytest.skip("Terminal.app non trovata")
    assert _vera(icons.resolve(str(p), 64))


def test_un_nome_di_app_funziona_senza_prefisso():
    if icons.locate_bundle("Terminal") is None:
        pytest.skip("Terminal.app non trovata")
    assert _vera(icons.resolve("Terminal", 64))


def test_un_file_immagine_funziona_senza_prefisso(tmp_path):
    f = tmp_path / "logo.png"
    Image.new("RGB", (40, 40), "#c04030").save(f)
    assert _vera(icons.resolve(str(f), 64))


def test_il_prefisso_esplicito_continua_a_vincere():
    im = icons.resolve("text:AB", 64)
    assert _vera(im)


def test_uno_schema_ignoto_non_diventa_un_percorso():
    # "pippo:qualcosa" non e' un percorso ne' un'app: deve restare il ripiego,
    # non provare a interpretare "pippo" come cartella.
    im = icons.resolve("pippo:qualcosa", 64)
    assert im is not None


def test_testo_qualunque_resta_leggibile():
    assert _vera(icons.resolve("XY", 64))


def test_un_percorso_inesistente_non_esplode():
    assert icons.resolve("/non/esiste/da/nessuna/parte.app", 64) is not None
