from macdeck.executor import Result as R
from macdeck.state import StateProbe


def _probe(fake_ex, **kw):
    return StateProbe(fake_ex, **kw)


def test_volume_viene_parsato(fake_ex):
    fake_ex.replies = {"output volume of": R(True, out="62\nfalse\n")}
    s = _probe(fake_ex).snapshot()
    assert s["volume"]["level"] == 62
    assert s["volume"]["muted"] is False


def test_volume_muto(fake_ex):
    fake_ex.replies = {"output volume of": R(True, out="0\ntrue\n")}
    assert _probe(fake_ex).snapshot()["volume"]["muted"] is True


def test_volume_illeggibile_non_rompe_lo_snapshot(fake_ex):
    fake_ex.replies = {"output volume of": R(False, error="boom")}
    s = _probe(fake_ex).snapshot()
    assert s["volume"]["level"] is None
    assert s["volume"]["muted"] is None


def test_media_in_riproduzione(fake_ex):
    fake_ex.replies = {
        "running_apps": R(True, out="Spotify\ntrue\nAnagrafe\nMarlene Kuntz\n")
    }
    m = _probe(fake_ex).snapshot()["media"]
    assert m["app"] == "Spotify"
    assert m["playing"] is True
    assert m["title"] == "Anagrafe"
    assert m["artist"] == "Marlene Kuntz"


def test_nessun_player_attivo(fake_ex):
    fake_ex.replies = {"running_apps": R(True, out="none\n")}
    m = _probe(fake_ex).snapshot()["media"]
    assert m["app"] is None
    assert m["playing"] is False
    assert m["title"] is None


def test_sistema_ha_numeri_plausibili(fake_ex):
    s = _probe(fake_ex).snapshot()["system"]
    assert 0 <= s["cpu"] <= 100
    assert 0 <= s["ram"] <= 100
    assert s["battery"] is None or 0 <= s["battery"] <= 100


def test_accessibility_ok_quando_il_probe_riesce(fake_ex):
    fake_ex.replies = {"first process": R(True, out="loginwindow\n")}
    assert _probe(fake_ex).snapshot()["accessibility_ok"] is True


def test_accessibility_ko_quando_il_probe_fallisce(fake_ex):
    fake_ex.replies = {"first process": R(False, error="not allowed")}
    assert _probe(fake_ex).snapshot()["accessibility_ok"] is False


def test_la_cache_evita_di_riinterrogare_entro_il_ttl(fake_ex):
    p = _probe(fake_ex, ttl=60.0)
    p.snapshot()
    quante = len(fake_ex.calls)
    p.snapshot()
    assert len(fake_ex.calls) == quante


def test_ttl_scaduto_riinterroga(fake_ex):
    p = _probe(fake_ex, ttl=0.0)
    p.snapshot()
    quante = len(fake_ex.calls)
    p.snapshot()
    assert len(fake_ex.calls) > quante


def test_note_error_compare_e_si_pulisce(fake_ex):
    p = _probe(fake_ex, ttl=0.0)
    p.note_error("shell: exit 1")
    assert p.snapshot()["last_error"] == "shell: exit 1"
    p.note_error(None)
    assert p.snapshot()["last_error"] is None
