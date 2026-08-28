"""Insegnare al deck una rete che si scopre solo sul momento.

Fuori casa il Mac si attacca al WiFi del posto e il deck non lo conosce: la
password di quella rete non esisteva quando il firmware e' stato compilato.
Il deck pero' alza gia' un proprio access point con un portale che accetta
credenziali su `/wifisave`, e ESPHome le salva in modo permanente. Qui c'e'
chi guida quel portale al posto nostro.

Il giro richiede di staccare il Mac dalla sua rete per una ventina di
secondi. Da cui la regola che governa questo modulo: **la rete di partenza
va ripristinata sempre**, anche quando il resto fallisce. Un Mac che resta
attaccato all'access point di un display e' un guaio peggiore di un
accoppiamento non riuscito.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .executor import Executor

AP_SSID = "MacDeck Fallback"
AP_IP = "192.168.4.1"
WIFI_IFACE = "en0"
NETWORKSETUP = "/usr/sbin/networksetup"
SECURITY = "/usr/bin/security"
IPCONFIG = "/usr/sbin/ipconfig"

# Quanto aspettare che il Mac si sia effettivamente spostato di rete.
SETTLE = 3.0


@dataclass
class Esito:
    ok: bool
    error: str | None = None
    ssid: str | None = None


# ----------------------------------------------------------- lettura di rete


def current_ssid(ex: Executor) -> str | None:
    """A quale rete e' attaccato il Mac adesso.

    Da macOS 14 `networksetup -getairportnetwork` non risponde piu' ("You are
    not associated with an AirPort network" anche quando lo sei): il nome
    della rete e' diventato un dato protetto. `ipconfig getsummary` lo
    riporta ancora.
    """
    r = ex.run([IPCONFIG, "getsummary", WIFI_IFACE], timeout=5.0)
    if not r.ok or not r.out:
        return None
    # `^\s*SSID :` e non `SSID :`, altrimenti la riga BSSID (l'indirizzo
    # hardware dell'access point) vince perche' viene prima.
    m = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", r.out, re.M)
    return m.group(1) if m else None


def wifi_password(ex: Executor, ssid: str) -> str | None:
    """La password della rete, dal portachiavi.

    macOS chiede conferma all'utente: e' un prompt grafico, e la prima volta
    per ogni rete va autorizzato a mano. Un rifiuto non e' un errore da
    nascondere — senza password non si puo' tornare indietro, quindi il giro
    non deve nemmeno cominciare.
    """
    r = ex.run([SECURITY, "find-generic-password", "-wa", ssid], timeout=60.0)
    if not r.ok or not r.out:
        return None
    return r.out.strip() or None


def join(ex: Executor, ssid: str, password: str | None = None):
    argv = [NETWORKSETUP, "-setairportnetwork", WIFI_IFACE, ssid]
    if password:
        argv.append(password)
    return ex.run(argv, timeout=30.0)


def _send(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=10.0) as resp:
        return 200 <= resp.status < 400


# --------------------------------------------------------------- il giro


def pair_over_wifi(ex: Executor, *, sender=_send, password: str | None = None,
                   settle: float = SETTLE) -> Esito:
    """Passa al deck la rete a cui il Mac e' attaccato adesso."""
    ssid = current_ssid(ex)
    if not ssid:
        return Esito(False, "il Mac non risulta collegato a nessun WiFi")
    if ssid == AP_SSID:
        return Esito(False, "il Mac e' gia' attaccato al deck, non a una rete")

    psk = password or wifi_password(ex, ssid)
    if not psk:
        return Esito(
            False,
            f"password di '{ssid}' non leggibile dal portachiavi. "
            "Passala a mano con --password: senza, staccandosi dalla rete "
            "non si potrebbe piu' tornare indietro.")

    ritorno = None
    try:
        r = join(ex, AP_SSID)
        if not r.ok:
            return Esito(
                False,
                f"non riesco ad attaccarmi a '{AP_SSID}': {r.error}. "
                "Il deck alza il suo access point solo quando non trova reti "
                "note: se e' gia' collegato a qualcosa, non c'e'.")
        time.sleep(settle)

        url = (f"http://{AP_IP}/wifisave?"
               + urllib.parse.urlencode({"ssid": ssid, "psk": psk}))
        if not sender(url):
            return Esito(False, "il portale del deck ha rifiutato le credenziali")
        ritorno = Esito(True, ssid=ssid)
    except Exception as exc:                                   # noqa: BLE001
        ritorno = Esito(False, f"{exc}")
    finally:
        # Sempre, comunque sia andata.
        indietro = join(ex, ssid, psk)
        if ritorno is None:
            ritorno = Esito(False, "interrotto")
        if not indietro.ok and ritorno.ok:
            ritorno = Esito(
                False,
                f"credenziali passate al deck, ma il Mac non e' tornato su "
                f"'{ssid}': {indietro.error}")
    return ritorno


