"""Insegnare al deck una rete che si scopre solo sul momento.

Fuori casa il Mac si attacca al WiFi del posto e il deck non lo conosce.
Il portale del deck accetta gia' credenziali su /wifisave; qui c'e' chi lo
guida al posto nostro, e soprattutto chi garantisce che il Mac torni sulla
sua rete anche quando qualcosa va storto a meta' strada.
"""

import pytest

from conftest import FakeExecutor

from macdeck.executor import Result as R
from macdeck.pairing import (
    AP_IP,
    AP_SSID,
    REDACTED,
    current_ssid,
    improv_packet,
    join,
    pair_over_wifi,
    wifi_password,
)

SUMMARY = """<dictionary> {
  BSSID : aa:bb:cc:dd:ee:ff
  SSID : Rete Del Cliente
  Security : WPA2 Personal
}"""


# ------------------------------------------------------------ lettura rete


def test_legge_l_ssid_anche_con_gli_spazi(fake_ex):
    fake_ex.replies = {"getsummary": R(True, out=SUMMARY)}
    assert current_ssid(fake_ex) == "Rete Del Cliente"


def test_senza_wifi_non_inventa_un_nome(fake_ex):
    fake_ex.replies = {"getsummary": R(True, out="<dictionary> {\n}")}
    assert current_ssid(fake_ex) is None


def test_comando_fallito_non_e_un_ssid(fake_ex):
    fake_ex.replies = {"getsummary": R(False, error="boom")}
    assert current_ssid(fake_ex) is None


def test_non_si_lascia_ingannare_dal_bssid(fake_ex):
    # "BSSID :" contiene "SSID :": un parsing ingenuo prende l'indirizzo
    # hardware al posto del nome della rete.
    fake_ex.replies = {"getsummary": R(True, out=SUMMARY)}
    assert current_ssid(fake_ex) == "Rete Del Cliente"


def test_legge_la_password_dal_portachiavi(fake_ex):
    fake_ex.replies = {"find-generic-password": R(True, out="segreta123\n")}
    assert wifi_password(fake_ex, "Rete") == "segreta123"


def test_portachiavi_negato_non_e_una_password_vuota(fake_ex):
    fake_ex.replies = {"find-generic-password": R(False, error="denied")}
    assert wifi_password(fake_ex, "Rete") is None


# ------------------------------------------------------------- il giro completo


# L'indirizzo che il DHCP del deck da' al Mac dentro il proprio access
# point. Va simulato: senza, il giro aspetta invano un indirizzo che nessuno
# assegna, ed e' giusto che lo faccia.
IP_SULL_AP = R(True, out="192.168.4.2")


def _fake(fake_ex, *, invio=None, ssid=SUMMARY):
    fake_ex.replies = {
        "getsummary": R(True, out=ssid),
        "find-generic-password": R(True, out="segreta123\n"),
        "getifaddr": IP_SULL_AP,
    }
    inviati = []

    def invia(url):
        inviati.append(url)
        if invio is not None:
            return invio()
        return True

    return inviati, invia


def _reti_impostate(fake_ex):
    return [c for c in fake_ex.calls if "setairportnetwork" in " ".join(c)]


def test_giro_completo(fake_ex):
    inviati, invia = _fake(fake_ex)
    esito = pair_over_wifi(fake_ex, sender=invia)
    assert esito.ok, esito.error
    # si e' attaccato al deck, poi e' tornato indietro
    reti = [c[-2] if c[-1] == "segreta123" else c[-1]
            for c in _reti_impostate(fake_ex)]
    assert reti == [AP_SSID, "Rete Del Cliente"]
    assert len(inviati) == 1 and AP_IP in inviati[0]
    assert "Rete+Del+Cliente" in inviati[0] or "Rete%20Del%20Cliente" in inviati[0]
    assert "segreta123" in inviati[0]


