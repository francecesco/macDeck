"""Insegnare al deck una rete che si scopre solo sul momento.

Fuori casa il Mac si attacca al WiFi del posto e il deck non lo conosce: la
password di quella rete non esisteva quando il firmware e' stato compilato.
Il deck pero' alza gia' un proprio access point con un portale che accetta
credenziali su `/wifisave`, e ESPHome le salva in modo permanente. Qui c'e'
chi guida quel portale al posto nostro.

Il giro richiede di staccare il Mac dalla sua rete per un minuto circa, e
fino a tre minuti e mezzo nel caso peggiore: l'indirizzo sull'access point
del deck arriva quando il suo DHCP risponde, non a un tempo fisso, e ogni
aggancio ha per conto suo 30 s di timeout. Da cui la regola che governa questo modulo: **la rete
di partenza va ripristinata sempre**, anche quando il resto fallisce. Un Mac
che resta attaccato all'access point di un display e' un guaio peggiore di
un accoppiamento non riuscito.

ATTENZIONE: questa via vale solo per un firmware che alzi il portale. Quello
di MacDeck non lo fa piu' — una rete salvata da li' cancellerebbe casa e
ufficio — quindi `macdeck pair` accetta solo `--usb`. Il codice resta perche'
il protocollo del portale e le trappole di macOS qui sotto non cambiano.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .executor import Executor, Result

AP_SSID = "MacDeck Fallback"
AP_IP = "192.168.4.1"
WIFI_IFACE = "en0"
NETWORKSETUP = "/usr/sbin/networksetup"
SECURITY = "/usr/bin/security"
IPCONFIG = "/usr/sbin/ipconfig"

# Cosa scrive macOS al posto del nome della rete quando non ha il permesso
# per dirtelo. Non e' un SSID: e' un rifiuto travestito da risposta.
REDACTED = "<redacted>"

SSID_ILLEGGIBILE = (
    "non riesco a leggere la rete del Mac: o non e' collegato a nessun WiFi, "
    "o macOS ne sta nascondendo il nome (al terminale manca il permesso "
    "Localizzazione). Passala a mano con --ssid 'nome' --password 'segreta'.")

# Quanto aspettare tra un sondaggio e l'altro mentre il Mac si sposta di rete.
SETTLE = 1.0

# La sottorete che il DHCP del deck serve dentro il proprio access point:
# ESPHome sta su 192.168.4.1 e da' ai client 192.168.4.2 e seguenti.
AP_SUBNET = "192.168.4."

# Cio' che macOS si assegna da solo quando il DHCP non risponde. Vederlo nel
# registro non e' un dettaglio: e' la firma di un lease mancato, cioe' del
# guasto opposto a "il Mac e' tornato sulla sua rete".
LINK_LOCAL = "169.254."

# Associarsi a un access point e avere un indirizzo su di esso sono due
# fatti distinti, separati da una trattativa DHCP di durata non garantita.
# Con un'attesa a tempo di tre secondi la GET al portale partiva prima
# dell'indirizzo e moriva in `urlopen error timed out`: un guasto
# inesistente, riportato come guasto. Si aspetta la condizione — l'indirizzo
# c'e' — e non un tempo che indovina quando arrivera'.
AP_ATTESA = 60.0

# Quante volte ri-emettere l'associazione all'access point mentre si aspetta
# il lease. ESPHome, in ripiego, non rinuncia alla rete di casa: continua a
# ritentarla, e mentre lo fa il proprio access point cade — la trattativa
# DHCP di macOS puo' finire in quel buco piu' volte di fila. Copre anche
# l'altro guasto possibile, macOS che rientra da solo su una rete con
# internet. Cinque perche' ogni giro e' una scommessa indipendente: cinque
# tentativi da dodici secondi costano un minuto di rete al Mac e non
# richiedono ne' cavo ne' privilegi di root.
RIAGGANCI = 5

# Il portale risponde quando il loop principale del deck glielo lascia fare,
# e quel loop sta disegnando lo schermo. Stessa ragione dei dieci secondi di
# discovery.py: un tentativo solo misura la fortuna, non la raggiungibilita'.
TENTATIVI = 3


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
    riporta ancora, ma solo a chi ha il permesso Localizzazione: senza,
    risponde `SSID : <redacted>`. Che e' un "non te lo dico", non un nome —
    e va trattato come l'assenza di rete, o si finisce per insegnare al
    deck una rete che si chiama "<redacted>".
    """
    r = ex.run([IPCONFIG, "getsummary", WIFI_IFACE], timeout=5.0)
    if not r.ok or not r.out:
        return None
    # `^\s*SSID :` e non `SSID :`, altrimenti la riga BSSID (l'indirizzo
    # hardware dell'access point) vince perche' viene prima.
    m = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", r.out, re.M)
    if not m or m.group(1) == REDACTED:
        return None
    return m.group(1)


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