# ------------------------------------------------------- provisioning su cavo

IMPROV_HEADER = b"IMPROV"
IMPROV_VERSION = 1
IMPROV_TYPE_RPC = 0x03
IMPROV_CMD_WIFI = 0x01


def improv_packet(ssid: str, password: str) -> bytes:
    """Il pacchetto Improv che porta una rete al deck via cavo seriale.

    Formato: "IMPROV" | versione | tipo | lunghezza | corpo | checksum.
    Il corpo di un comando RPC e': comando | lunghezza del resto | poi ogni
    stringa preceduta dalla propria lunghezza in un byte — da cui il limite
    di 255 caratteri, che va verificato prima e non scoperto dopo.
    """
    s, p = ssid.encode(), password.encode()
    if len(s) > 255 or len(p) > 255:
        raise ValueError("SSID e password non possono superare i 255 byte")
    corpo = bytes([len(s)]) + s + bytes([len(p)]) + p
    rpc = bytes([IMPROV_CMD_WIFI, len(corpo)]) + corpo
    testa = IMPROV_HEADER + bytes([IMPROV_VERSION, IMPROV_TYPE_RPC, len(rpc)])
    pacchetto = testa + rpc
    return pacchetto + bytes([sum(pacchetto) & 0xFF])


IMPROV_TYPE_STATE = 0x01
IMPROV_TYPE_ERROR = 0x02

STATI = {0x02: "autorizzato", 0x03: "sto provando", 0x04: "collegato"}
ERRORI = {
    0x01: "il deck non ha capito il pacchetto",
    0x02: "comando sconosciuto",
    0x03: "non riesce a collegarsi: rete o password sbagliate",
    0xFF: "errore non specificato",
}


def improv_parse(flusso: bytes) -> list[tuple[int, bytes]]:
    """Pesca i pacchetti Improv dal flusso seriale.

    Sulla stessa porta viaggiano anche i log dell'ESP, quindi non si puo'
    assumere un flusso pulito: si cerca l'intestazione, si verifica il
    checksum, e quello che non torna si butta invece di fidarsi.
    """
    trovati: list[tuple[int, bytes]] = []
    i = 0
    while True:
        i = flusso.find(IMPROV_HEADER, i)
        if i < 0 or i + 9 > len(flusso):
            return trovati
        tipo, lung = flusso[i + 7], flusso[i + 8]
        fine = i + 9 + lung
        if fine >= len(flusso):
            return trovati
        corpo = flusso[i + 9:fine]
        if flusso[fine] == sum(flusso[i:fine]) & 0xFF:
            trovati.append((tipo, corpo))
        i = fine + 1


def _apri_seriale(porta: str):
    import serial
    return serial.Serial(porta, 115200, timeout=0.2)


def pair_over_usb(porta: str, ssid: str, password: str, *,
                  opener=None, attesa: float = 8.0) -> Esito:
    """Passa la rete al deck sul cavo, con il protocollo Improv.

    Serve quando il WiFi e' proprio la cosa che non funziona: l'access point
    del deck non si vede, oppure il Mac non riesce a spostarsi di rete. Il
    cavo non dipende da niente di tutto questo.
    """
    apri = opener or _apri_seriale
    try:
        ser = apri(porta)
    except Exception as exc:                                   # noqa: BLE001
        return Esito(False, f"{exc}")
    try:
        ser.write(improv_packet(ssid, password))
        scadenza = time.monotonic() + attesa
        buf = b""
        while time.monotonic() < scadenza:
            pezzo = ser.read(256)
            if pezzo:
                buf += pezzo
            for tipo, dati in improv_parse(buf):
                if tipo == IMPROV_TYPE_ERROR and dati and dati[0] != 0x00:
                    return Esito(False, ERRORI.get(
                        dati[0], f"errore 0x{dati[0]:02X} dal deck"))
                if tipo == IMPROV_TYPE_STATE and dati and dati[0] == 0x04:
                    return Esito(True, ssid=ssid)
            if not pezzo:
                time.sleep(0.05)
        return Esito(False, "nessuna risposta dal deck sul cavo: porta "
                            "sbagliata, o firmware senza improv_serial")
    except Exception as exc:                                   # noqa: BLE001
        return Esito(False, f"{exc}")
    finally:
        try:
            ser.close()
        except Exception:                                      # noqa: BLE001
            pass