def test_la_rete_originale_torna_anche_se_l_invio_fallisce(fake_ex):
    def esplode():
        raise OSError("il deck non risponde")

    inviati, invia = _fake(fake_ex, invio=esplode)
    esito = pair_over_wifi(fake_ex, sender=invia, dormi=lambda s: None)
    assert not esito.ok
    assert "il deck non risponde" in esito.error
    # il pezzo che conta: il Mac non resta orfano
    reti = _reti_impostate(fake_ex)
    assert len(reti) == 2 and "Rete Del Cliente" in " ".join(reti[-1])


def test_la_rete_originale_torna_anche_se_l_ap_non_si_raggiunge(fake_ex):
    fake_ex.replies = {
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
        f"setairportnetwork en0 {AP_SSID}": R(False, error="rete non trovata"),
    }
    esito = pair_over_wifi(fake_ex, sender=lambda url: True)
    assert not esito.ok
    assert "MacDeck Fallback" in esito.error or "non trovata" in esito.error
    assert any("Rete Del Cliente" in " ".join(c)
               for c in _reti_impostate(fake_ex))


def test_senza_rete_di_partenza_non_si_muove(fake_ex):
    inviati, invia = _fake(fake_ex, ssid="<dictionary> {\n}")
    esito = pair_over_wifi(fake_ex, sender=invia)
    assert not esito.ok
    assert _reti_impostate(fake_ex) == []
    assert inviati == []


def test_senza_password_non_si_muove(fake_ex):
    # Staccarsi dalla rete senza saperne la password significa non poterci
    # piu' tornare: meglio non partire affatto.
    fake_ex.replies = {
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(False, error="denied"),
    }
    esito = pair_over_wifi(fake_ex, sender=lambda url: True)
    assert not esito.ok
    assert "portachiavi" in esito.error.lower()
    assert _reti_impostate(fake_ex) == []


def test_una_password_passata_a_mano_evita_il_portachiavi(fake_ex):
    fake_ex.replies = {
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(False, error="denied"),
        "getifaddr": IP_SULL_AP,
    }
    esito = pair_over_wifi(fake_ex, sender=lambda url: True,
                           password="scritta a mano")
    assert esito.ok, esito.error
    assert not any("find-generic-password" in " ".join(c)
                   for c in fake_ex.calls)


# ------------------------------------------------------- protocollo su cavo


def test_pacchetto_improv_ben_formato():
    p = improv_packet("rete", "chiave")
    assert p[:6] == b"IMPROV"
    assert p[6] == 1              # versione del protocollo
    assert p[7] == 0x03           # comando RPC
    assert p[9] == 0x01           # WIFI_SETTINGS
    # lunghezza dichiarata contro lunghezza reale
    assert p[8] == len(p) - 10
    assert p[-1] == sum(p[:-1]) & 0xFF


def test_il_pacchetto_porta_i_valori_giusti():
    p = improv_packet("rete", "chiave")
    assert b"rete" in p and b"chiave" in p
    corpo = p[9:-1]
    assert corpo[0] == 0x01       # WIFI_SETTINGS
    assert corpo[1] == len(corpo) - 2   # lunghezza dichiarata del corpo RPC
    assert corpo[2] == 4          # len("rete")
    assert corpo[3:7] == b"rete"
    assert corpo[7] == 6          # len("chiave")
    assert corpo[8:14] == b"chiave"


@pytest.mark.parametrize("ssid,psk", [("a" * 300, "x"), ("a", "y" * 300)])
def test_valori_troppo_lunghi_vengono_rifiutati(ssid, psk):
    # Un campo oltre 255 non entra in un byte di lunghezza: senza controllo
    # il pacchetto sarebbe silenziosamente malformato.
    with pytest.raises(ValueError):
        improv_packet(ssid, psk)


# ------------------------------------------- lettura delle risposte sul cavo


def _pkt(tipo: int, dati: bytes) -> bytes:
    testa = b"IMPROV" + bytes([1, tipo, len(dati)])
    return testa + dati + bytes([sum(testa + dati) & 0xFF])


