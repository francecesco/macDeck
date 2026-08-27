import pytest

from macdeck.keymap import InvalidKeys, to_applescript


def test_carattere_semplice_con_un_modificatore():
    s = to_applescript("cmd+s")
    assert 'keystroke "s"' in s
    assert "command down" in s
    assert "System Events" in s


def test_modificatori_multipli_in_ordine_canonico():
    s = to_applescript("cmd+shift+4")
    assert 'keystroke "4"' in s
    assert "{command down, shift down}" in s


def test_alias_dei_modificatori():
    assert to_applescript("command+opt+a") == to_applescript("cmd+option+a")
    assert "option down" in to_applescript("alt+a")
    assert "control down" in to_applescript("ctrl+a")


def test_tasto_non_stampabile_usa_key_code():
    s = to_applescript("cmd+return")
    assert "key code 36" in s
    assert "keystroke" not in s


def test_frecce_e_funzione():
    assert "key code 126" in to_applescript("up")
    assert "key code 118" in to_applescript("f4")


def test_senza_modificatori_non_emette_using():
    s = to_applescript("a")
    assert "using" not in s


def test_indirizzamento_a_una_app_specifica():
    s = to_applescript("cmd+r", target="Safari")
    assert 'tell application "Safari" to activate' in s
    assert 'keystroke "r"' in s


def test_le_virgolette_nel_tasto_sono_escapate():
    s = to_applescript('"')
    assert '\\"' in s


@pytest.mark.parametrize("combo", ["", "cmd+", "+s", "cmd+nonesiste", "cmd shift s"])
def test_combinazioni_invalide_sollevano(combo):
    with pytest.raises(InvalidKeys):
        to_applescript(combo)
