from macdeck import cli


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
