"""Insegnare al deck una rete che si scopre solo sul momento.

Fuori casa il Mac si attacca al WiFi del posto e il deck non lo conosce.
Il portale del deck accetta gia' credenziali su /wifisave; qui c'e' chi lo
guida al posto nostro, e soprattutto chi garantisce che il Mac torni sulla
sua rete anche quando qualcosa va storto a meta' strada.
"""

import pytest

from macdeck.executor import Result as R
from macdeck.pairing import (
    AP_IP,
    AP_SSID,
    current_ssid,
    improv_packet,
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


def _fake(fake_ex, *, invio=None, ssid=SUMMARY):
    fake_ex.replies = {
        "getsummary": R(True, out=ssid),
        "find-generic-password": R(True, out="segreta123\n"),
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
    esito = pair_over_wifi(fake_ex, sender=invia)
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
