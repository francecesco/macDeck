import macdeck.actions as actions_module
from macdeck import actions
from macdeck.executor import Result
from macdeck.executor import Result as R


def test_tipo_ignoto_non_solleva_ma_riporta_errore(fake_ex):
    r = actions.run({"type": "inventato"}, fake_ex)
    assert not r.ok
    assert "ignoto" in r.error
    assert fake_ex.calls == []


def test_handler_che_solleva_diventa_result_fallito(fake_ex):
    @actions.action("esplode_nel_test")
    def _boom(spec, ex):
        raise RuntimeError("scoppiato")

    r = actions.run({"type": "esplode_nel_test"}, fake_ex)
    assert not r.ok
    assert "scoppiato" in r.error


def test_app_per_nome_usa_open_a(fake_ex):
    assert actions.run({"type": "app", "target": "Slack"}, fake_ex).ok
    assert fake_ex.calls[0] == ("/usr/bin/open", "-a", "Slack")


def test_app_per_path_usa_open_a(fake_ex):
    actions.run({"type": "app", "target": "/Applications/DataGrip.app"}, fake_ex)
    assert fake_ex.calls[0] == ("/usr/bin/open", "-a", "/Applications/DataGrip.app")


def test_app_per_bundle_id_usa_open_b(fake_ex):
    actions.run({"type": "app", "target": "com.tinyspeck.slackmacgap"}, fake_ex)
    assert fake_ex.calls[0] == ("/usr/bin/open", "-b", "com.tinyspeck.slackmacgap")


def test_app_senza_target_fallisce_senza_eseguire(fake_ex):
    r = actions.run({"type": "app"}, fake_ex)
    assert not r.ok
    assert "target" in r.error
    assert fake_ex.calls == []


def test_url(fake_ex):
    actions.run({"type": "url", "url": "https://esphome.io"}, fake_ex)
    assert fake_ex.calls[0] == ("/usr/bin/open", "https://esphome.io")


def test_keys_produce_applescript(fake_ex):
    assert actions.run({"type": "keys", "keys": "cmd+shift+4"}, fake_ex).ok
    assert 'keystroke "4"' in fake_ex.scripts[0]


def test_keys_con_destinazione(fake_ex):
    actions.run({"type": "keys", "keys": "cmd+r", "to": "Safari"}, fake_ex)
    assert 'tell application "Safari" to activate' in fake_ex.scripts[0]


def test_keys_invalidi_non_arrivano_a_osascript(fake_ex):
    r = actions.run({"type": "keys", "keys": "cmd+nonesiste"}, fake_ex)
    assert not r.ok
    assert fake_ex.calls == []


def test_text_escapa_le_virgolette(fake_ex):
    actions.run({"type": "text", "text": 'ha detto "no"'}, fake_ex)
    assert '\\"no\\"' in fake_ex.scripts[0]


def test_page_e_sincrono_e_riesce_senza_eseguire_nulla(fake_ex):
    r = actions.run({"type": "page", "to": 1}, fake_ex)
    assert r.ok
    assert fake_ex.calls == []


def test_i_tipi_sincroni_non_sono_marcati_async():
    for t in ("app", "url", "keys", "text", "page", "noop"):
        assert not actions.is_async({"type": t})


def test_known_types_contiene_i_tipi_registrati():
    assert {"app", "keys", "url", "text", "noop", "page"} <= actions.known_types()


# ---------------------------------------------------------------- volume/media


def test_volume_set_valido(fake_ex):
    assert actions.run({"type": "volume", "op": "set", "value": 40}, fake_ex).ok
    assert "set volume output volume 40" in fake_ex.scripts[0]


def test_volume_set_fuori_range_fallisce_senza_eseguire(fake_ex):
    r = actions.run({"type": "volume", "op": "set", "value": 400}, fake_ex)
    assert not r.ok
    assert fake_ex.calls == []


def test_volume_up_clampa_a_100(fake_ex):
    actions.run({"type": "volume", "op": "up", "step": 10}, fake_ex)
    s = fake_ex.scripts[0]
    assert "+ 10" in s
    assert "100" in s


def test_volume_down_usa_delta_negativo(fake_ex):
    actions.run({"type": "volume", "op": "down"}, fake_ex)
    assert "- 6" in fake_ex.scripts[0]


def test_mute_toggle_legge_e_inverte(fake_ex):
    actions.run({"type": "volume", "op": "mute_toggle"}, fake_ex)
    s = fake_ex.scripts[0]
    assert "output muted" in s
    assert "not" in s


def test_volume_op_ignota(fake_ex):
    r = actions.run({"type": "volume", "op": "sideways"}, fake_ex)
    assert not r.ok
    assert fake_ex.calls == []