def ip_interfaccia(ex: Executor, iface: str = WIFI_IFACE) -> str | None:
    """L'indirizzo IPv4 dell'interfaccia adesso, o None se non ne ha."""
    r = ex.run([IPCONFIG, "getifaddr", iface], timeout=5.0)
    if not r.ok or not r.out:
        return None
    return r.out.strip() or None


def attendi_ip(ex: Executor, prefisso: str = AP_SUBNET, *,
               entro: float = AP_ATTESA, passo: float = SETTLE,
               dormi=time.sleep, visti: list[str] | None = None) -> str | None:
    """Aspetta che il Mac abbia un indirizzo sulla sottorete data.

    Restituisce l'indirizzo, oppure None se non arriva entro il tempo: in
    quel caso non c'e' niente da chiedere al portale, e dirlo e' piu' utile
    che lasciare scadere una richiesta.

    In `visti` finiscono gli indirizzi incontrati per strada, senza
    ripetizioni. Non e' contabilita' oziosa: un'attesa fallita con
    l'indirizzo di casa nel registro significa che il Mac non e' rimasto
    sull'access point del deck, e nessun DHCP e' stato mai in causa. Sono
    due guasti diversi e un messaggio che non li distingue manda a
    cercare la cosa sbagliata.
    """
    scadenza = time.monotonic() + entro
    while True:
        ip = ip_interfaccia(ex)
        if ip and visti is not None and ip not in visti:
            visti.append(ip)
        if ip and ip.startswith(prefisso):
            return ip
        if time.monotonic() >= scadenza:
            return None
        dormi(passo)


# Cosa stampa `networksetup` quando la rete non c'e'. Serve perche' in quel
# caso esce comunque con codice ZERO: fidarsi del codice di uscita significa
# credere di essersi spostati di rete mentre non e' successo niente, e la
# richiesta successiva parte dalla rete sbagliata e scade. Un timeout che
# sembra un dispositivo muto ed e' invece una domanda fatta al posto
# sbagliato: due giri di diagnosi buttati, prima di guardare l'output.
JOIN_FALLITA = "could not find network"


def join(ex: Executor, ssid: str, password: str | None = None) -> Result:
    argv = [NETWORKSETUP, "-setairportnetwork", WIFI_IFACE, ssid]
    if password:
        argv.append(password)
    r = ex.run(argv, timeout=30.0)
    if r.ok and JOIN_FALLITA in (r.out or "").lower():
        return Result(False, out=r.out,
                      error=f"la rete '{ssid}' non e' in aria: {r.out.strip()}")
    return r


def _send(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=10.0) as resp:
        return 200 <= resp.status < 400


# --------------------------------------------------------------- il giro


