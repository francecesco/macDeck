from macdeck.executor import Executor


def test_run_riuscito_cattura_stdout():
    r = Executor().run(["/bin/echo", "ciao"])
    assert r.ok
    assert r.out.strip() == "ciao"
    assert r.error is None


def test_run_fallito_riporta_stderr_senza_eccezione():
    r = Executor().run(["/bin/sh", "-c", "echo brutto >&2; exit 3"])
    assert not r.ok
    assert "brutto" in r.error


def test_comando_inesistente_non_solleva():
    r = Executor().run(["/bin/comando-che-non-esiste"])
    assert not r.ok
    assert r.error


def test_timeout_riportato_come_errore():
    r = Executor().run(["/bin/sleep", "5"], timeout=0.2)
    assert not r.ok
    assert "timeout" in r.error.lower()


def test_osascript_valuta_espressione():
    r = Executor().osascript("return 2 + 3")
    assert r.ok
    assert r.out.strip() == "5"


def test_shell_rispetta_cwd(tmp_path):
    (tmp_path / "segnale.txt").write_text("x")
    r = Executor().shell("ls", cwd=str(tmp_path))
    assert r.ok
    assert "segnale.txt" in r.out