def test_riconosce_uno_stato_nel_flusso():
    from macdeck.pairing import improv_parse
    # In mezzo ci finiscono i log dell'ESP: il parser deve pescare i
    # pacchetti dal rumore, non pretendere un flusso pulito.
    flusso = b"[I][app]: avvio\r\n" + _pkt(0x01, bytes([0x04])) + b"altro"
    assert improv_parse(flusso) == [(0x01, bytes([0x04]))]


def test_scarta_un_pacchetto_con_checksum_sbagliato():
    from macdeck.pairing import improv_parse
    rotto = bytearray(_pkt(0x01, bytes([0x04])))
    rotto[-1] ^= 0xFF
    assert improv_parse(bytes(rotto)) == []


def test_un_pacchetto_troncato_non_esplode():
    from macdeck.pairing import improv_parse
    assert improv_parse(_pkt(0x01, bytes([0x04]))[:-3]) == []


def test_legge_piu_pacchetti_di_seguito():
    from macdeck.pairing import improv_parse
    flusso = _pkt(0x01, bytes([0x03])) + _pkt(0x01, bytes([0x04]))
    assert len(improv_parse(flusso)) == 2


# ------------------------------------------------------- accoppiamento su cavo


class _FintaSeriale:
    def __init__(self, risposta: bytes):
        self.scritto = b""
        self._risposta = risposta

    def write(self, dati):
        self.scritto += dati

    def read(self, n=1):
        pezzo, self._risposta = self._risposta[:n], self._risposta[n:]
        return pezzo

    def close(self):
        pass


def test_su_cavo_riuscito():
    from macdeck.pairing import pair_over_usb
    finta = _FintaSeriale(_pkt(0x01, bytes([0x04])))   # provisioned
    esito = pair_over_usb("/dev/finta", "rete", "chiave",
                          opener=lambda porta: finta, attesa=0.2)
    assert esito.ok, esito.error
    assert b"IMPROV" in finta.scritto and b"rete" in finta.scritto


def test_su_cavo_il_deck_segnala_un_errore():
    from macdeck.pairing import pair_over_usb
    finta = _FintaSeriale(_pkt(0x02, bytes([0x03])))   # unable to connect
    esito = pair_over_usb("/dev/finta", "rete", "chiave",
                          opener=lambda porta: finta, attesa=0.2)
    assert not esito.ok
    assert "collegarsi" in esito.error.lower()


def test_su_cavo_nessuna_risposta():
    from macdeck.pairing import pair_over_usb
    esito = pair_over_usb("/dev/finta", "rete", "chiave",
                          opener=lambda porta: _FintaSeriale(b""), attesa=0.2)
    assert not esito.ok
    assert "risposta" in esito.error.lower()


def test_su_cavo_porta_inesistente_non_solleva():
    from macdeck.pairing import pair_over_usb

    def boom(porta):
        raise OSError("could not open port")

    esito = pair_over_usb("/dev/manca", "rete", "chiave", opener=boom)
    assert not esito.ok and "could not open port" in esito.error


SUMMARY_OSCURATO = """<dictionary> {
  BSSID : <redacted>
  SSID : <redacted>
  Security : WPA2 Personal
}"""


def test_ssid_oscurato_da_macos_non_e_un_nome_di_rete(fake_ex):
    # Senza il permesso Localizzazione macOS risponde `SSID : <redacted>`.
    # Preso alla lettera diventa una rete che si chiama "<redacted>".
    fake_ex.replies = {"getsummary": R(True, out=SUMMARY_OSCURATO)}
    assert current_ssid(fake_ex) is None


def test_ssid_oscurato_manda_a_passarlo_a_mano(fake_ex):
    fake_ex.replies = {"getsummary": R(True, out=SUMMARY_OSCURATO)}
    esito = pair_over_wifi(fake_ex)
    assert not esito.ok
    assert "--ssid" in esito.error


def test_ssid_oscurato_non_fa_cambiare_rete_al_mac(fake_ex):
    # Con --password il blocco del portachiavi non scatta, e il giro
    # partirebbe: il Mac si staccherebbe per tornare su una rete che si
    # chiama "<redacted>", cioe' su nessuna rete.
    fake_ex.replies = {"getsummary": R(True, out=SUMMARY_OSCURATO)}
    pair_over_wifi(fake_ex, password="segreta123")
    assert not any("-setairportnetwork" in " ".join(c) for c in fake_ex.calls)


