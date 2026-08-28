"""Il Mac si presenta al deck.

Il deck non sa dove sia il Mac: l'indirizzo cambia con la rete. Invece di
inseguirlo dal firmware, e' il Mac a cercare il deck (che si annuncia da
solo via Bonjour) e a dirgli dove trovarsi. Qui si prova l'orchestrazione;
zeroconf e l'API ESPHome sono iniettate, quindi i test girano senza rete.
"""

import threading
import time

import pytest

from macdeck.discovery import (
    Announcer,
    local_ip_towards,
    needs_update,
    read_secret,
)


# ------------------------------------------------------- pezzi indipendenti


def test_ip_locale_verso_il_loopback():
    assert local_ip_towards("127.0.0.1") == "127.0.0.1"


def test_ip_locale_verso_un_host_impossibile_non_esplode():
    assert local_ip_towards("") is None


def test_ip_locale_verso_un_indirizzo_esterno_non_e_il_loopback():
    ip = local_ip_towards("192.0.2.1")  # rete di documentazione, mai raggiunta
    # Senza rete puo' non esserci risposta, ma se c'e' non deve essere il
    # loopback: al deck servirebbe un indirizzo che significa "me stesso".
    assert ip is None or not ip.startswith("127.")


@pytest.mark.parametrize(
    "attuale,voluto,atteso",
    [
        ("192.168.1.28", "192.168.1.28", False),
        ("192.168.1.28", "10.0.0.5", True),
        ("", "10.0.0.5", True),
        ("  10.0.0.5  ", "10.0.0.5", False),  # spazi: non e' un cambio
        ("10.0.0.5", "", False),              # senza IP non si scrive nulla
    ],
)
def test_serve_aggiornare(attuale, voluto, atteso):
    assert needs_update(attuale, voluto) is atteso


def test_legge_un_secret(tmp_path):
    f = tmp_path / "secrets.yaml"
    f.write_text("wifi_ssid: casa\napi_key: \"abc123=\"\n")
    assert read_secret(f, "api_key") == "abc123="


def test_secret_mancante_e_none(tmp_path):
    f = tmp_path / "secrets.yaml"
    f.write_text("wifi_ssid: casa\n")
    assert read_secret(f, "api_key") is None
    assert read_secret(tmp_path / "manca.yaml", "api_key") is None


# ------------------------------------------------------------- orchestrazione


def _ann(**kw):
    kw.setdefault("psk", "chiave")
    kw.setdefault("finder", lambda timeout: "192.168.1.200")
    kw.setdefault("reader", lambda ip, psk: "192.168.1.28")
    kw.setdefault("writer", lambda ip, psk, valore: True)
    kw.setdefault("local_ip", lambda host: "192.168.1.28")
    return Announcer(**kw)


def test_se_l_indirizzo_e_gia_giusto_non_scrive():
    scritture = []
    a = _ann(writer=lambda ip, psk, v: scritture.append(v) or True)
    a.tick()
    assert scritture == []
    assert a.status()["deck"] == "192.168.1.200"


def test_se_l_indirizzo_e_cambiato_lo_scrive():
    scritture = []
    a = _ann(
        local_ip=lambda host: "10.0.0.7",
        writer=lambda ip, psk, v: scritture.append((ip, v)) or True,
    )
    a.tick()
    assert scritture == [("192.168.1.200", "10.0.0.7")]
    assert a.status()["annunciato"] == "10.0.0.7"


def test_deck_non_trovato_non_e_un_errore_fatale():
    a = _ann(finder=lambda timeout: None)
    a.tick()
    assert a.status()["deck"] is None
    assert a.status()["ultimo_errore"] is None


def test_un_finder_che_esplode_non_ferma_il_ciclo():
    def boom(timeout):
        raise RuntimeError("zeroconf giu'")

    a = _ann(finder=boom)
    a.tick()  # non deve sollevare
    assert "zeroconf giu'" in a.status()["ultimo_errore"]


def test_uno_writer_che_esplode_non_ferma_il_ciclo():
    def boom(ip, psk, v):
        raise RuntimeError("api rifiutata")

    a = _ann(local_ip=lambda host: "10.0.0.7", writer=boom)
    a.tick()
    assert "api rifiutata" in a.status()["ultimo_errore"]
    assert a.status()["annunciato"] is None


def test_senza_ip_locale_non_scrive_niente():
    scritture = []
    a = _ann(local_ip=lambda host: None,
             writer=lambda ip, psk, v: scritture.append(v) or True)
    a.tick()
    assert scritture == []


