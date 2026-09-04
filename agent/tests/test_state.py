import time

from macdeck import sources as S
from macdeck.executor import Result as R
from macdeck.state import EMPTY_SNAPSHOT, StateProbe


def _registro(*fns):
    """Un registro isolato: i test non devono sporcare quello globale."""
    reg = {}
    for name, fn, kw in fns:
        reg[name] = S.Source(name=name, fn=fn, empty=dict(kw.get("empty", {"v": None})),
                             every=float(kw.get("every", 1.0)),
                             app=tuple(a.lower() for a in kw.get("app", ())))
    return reg


class Orologio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


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
    # every=0: le sonde di base (cadenza 1-2 s) non farebbero in tempo a
    # scadere in 0.2 s, quindi qui si usa una sonda con cadenza propria per
    # verificare che il thread continui davvero a girare.
    chiamate = {"n": 0}

    def sonda(ex, ctx):
        chiamate["n"] += 1
        return {"v": chiamate["n"]}

    reg = _registro(("x", sonda, {"every": 0.0}))
    p = StateProbe(fake_ex, interval=0.02, sources=reg)
    try:
        p.start()
        quante = chiamate["n"]
        time.sleep(0.2)
        assert chiamate["n"] > quante
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


def test_una_sonda_che_esplode_non_uccide_il_thread(fake_ex):
    boom = {"n": 0}

    def esplode(ex, ctx):
        boom["n"] += 1
        raise RuntimeError("sonda rotta")

    # every=0: deve girare a OGNI giro del thread, altrimenti con la cadenza
    # di default (1 s) esploderebbe una volta sola in 0.15 s.
    reg = _registro(("rotta", esplode, {"every": 0.0}))
    p = StateProbe(fake_ex, interval=0.02, sources=reg)
    p.start()
    time.sleep(0.15)
    assert p._thread.is_alive()
    assert boom["n"] > 1
    p.stop()


# ---------------------------- diniego vero contro sonda che non riesce a girare


def test_un_diniego_vero_viene_riportato(fake_ex):
    fake_ex.replies = {"first process": R(
        False, error="System Events got an error: ... (-1743)")}
    assert _probe(fake_ex).refresh()["accessibility_ok"] is False


def test_un_timeout_non_e_un_diniego(fake_ex):
    """Il difetto che faceva comparire l'allarme rosso sul display mentre il
    Mac compilava: con load average 40 osascript sfora il timeout, e trattarlo
    come diniego e' un falso allarme."""
    p = _probe(fake_ex, accessibility_interval=0)
    fake_ex.replies = {"first process": R(True, out="loginwindow\n")}
    assert p.refresh()["accessibility_ok"] is True
    fake_ex.replies = {"first process": R(False, error="timeout dopo 3.0s: osascript")}
    assert p.refresh()["accessibility_ok"] is True      # tiene l'ultimo noto


def test_esito_incerto_senza_storia_e_ottimista(fake_ex):
    fake_ex.replies = {"first process": R(False, error="qualcosa di strano")}
    assert _probe(fake_ex).refresh()["accessibility_ok"] is True


def test_dopo_un_diniego_un_successo_lo_annulla(fake_ex):
    p = _probe(fake_ex, accessibility_interval=0)
    fake_ex.replies = {"first process": R(False, error="not authorized")}
    assert p.refresh()["accessibility_ok"] is False
    fake_ex.replies = {"first process": R(True, out="Finder\n")}
    assert p.refresh()["accessibility_ok"] is True


# ------------------------------------------------- registro delle sonde


def test_una_sonda_con_app_non_gira_se_lapp_e_chiusa(fake_ex):
    chiamate = {"n": 0}

    def sonda(ex, ctx):
        chiamate["n"] += 1
        return {"v": 1}

    reg = _registro(("mail", sonda, {"app": ("com.apple.mail",)}))
    snap = StateProbe(fake_ex, sources=reg).refresh()
    assert chiamate["n"] == 0
    assert snap["mail"] == {"v": None}


def test_una_sonda_con_app_gira_se_lapp_e_aperta(fake_ex):
    fake_ex.replies = {"lsappinfo list": R(True, out=' 1) "Mail" ASN:0x0-0x1:\n    bundleID="com.apple.mail"\n')}

    def sonda(ex, ctx):
        assert "com.apple.mail" in ctx.running
        return {"v": 7}

    reg = _registro(("mail", sonda, {"app": ("com.apple.mail",)}))
    assert StateProbe(fake_ex, sources=reg).refresh()["mail"] == {"v": 7}


def test_la_cadenza_viene_rispettata_solo_con_due_only(fake_ex):
    chiamate = {"n": 0}

    def sonda(ex, ctx):
        chiamate["n"] += 1
        return {"v": chiamate["n"]}

    clock = Orologio()
    reg = _registro(("lenta", sonda, {"every": 5.0}))
    p = StateProbe(fake_ex, sources=reg, clock=clock)
    p.refresh(due_only=True)
    clock.t += 1.0
    p.refresh(due_only=True)
    assert chiamate["n"] == 1            # non e' ancora il suo turno
    clock.t += 4.5
    p.refresh(due_only=True)
    assert chiamate["n"] == 2
    p.refresh()                          # senza due_only gira sempre
    assert chiamate["n"] == 3


def test_un_fallimento_tiene_lultimo_valore_noto(fake_ex):
    esiti = iter([{"v": 3}, None, None])
    reg = _registro(("x", lambda ex, ctx: next(esiti), {}))
    p = StateProbe(fake_ex, sources=reg)
    assert p.refresh()["x"] == {"v": 3}
    assert p.refresh()["x"] == {"v": 3}
    assert p.refresh()["x"] == {"v": 3}


def test_tre_fallimenti_consecutivi_riportano_al_vuoto(fake_ex):
    esiti = iter([{"v": 3}, None, None, None])
    reg = _registro(("x", lambda ex, ctx: next(esiti), {}))
    p = StateProbe(fake_ex, sources=reg)
    for _ in range(4):
        snap = p.refresh()
    assert snap["x"] == {"v": None}


def test_uneccezione_nella_sonda_vale_come_fallimento(fake_ex):
    def esplode(ex, ctx):
        raise RuntimeError("rotta")

    reg = _registro(("x", esplode, {}))
    snap = StateProbe(fake_ex, sources=reg).refresh()
    assert snap["x"] == {"v": None}


def test_la_sonda_riceve_il_proprio_ultimo_valore(fake_ex):
    visti = []

    def sonda(ex, ctx):
        visti.append(dict(ctx.last))
        return {"v": len(visti)}

    reg = _registro(("x", sonda, {}))
    p = StateProbe(fake_ex, sources=reg)
    p.refresh()
    p.refresh()
    assert visti[0] == {"v": None}
    assert visti[1] == {"v": 1}


def test_il_valore_restituito_si_fonde_sul_vuoto(fake_ex):
    reg = _registro(("x", lambda ex, ctx: {"a": 1}, {"empty": {"a": None, "b": None}}))
    assert StateProbe(fake_ex, sources=reg).refresh()["x"] == {"a": 1, "b": None}
