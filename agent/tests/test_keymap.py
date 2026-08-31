import pytest

from macdeck.keymap import InvalidKeys, to_applescript, from_event, KEY_CODES


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


def test_un_tasto_funzione_con_modificatori():
    # f4 e' il key code 118, gli stessi che NSEvent consegna
    assert from_event(118, ["cmd", "shift"]) == "cmd+shift+f4"


def test_l_ordine_dei_modificatori_e_canonico():
    # in ingresso disordinati, in uscita sempre cmd, ctrl, opt, shift
    assert from_event(118, ["shift", "opt", "ctrl", "cmd"]) == \
        "cmd+ctrl+opt+shift+f4"


def test_i_nomi_lunghi_diventano_quelli_corti():
    assert from_event(118, ["command", "option"]) == "cmd+opt+f4"


def test_un_tasto_stampabile_arriva_dai_caratteri():
    # "4" non sta in KEY_CODES: il codice da solo non basta
    assert from_event(21, ["cmd"], chars="4") == "cmd+4"


def test_senza_modificatori():
    assert from_event(53) == "escape"


def test_un_tasto_ignoto_e_un_errore_non_una_stringa_strana():
    with pytest.raises(InvalidKeys):
        from_event(9999, ["cmd"])


def test_un_modificatore_ignoto_e_un_errore():
    with pytest.raises(InvalidKeys):
        from_event(118, ["hyper"])


def test_quello_che_esce_da_from_event_rientra_in_to_applescript():
    # la proprieta' che conta: il rovescio produce solo combinazioni che il
    # dritto sa leggere. Se un giorno le due tabelle divergono, questo casca.
    for codice in KEY_CODES.values():
        combo = from_event(codice, ["cmd", "shift"])
        to_applescript(combo)      # non deve sollevare
