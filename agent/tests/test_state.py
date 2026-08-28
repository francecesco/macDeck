import time

from macdeck.executor import Result as R
from macdeck.state import EMPTY_SNAPSHOT, StateProbe


def _probe(fake_ex, **kw):
    return StateProbe(fake_ex, **kw)


# ------------------------------------------------------- lettura delle sonde


def test_volume_viene_parsato(fake_ex):
    fake_ex.replies = {"output volume of": R(True, out="62\nfalse\n")}
    s = _probe(fake_ex).refresh()
    assert s["volume"]["level"] == 62
    assert s["volume"]["muted"] is False


def test_volume_muto(fake_ex):
    fake_ex.replies = {"output volume of": R(True, out="0\ntrue\n")}
    assert _probe(fake_ex).refresh()["volume"]["muted"] is True


def test_volume_illeggibile_non_rompe_lo_snapshot(fake_ex):
    fake_ex.replies = {"output volume of": R(False, error="boom")}
    s = _probe(fake_ex).refresh()
    assert s["volume"]["level"] is None
    assert s["volume"]["muted"] is None


def test_media_in_riproduzione(fake_ex):
    fake_ex.replies = {
        "running_apps": R(True, out="Spotify\ntrue\nAnagrafe\nMarlene Kuntz\n")
    }
    m = _probe(fake_ex).refresh()["media"]
    assert m["app"] == "Spotify"
    assert m["playing"] is True
    assert m["title"] == "Anagrafe"
    assert m["artist"] == "Marlene Kuntz"


def test_nessun_player_attivo(fake_ex):
    fake_ex.replies = {"running_apps": R(True, out="none\n")}
    m = _probe(fake_ex).refresh()["media"]
    assert m["app"] is None
    assert m["playing"] is False
    assert m["title"] is None


def test_sistema_ha_numeri_plausibili(fake_ex):
    s = _probe(fake_ex).refresh()["system"]
    assert 0 <= s["cpu"] <= 100
    assert 0 <= s["ram"] <= 100
    assert s["battery"] is None or 0 <= s["battery"] <= 100


def test_accessibility_ok_quando_il_probe_riesce(fake_ex):
    fake_ex.replies = {"first process": R(True, out="loginwindow\n")}
    assert _probe(fake_ex).refresh()["accessibility_ok"] is True


def test_accessibility_ko_quando_il_probe_fallisce(fake_ex):
    fake_ex.replies = {"first process": R(False, error="not allowed")}
    assert _probe(fake_ex).refresh()["accessibility_ok"] is False


def test_accessibility_non_viene_riinterrogata_a_ogni_refresh(fake_ex):
    p = _probe(fake_ex, accessibility_interval=999)
    p.refresh()
    prima = sum("first process" in " ".join(c) for c in fake_ex.calls)
    p.refresh()
    dopo = sum("first process" in " ".join(c) for c in fake_ex.calls)
    assert prima == dopo == 1


# ------------------------------------------- la proprieta' che salva il display


def test_snapshot_non_interroga_mai_il_mac(fake_ex):
    """Il cuore della correzione.

    Se snapshot() interrogasse il Mac, /state costerebbe 1-2 s (tre osascript)
    e il loop di ESPHome resterebbe bloccato per tutto quel tempo a ogni poll,
    fino a far scattare il watchdog del display. Deve essere una lettura di
    memoria e nient'altro.
    """
    p = _probe(fake_ex)
    p.refresh()
    fake_ex.calls.clear()
    for _ in range(20):
        p.snapshot()
    assert fake_ex.calls == []


def test_snapshot_prima_di_qualunque_refresh_e_valido_e_non_blocca(fake_ex):
    p = _probe(fake_ex)
    s = p.snapshot()
    assert fake_ex.calls == []
    for chiave in EMPTY_SNAPSHOT:
        assert chiave in s
    assert s["volume"]["level"] is None
    assert s["last_error"] is None


def test_snapshot_e_veloce(fake_ex):
    p = _probe(fake_ex)
    p.refresh()
    t = time.perf_counter()
    for _ in range(200):
        p.snapshot()
    assert (time.perf_counter() - t) < 0.05


def test_snapshot_restituisce_una_copia(fake_ex):
    p = _probe(fake_ex)
    p.refresh()
    s = p.snapshot()
    s["volume"] = "manomesso"
    assert p.snapshot()["volume"] != "manomesso"


def test_note_error_compare_e_si_pulisce(fake_ex):
    p = _probe(fake_ex)
    p.note_error("shell: exit 1")
    assert p.snapshot()["last_error"] == "shell: exit 1"
    p.note_error(None)
    assert p.snapshot()["last_error"] is None


# ------------------------------------------------------------ ciclo di sfondo


def test_start_fa_subito_una_lettura_e_avvia_il_thread(fake_ex):
    fake_ex.replies = {"output volume of": R(True, out="55\nfalse\n")}
    p = _probe(fake_ex, interval=0.05)
    try:
        p.start()
        assert p.snapshot()["volume"]["level"] == 55
        assert p._thread is not None and p._thread.is_alive()
    finally:
        p.stop()


def test_il_thread_continua_ad_aggiornare(fake_ex):
    p = _probe(fake_ex, interval=0.02)
    try:
        p.start()
        quante = len(fake_ex.calls)
        time.sleep(0.2)
        assert len(fake_ex.calls) > quante
    finally:
        p.stop()


def test_stop_ferma_il_thread(fake_ex):
    p = _probe(fake_ex, interval=0.02)
    p.start()
    p.stop()
    assert p._thread is None
    quante = len(fake_ex.calls)
    time.sleep(0.15)
    assert len(fake_ex.calls) == quante


def test_start_e_idempotente(fake_ex):
    p = _probe(fake_ex, interval=0.05)
    try:
        p.start()
        t1 = p._thread
        p.start()
        assert p._thread is t1
    finally:
        p.stop()


def test_una_sonda_che_esplode_non_uccide_il_thread(fake_ex, monkeypatch):
    p = _probe(fake_ex, interval=0.02)
    boom = {"n": 0}

    def esplode():
        boom["n"] += 1
        raise RuntimeError("sonda rotta")

    monkeypatch.setattr(p, "_volume", esplode)
    try:
        p.start()
    except RuntimeError:
        pass
    p._stop.clear()
    p._thread = None
    p._thread = __import__("threading").Thread(target=p._loop, daemon=True)
    p._thread.start()
    time.sleep(0.15)
    assert p._thread.is_alive()
    assert boom["n"] > 1
    p.stop()