def test_senza_chiave_di_cifratura_non_ci_prova_nemmeno():
    scritture = []
    a = _ann(psk=None, writer=lambda ip, psk, v: scritture.append(v) or True)
    a.tick()
    assert scritture == []
    assert "chiave" in a.status()["ultimo_errore"].lower()


def test_un_errore_risolto_viene_dimenticato():
    stato = {"rompi": True}

    def ballerino(timeout):
        if stato["rompi"]:
            raise RuntimeError("giu'")
        return "192.168.1.200"

    a = _ann(finder=ballerino)
    a.tick()
    assert a.status()["ultimo_errore"] is not None
    stato["rompi"] = False
    a.tick()
    assert a.status()["ultimo_errore"] is None


def test_status_e_leggibile_senza_aver_mai_girato():
    s = _ann().status()
    assert s["deck"] is None and s["annunciato"] is None


# --------------------------------------------------------------------- thread


def test_il_thread_gira_e_si_ferma():
    giri = threading.Event()
    a = _ann(interval=0.01, finder=lambda t: giri.set() or "192.168.1.200")
    a.start()
    try:
        assert giri.wait(2.0), "il thread non ha mai chiamato il finder"
    finally:
        a.stop()
    assert a._thread is None or not a._thread.is_alive()


def test_start_due_volte_non_crea_due_thread():
    a = _ann(interval=0.01)
    a.start()
    primo = a._thread
    a.start()
    try:
        assert a._thread is primo
    finally:
        a.stop()


def test_stop_senza_start_non_esplode():
    _ann().stop()


def test_il_thread_sopravvive_a_un_giro_fallito():
    conteggio = {"n": 0}

    def alterno(timeout):
        conteggio["n"] += 1
        if conteggio["n"] == 1:
            raise RuntimeError("primo giro giu'")
        return "192.168.1.200"

    a = _ann(interval=0.01, finder=alterno)
    a.start()
    try:
        scadenza = time.monotonic() + 2.0
        while conteggio["n"] < 3 and time.monotonic() < scadenza:
            time.sleep(0.01)
    finally:
        a.stop()
    assert conteggio["n"] >= 3, "il thread si e' fermato al primo errore"


# ------------------------------------------------------------------ firewall


def test_firewall_spento_non_e_un_problema(fake_ex):
    from macdeck.discovery import firewall_state
    fake_ex.replies = {"socketfilterfw": __import__(
        "macdeck.executor", fromlist=["Result"]).Result(
            True, out="Firewall is disabled. (State = 0)\n")}
    assert firewall_state(fake_ex) is False


def test_firewall_acceso_va_segnalato(fake_ex):
    from macdeck.discovery import firewall_state
    from macdeck.executor import Result
    fake_ex.replies = {"socketfilterfw": Result(
        True, out="Firewall is enabled. (State = 1)\n")}
    assert firewall_state(fake_ex) is True


def test_firewall_illeggibile_non_inventa_una_risposta(fake_ex):
    from macdeck.discovery import firewall_state
    from macdeck.executor import Result
    fake_ex.replies = {"socketfilterfw": Result(False, error="boom")}
    assert firewall_state(fake_ex) is None


# ---------------------------------------------- la chiusura della connessione


class _FintoClient:
    """Un deck che accetta il comando ma poi non riesce a salutare."""

    def __init__(self, *, disconnect_appende=False):
        self.disconnect_appende = disconnect_appende
        self.scritto = None

    async def connect(self, login=False):
        return None

    async def list_entities_services(self):
        class E:
            name = "Indirizzo agent"
            key = 42
        return [E()], []

    def text_command(self, key, valore):
        self.scritto = (key, valore)

    async def disconnect(self, force=False):
        if self.disconnect_appende:
            import asyncio
            await asyncio.sleep(30)


def test_una_chiusura_che_si_appende_non_annulla_la_scrittura():
    # Scrivere l'indirizzo fa ripartire il deck verso il nuovo host: mentre
    # ci prova non risponde all'API, e la chiusura ordinata resta appesa.
    # Il lavoro pero' e' gia' stato fatto, e va riportato come riuscito.
    from macdeck.discovery import write_agent_host
    finto = _FintoClient(disconnect_appende=True)
    assert write_agent_host("1.2.3.4", "psk", "10.0.0.7",
                            client_factory=lambda ip, psk: finto) is True
    assert finto.scritto == (42, "10.0.0.7")


def test_scrittura_normale():
    from macdeck.discovery import write_agent_host
    finto = _FintoClient()
    assert write_agent_host("1.2.3.4", "psk", "10.0.0.7",
                            client_factory=lambda ip, psk: finto) is True