def pair_over_wifi(ex: Executor, *, sender=_send, ssid: str | None = None,
                   password: str | None = None,
                   settle: float = SETTLE, attesa: float = AP_ATTESA,
                   dormi=time.sleep) -> Esito:
    """Passa al deck la rete a cui il Mac e' attaccato adesso.

    `ssid` scavalca la lettura automatica, ed e' la via d'uscita che
    SSID_ILLEGGIBILE promette: quando macOS nasconde il nome della rete
    l'utente lo sa comunque, e senza questo parametro l'accoppiamento via
    WiFi sarebbe impossibile proprio sul Mac che ne ha bisogno.
    """
    ssid = ssid or current_ssid(ex)
    if not ssid:
        return Esito(False, SSID_ILLEGGIBILE)
    if ssid == AP_SSID:
        return Esito(False, "il Mac e' gia' attaccato al deck, non a una rete")

    psk = password or wifi_password(ex, ssid)
    if not psk:
        return Esito(
            False,
            f"password di '{ssid}' non leggibile dal portachiavi. "
            "Passala a mano con --password: senza, staccandosi dalla rete "
            "non si potrebbe piu' tornare indietro.")

    # L'indirizzo che il Mac ha PRIMA di spostarsi. E' il solo modo di
    # riconoscere poi "macOS e' rientrato sulla sua rete" senza confonderlo
    # con "il DHCP del deck non ha risposto": vedere un indirizzo che non e'
    # dell'access point non basta, perche' `attendi_ip` esce appena ne vede
    # uno dell'access point — quindi TUTTO cio' che resta nel registro e'
    # per costruzione non dell'access point, e la distinzione basata su
    # quello era sempre vera. Un link-local se ne accorge da solo.
    ip_partenza = ip_interfaccia(ex)

    ritorno = None
    try:
        visti: list[str] = []
        ip = None
        agganci = 0
        for _ in range(RIAGGANCI):
            # L'associazione si ri-emette a ogni giro. Un riaggancio fallito
            # e' un GIRO perso, non il comando perso: l'access point del deck
            # cade proprio mentre ESPHome ri-scandisce la rete di casa, ed e'
            # il transitorio per cui questo ciclo esiste. Farlo abortire al
            # primo intoppo annullerebbe la protezione con se stessa.
            if not join(ex, AP_SSID).ok:
                dormi(settle)
                continue
            agganci += 1
            ip = attendi_ip(ex, entro=attesa / RIAGGANCI, passo=settle,
                            dormi=dormi, visti=visti)
            if ip:
                break

        if not ip and agganci == 0:
            return Esito(
                False,
                f"l'access point '{AP_SSID}' non e' mai comparso in "
                f"{RIAGGANCI} tentativi. Il deck lo alza solo quando non "
                "trova reti note: se e' gia' collegato a qualcosa non c'e', "
                "e se e' spento o lontano nemmeno.")
        if not ip:
            dove = ", ".join(visti) if visti else "nessun indirizzo"
            # Il link-local per primo: e' una firma inequivocabile (macOS
            # se lo assegna SOLO quando il DHCP tace), mentre l'indirizzo di
            # partenza potrebbe coincidere per caso.
            if any(v.startswith(LINK_LOCAL) for v in visti):
                perche = ("Il Mac e' rimasto sull'access point ma si e' "
                          f"assegnato un indirizzo {LINK_LOCAL}x da solo, "
                          "che e' cio' che fa quando il DHCP non risponde: "
                          "il DHCP del deck e' muto perche' occupato a "
                          "ritentare la rete di casa. Riprova, oppure usa "
                          "il cavo dati.")
            elif ip_partenza and ip_partenza in visti:
                perche = (f"Il Mac e' rientrato su '{ssid}' da solo: macOS "
                          "abbandona le reti senza internet. Tieni il deck "
                          "vicino e riprova, oppure usa il cavo dati.")
            else:
                perche = ("Il DHCP del deck non ha risposto: e' occupato a "
                          "ritentare la rete di casa. Riprova, oppure usa "
                          "il cavo dati.")
            return Esito(
                False,
                f"attaccato a '{AP_SSID}' {agganci} volte su {RIAGGANCI}, "
                f"ma senza un indirizzo su {AP_SUBNET}x entro {attesa:g}s "
                f"complessivi di attesa (visto invece: {dove}). " + perche)

        url = (f"http://{AP_IP}/wifisave?"
               + urllib.parse.urlencode({"ssid": ssid, "psk": psk}))
        ultimo: Exception | None = None
        passato = False
        for tentativo in range(TENTATIVI):
            try:
                if not sender(url):
                    return Esito(
                        False,
                        "il portale del deck ha rifiutato le credenziali")
                passato = True
                break
            except Exception as exc:                           # noqa: BLE001
                ultimo = exc
                if tentativo < TENTATIVI - 1:
                    dormi(settle)
        if not passato:
            raise ultimo if ultimo else RuntimeError("invio non riuscito")
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
        # La terza causa non e' un errore di configurazione ed e' la piu'
        # probabile: senza `ap:` il firmware ha `reboot_timeout` attivo
        # (in wifi_component.cpp il riavvio scatta `if (!has_ap() &&
        # reboot_timeout_ != 0)`), quindi un deck che non trova ne' casa ne'
        # ufficio si riavvia ogni dieci minuti — ed e' esattamente lo stato
        # in cui si usa il cavo. Un riavvio dentro questa finestra fa
        # sparire la porta USB_SERIAL_JTAG: non c'e' niente da aggiustare,
        # c'e' da rilanciare il comando.
        return Esito(False, "nessuna risposta dal deck sul cavo: porta "
                            "sbagliata, firmware senza improv_serial, "
                            "oppure il deck si e' riavviato proprio adesso "
                            "(senza rete lo fa ogni 10 minuti): rilancia "
                            "il comando")
    except Exception as exc:                                   # noqa: BLE001
        return Esito(False, f"{exc}")
    finally:
        try:
            ser.close()
        except Exception:                                      # noqa: BLE001
            pass