def test_media_play_pause_prova_i_player_noti(fake_ex):
    assert actions.run({"type": "media", "op": "play_pause"}, fake_ex).ok
    s = fake_ex.scripts[0]
    assert "Spotify" in s and "Music" in s
    assert "playpause" in s


def test_media_next_e_prev(fake_ex):
    actions.run({"type": "media", "op": "next"}, fake_ex)
    assert "next track" in fake_ex.scripts[0]
    fake_ex.calls.clear()
    actions.run({"type": "media", "op": "prev"}, fake_ex)
    assert "previous track" in fake_ex.scripts[0]


def test_media_op_ignota(fake_ex):
    r = actions.run({"type": "media", "op": "scratch"}, fake_ex)
    assert not r.ok
    assert fake_ex.calls == []


# ------------------------------------------------------------------ asincrone


def test_shell_passa_cmd_e_cwd(fake_ex):
    actions.run({"type": "shell", "cmd": "ls", "cwd": "/tmp"}, fake_ex)
    joined = " ".join(fake_ex.calls[0])
    assert "ls" in joined and "/tmp" in joined


def test_shell_senza_cmd(fake_ex):
    r = actions.run({"type": "shell"}, fake_ex)
    assert not r.ok
    assert fake_ex.calls == []


def test_applescript_grezzo(fake_ex):
    actions.run({"type": "applescript", "script": "return 1"}, fake_ex)
    assert fake_ex.scripts[0] == "return 1"


def test_shortcut_invoca_il_binario_di_sistema(fake_ex):
    actions.run({"type": "shortcut", "name": "Buonanotte"}, fake_ex)
    assert fake_ex.calls[0] == ("/usr/bin/shortcuts", "run", "Buonanotte")


def test_delay_dorme_i_millisecondi_richiesti(monkeypatch, fake_ex):
    dormite = []
    monkeypatch.setattr(actions_module.time, "sleep", dormite.append)
    assert actions.run({"type": "delay", "ms": 250}, fake_ex).ok
    assert dormite == [0.25]


def test_delay_e_limitato_a_dieci_secondi(monkeypatch, fake_ex):
    dormite = []
    monkeypatch.setattr(actions_module.time, "sleep", dormite.append)
    actions.run({"type": "delay", "ms": 999_999}, fake_ex)
    assert dormite == [10.0]


def test_sequence_esegue_in_ordine(monkeypatch, fake_ex):
    monkeypatch.setattr(actions_module.time, "sleep", lambda _s: None)
    spec = {
        "type": "sequence",
        "steps": [
            {"type": "keys", "keys": "cmd+s"},
            {"type": "delay", "ms": 100},
            {"type": "app", "target": "Slack"},
        ],
    }
    assert actions.run(spec, fake_ex).ok
    assert 'keystroke "s"' in fake_ex.scripts[0]
    assert fake_ex.calls[-1] == ("/usr/bin/open", "-a", "Slack")


def test_sequence_si_ferma_al_primo_errore_e_dice_quale(fake_ex):
    fake_ex.replies = {"open -a Rotta": R(False, error="non trovata")}
    spec = {
        "type": "sequence",
        "steps": [
            {"type": "app", "target": "Rotta"},
            {"type": "app", "target": "MaiRaggiunta"},
        ],
    }
    r = actions.run(spec, fake_ex)
    assert not r.ok
    assert "passo 1" in r.error
    assert "non trovata" in r.error
    assert len(fake_ex.calls) == 1


def test_sequence_annidata(fake_ex):
    spec = {
        "type": "sequence",
        "steps": [
            {"type": "sequence", "steps": [{"type": "app", "target": "A"}]},
            {"type": "app", "target": "B"},
        ],
    }
    assert actions.run(spec, fake_ex).ok
    assert len(fake_ex.calls) == 2


def test_sequence_vuota_riesce(fake_ex):
    assert actions.run({"type": "sequence", "steps": []}, fake_ex).ok


def test_classificazione_sincrono_asincrono():
    for t in ("shell", "applescript", "shortcut", "sequence"):
        assert actions.is_async({"type": t}), t
    for t in ("app", "keys", "text", "url", "volume", "media", "page", "delay"):
        assert not actions.is_async({"type": t}), t


def test_media_shuffle_e_repeat_hanno_un_comando_per_player(fake_ex):
    actions.run({"type": "media", "op": "shuffle_toggle"}, fake_ex)
    actions.run({"type": "media", "op": "repeat_toggle"}, fake_ex)
    s1, s2 = fake_ex.scripts[-2:]
    assert "set shuffling to not shuffling" in s1 and "shuffle enabled" in s1
    assert "set repeating to not repeating" in s2 and "song repeat" in s2