def test_ssid_passato_a_mano_supera_l_oscuramento(fake_ex):
    # Il messaggio di SSID_ILLEGGIBILE promette una via d'uscita: "passala a
    # mano con --ssid". Se l'opzione non arriva fin qui, quella promessa e'
    # falsa e su un Mac che nasconde il nome della rete l'accoppiamento via
    # WiFi diventa impossibile — proprio quando il deck e' irraggiungibile
    # e il cavo dati non e' a portata di mano.
    fake_ex.replies = {"getsummary": R(True, out=SUMMARY_OSCURATO),
                       "getifaddr": IP_SULL_AP}
    inviati = []
    esito = pair_over_wifi(fake_ex, sender=lambda url: inviati.append(url) or True,
                           ssid="FASTWEB-45H7P7", password="segreta123")
    assert esito.ok, esito.error
    assert esito.ssid == "FASTWEB-45H7P7"
    assert len(inviati) == 1 and "FASTWEB-45H7P7" in inviati[0]


def test_si_torna_sulla_rete_passata_a_mano(fake_ex):
    # Il pezzo che non si puo' sbagliare: la rete del ritorno e' quella
    # passata a mano, non il "<redacted>" che macOS ha risposto.
    fake_ex.replies = {"getsummary": R(True, out=SUMMARY_OSCURATO),
                       "getifaddr": IP_SULL_AP}
    pair_over_wifi(fake_ex, sender=lambda url: True,
                   ssid="FASTWEB-45H7P7", password="segreta123")
    reti = _reti_impostate(fake_ex)
    assert len(reti) == 2
    assert AP_SSID in " ".join(reti[0])
    assert "FASTWEB-45H7P7" in " ".join(reti[-1])
    assert REDACTED not in " ".join(" ".join(c) for c in fake_ex.calls)


def test_un_ssid_a_mano_non_scavalca_il_portachiavi_mancante(fake_ex):
    # Sapere il nome della rete non significa saperne la password: senza
    # quella il giro non deve partire, come sempre.
    fake_ex.replies = {
        "getsummary": R(True, out=SUMMARY_OSCURATO),
        "find-generic-password": R(False, error="denied"),
    }
    esito = pair_over_wifi(fake_ex, sender=lambda url: True,
                           ssid="FASTWEB-45H7P7")
    assert not esito.ok
    assert _reti_impostate(fake_ex) == []


# ------------------------------- l'indirizzo sulla sottorete dell'access point


class ExecutorConSequenza(FakeExecutor):
    """Come FakeExecutor, ma `getifaddr` risponde una cosa diversa ogni volta.

    Serve a riprodurre il DHCP: subito dopo l'associazione il Mac non ha
    ancora un indirizzo, e ce l'ha qualche secondo dopo.
    """

    def __init__(self, indirizzi: list[str], **kw):
        super().__init__(**kw)
        self.indirizzi = list(indirizzi)

    def run(self, argv, timeout: float = 5.0):
        if "getifaddr" in " ".join(argv):
            self.calls.append(tuple(argv))
            out = self.indirizzi.pop(0) if len(self.indirizzi) > 1 \
                else self.indirizzi[0]
            return R(bool(out), out=out)
        return super().run(argv, timeout)


def _quante(ex, pezzo):
    return len([c for c in ex.calls if pezzo in " ".join(c)])


