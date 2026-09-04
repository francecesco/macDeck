import json
import os
import time

from macdeck import cli, paths


def test_render_plist_contiene_label_interprete_e_keepalive():
    xml = cli.render_plist("/percorso/python", "/percorso/agent")
    assert cli.PLIST_LABEL in xml
    assert "/percorso/python" in xml
    assert "KeepAlive" in xml
    assert xml.lstrip().startswith("<?xml")


def test_build_serve_app_costruisce_tutto(tmp_path):
    app, token = cli.build_serve_app(root=tmp_path)
    assert len(token) == 32
    percorsi = {r.path for r in app.routes}
    assert {"/layout", "/state", "/press", "/"} <= percorsi


def test_token_stampato_e_quello_su_disco(tmp_path, capsys):
    assert cli.main(["token", "--root", str(tmp_path)]) == 0
    stampato = capsys.readouterr().out.strip()
    from macdeck import paths
    assert stampato == paths.load_or_create_token(root=tmp_path)


def test_doctor_riporta_esito_senza_sollevare(tmp_path, capsys):
    codice = cli.main(["doctor", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert codice in (0, 1)
    assert "Accessibilità" in out
    assert "layout.yaml" in out


def test_comando_ignoto_da_errore(capsys):
    assert cli.main(["inventato"]) != 0


# ------------------------------------------------------ annuncio al deck


def test_senza_avvio_sonde_l_annuncio_resta_fermo(tmp_path):
    # I test non devono mettersi a cercare deck sulla rete di chi compila.
    app, _ = cli.build_serve_app(root=tmp_path)
    ann = app.state.announcer
    assert ann is not None
    assert ann._thread is None


def test_doctor_riporta_lo_stato_dell_annuncio(tmp_path, capsys):
    cli.main(["doctor", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "deck" in out.lower()


def test_pair_senza_cavo_lo_dice_e_non_esplode(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("pathlib.Path.glob", lambda self, pat: iter([]))
    rc = cli.main(["pair", "--usb", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "cavo dati" in out


def test_pair_e_registrato_fra_i_comandi(capsys):
    rc = cli.main(["--help"])
    assert "pair" in capsys.readouterr().out


def test_pair_via_wifi_dice_che_il_portale_non_esiste_piu(tmp_path, capsys):
    # Il firmware non alza piu' l'access point di ripiego ne' il portale:
    # una rete insegnata da li' CANCELLA casa e ufficio (set_sta ->
    # clear_sta), ed e' il difetto che ha tenuto il deck fuori rete per un
    # giorno. Un comando che non puo' piu' riuscire deve dirlo subito,
    # invece di far cercare per un'ora un access point che non c'e'.
    rc = cli.main(["pair", "--ssid", "Casa", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "--usb" in out
    assert "portale" in out.lower()


# ---------------------------------------------------- ponte con Claude Code


def test_ponte_claude_assente_spiega_la_riga_da_aggiungere(tmp_path):
    ok, msg = cli.claude_bridge_status(tmp_path)
    assert ok is False
    assert "statusLine" in msg and "macdeck/claude" in msg


def test_ponte_claude_fresco_e_ok(tmp_path):
    (paths.claude_dir(tmp_path) / "s.json").write_text(json.dumps({"session_id": "s"}))
    ok, msg = cli.claude_bridge_status(tmp_path)
    assert ok is True and "s.json" in msg


def test_ponte_claude_stantio_non_e_ok(tmp_path):
    f = paths.claude_dir(tmp_path) / "s.json"
    f.write_text("{}")
    t = time.time() - 3 * 3600
    os.utime(f, (t, t))
    ok, msg = cli.claude_bridge_status(tmp_path)
    assert ok is False and "ore" in msg


def test_ponte_claude_file_sparito_fra_glob_e_stat_non_esplode(tmp_path, monkeypatch):
    # Il file trovato dal glob puo' sparire (rotazione, pulizia) prima dello
    # stat(): non deve uscire un OSError non gestito da `doctor`.
    from macdeck import sources
    fantasma = paths.claude_dir(tmp_path) / "sparito.json"
    monkeypatch.setattr(sources, "newest_claude_file", lambda d: fantasma)
    ok, msg = cli.claude_bridge_status(tmp_path)
    assert ok is False and "statusLine" in msg and "macdeck/claude" in msg


def test_pagine_con_app_assente_segnala_solo_le_introvabili(monkeypatch):
    from macdeck import layout as L
    monkeypatch.setattr(cli.icons, "locate_bundle",
                        lambda t: "/x/Spotify.app" if "spotify" in t else None)
    layout = L.validate({"pages": [
        {"name": "Griglia", "slots": []},
        {"name": "Spotify", "app": "com.spotify.client", "slots": []},
        {"name": "Boh", "app": ["Inesistente"], "slots": []},
    ]})
    assert cli.pagine_con_app_assente(layout) == [("Boh", "inesistente")]