def test_aspetta_l_indirizzo_dell_ap_prima_di_parlargli():
    # Il guasto vero: associarsi all'AP non significa avere un indirizzo su
    # quella sottorete. Il DHCP del deck ci mette qualche secondo, e la GET
    # partita troppo presto muore in timeout senza che nulla sia rotto.
    ex = ExecutorConSequenza(["", "", "192.168.4.2"], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    quando = []

    def invia(url):
        quando.append(_quante(ex, "getifaddr"))
        return True

    esito = pair_over_wifi(ex, sender=invia, dormi=lambda s: None)
    assert esito.ok, esito.error
    # ha parlato solo dopo che l'indirizzo era arrivato, non al terzo secondo
    assert quando == [3]


def test_senza_indirizzo_sull_ap_lo_dice_invece_di_scadere():
    # Se l'indirizzo non arriva mai, "timed out" non spiega niente: il
    # messaggio deve dire cosa non e' successo.
    ex = ExecutorConSequenza([""], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    inviati = []
    esito = pair_over_wifi(ex, sender=lambda url: inviati.append(url) or True,
                           attesa=0.05, dormi=lambda s: None)
    assert not esito.ok
    assert "indirizzo" in esito.error.lower()
    assert inviati == []
    # e comunque il Mac torna a casa
    assert any("Rete Del Cliente" in " ".join(c) for c in _reti_impostate(ex))


def test_riprova_a_mandare_le_credenziali():
    # Il portale risponde quando il loop principale del deck glielo lascia
    # fare, e quel loop sta disegnando: un solo tentativo e' troppo poco.
    ex = ExecutorConSequenza(["192.168.4.2"], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    tentativi = []

    def invia(url):
        tentativi.append(url)
        if len(tentativi) < 3:
            raise OSError("timed out")
        return True

    esito = pair_over_wifi(ex, sender=invia, dormi=lambda s: None)
    assert esito.ok, esito.error
    assert len(tentativi) == 3


def test_riaggancia_l_ap_se_il_lease_non_arriva():
    # ESPHome, in ripiego, non smette di ritentare la rete di casa: mentre
    # lo fa il proprio access point cade, e la trattativa DHCP di macOS
    # finisce nel buco. Insistere sull'associazione e' la differenza fra
    # riuscire e riportare un guasto che non c'e'.
    ex = ExecutorConSequenza([""], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    esito = pair_over_wifi(ex, sender=lambda url: True,
                           attesa=0.06, dormi=lambda s: None)
    assert not esito.ok
    aggancî = [c for c in _reti_impostate(ex) if AP_SSID in " ".join(c)]
    assert len(aggancî) > 1, "un'associazione sola non basta"
    # e comunque si torna a casa
    assert "Rete Del Cliente" in " ".join(_reti_impostate(ex)[-1])


def test_dice_quale_indirizzo_ha_visto():
    # Se macOS e' rientrato sulla rete di casa da solo, l'indirizzo visto
    # e' quello di casa: dirlo distingue "non sono rimasto sull'AP" da
    # "il DHCP del deck non ha risposto", che sono guasti diversi.
    ex = ExecutorConSequenza(["192.168.1.28"], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    esito = pair_over_wifi(ex, sender=lambda url: True,
                           attesa=0.06, dormi=lambda s: None)
    assert not esito.ok
    assert "192.168.1.28" in esito.error


def test_nessun_indirizzo_lo_dice_a_parole():
    ex = ExecutorConSequenza([""], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    esito = pair_over_wifi(ex, sender=lambda url: True,
                           attesa=0.06, dormi=lambda s: None)
    assert "nessun indirizzo" in esito.error.lower()


def test_ap_inesistente_non_e_un_agganciamento(fake_ex):
    # `networksetup -setairportnetwork` stampa "Could not find network X."
    # ed esce con codice ZERO. Fidarsi del codice di uscita significa
    # credere di essere sull'access point del deck mentre il Mac non si e'
    # mosso da casa — ed e' cosi' che una diagnosi finisce fuori strada:
    # il timeout verso 192.168.4.1 sembra un deck che non risponde, ed e'
    # invece una richiesta partita dalla rete sbagliata.
    fake_ex.replies = {
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
        # L'indirizzo c'e': cosi' l'unica cosa che puo' fermare il giro e'
        # il controllo sull'esito della join, che e' quello in prova.
        "getifaddr": IP_SULL_AP,
        f"setairportnetwork en0 {AP_SSID}": R(
            True, out=f"Could not find network {AP_SSID}."),
    }
    inviati = []
    esito = pair_over_wifi(fake_ex, sender=lambda url: inviati.append(url) or True,
                           attesa=0.05, dormi=lambda s: None)
    assert not esito.ok
    assert inviati == [], "non si parla a un access point a cui non si e' attaccati"
    assert AP_SSID in esito.error


def test_join_riuscita_non_stampa_niente(fake_ex):
    # Il caso normale: nessun output significa associazione avvenuta.
    fake_ex.replies = {"setairportnetwork": R(True, out="")}
    assert join(fake_ex, "Una Rete").ok

def test_un_flap_dell_ap_non_ferma_i_tentativi_successivi():
    # L'access point del deck cade mentre ESPHome ri-scandisce la rete di
    # casa: e' proprio il transitorio per cui esistono i cinque giri. Un
    # riaggancio fallito e' un giro perso, non il comando perso — altrimenti
    # la protezione annulla se stessa al primo intoppo.
    class ApCheSfarfalla(FakeExecutor):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.agganci = 0

        def run(self, argv, timeout: float = 5.0):
            unito = " ".join(argv)
            if AP_SSID in unito and "setairportnetwork" in unito:
                self.agganci += 1
                self.calls.append(tuple(argv))
                # il secondo tentativo cade nel buco, il terzo riesce
                if self.agganci == 2:
                    return R(True, out=f"Could not find network {AP_SSID}.")
                return R(True, out="")
            if "getifaddr" in unito:
                self.calls.append(tuple(argv))
                # l'indirizzo arriva solo dopo il terzo aggancio
                return R(True, out="192.168.4.2") if self.agganci >= 3 \
                    else R(False, out="")
            return super().run(argv, timeout)

    ex = ApCheSfarfalla(replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    inviati = []
    esito = pair_over_wifi(ex, sender=lambda url: inviati.append(url) or True,
                           attesa=0.15, dormi=lambda s: None)
    assert esito.ok, esito.error
    assert ex.agganci >= 3, "si e' fermato al primo riaggancio fallito"
    assert len(inviati) == 1


def test_ap_mai_comparso_lo_dice_senza_incolpare_il_dhcp():
    ex = ExecutorConSequenza([""], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
        f"setairportnetwork en0 {AP_SSID}": R(
            True, out=f"Could not find network {AP_SSID}."),
    })
    esito = pair_over_wifi(ex, sender=lambda url: True,
                           attesa=0.05, dormi=lambda s: None)
    assert not esito.ok
    assert "mai comparso" in esito.error
    assert "DHCP" not in esito.error, "l'AP non c'era: il DHCP non e' in causa"


def test_l_indirizzo_di_partenza_fra_i_visti_accusa_macos():
    # Il Mac e' tornato sulla rete di prima: lo si sa perche' fra gli
    # indirizzi visti c'e' quello che aveva PRIMA di spostarsi, non perche'
    # non e' un indirizzo dell'access point.
    ex = ExecutorConSequenza(["192.168.1.28"], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    esito = pair_over_wifi(ex, sender=lambda url: True,
                           attesa=0.05, dormi=lambda s: None)
    assert not esito.ok
    assert "rientrato" in esito.error
    assert "192.168.1.28" in esito.error


def test_un_link_local_accusa_il_dhcp_del_deck_non_macos():
    # 169.254.x.y e' cio' che macOS si assegna da solo QUANDO IL DHCP NON
    # RISPONDE, ed e' la firma del guasto opposto: il Mac e' rimasto
    # sull'access point. Con la vecchia condizione "qualunque indirizzo non
    # dell'AP" questo caso accusava macOS di essere scappato.
    ex = ExecutorConSequenza(["192.168.1.28", "169.254.7.7"], replies={
        "getsummary": R(True, out=SUMMARY),
        "find-generic-password": R(True, out="segreta123\n"),
    })
    esito = pair_over_wifi(ex, sender=lambda url: True,
                           attesa=0.05, dormi=lambda s: None)
    assert not esito.ok
    assert "DHCP" in esito.error
    assert "rientrato" not in esito.error
