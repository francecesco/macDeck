"""Il Mac si presenta al deck.

Il problema non e' il trasporto — quello e' gia' WiFi — ma il fatto che il
deck non sappia dove sia il Mac: su una rete nuova l'indirizzo cambia, e un
IP scritto nel firmware smette di esistere.

La direzione qui e' invertita rispetto a tutto il resto del progetto. Il deck
si annuncia gia' da solo via Bonjour (lo fa ESPHome, gratis), quindi e' il Mac
a cercarlo e a *dirgli* dove trovarsi. Il deck scrive quell'indirizzo in
memoria permanente e riparte da li' al riavvio successivo.

Conseguenza: su qualsiasi rete — casa, hotspot del telefono, WiFi di un
cliente — non c'e' niente da configurare. Se il router riassegna gli
indirizzi a entrambi, si ritrovano al giro dopo.

Come l'esecutore, questo modulo non solleva MAI: un deck spento, una rete
assente o una chiave sbagliata sono normale amministrazione, non guasti.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

# Il deck si annuncia con questo nome (il `name:` del blocco esphome:).
DEVICE_NAME = "macdeck"

# L'entita' testo esposta dal firmware che contiene l'indirizzo dell'agent.
HOST_ENTITY = "Indirizzo agent"

# Dieci secondi e non quattro: il deck risponde all'annuncio Bonjour quando
# il suo loop principale glielo lascia fare, e quel loop e' occupato a
# disegnare e a interrogare il Mac. Con quattro secondi la ricerca falliva a
# intermittenza — misurato: fallita a 6 s, riuscita a 10 sullo stesso deck
# acceso e raggiungibile. Il costo e' nullo, gira in un thread di sfondo
# ogni trenta secondi.
DISCOVERY_TIMEOUT = 10.0
API_TIMEOUT = 10.0


# ------------------------------------------------------------ pezzi isolati


def local_ip_towards(host: str) -> str | None:
    """L'indirizzo con cui questo Mac si presenta *a quell'host*.

    Un socket UDP "connesso" non manda nulla, ma obbliga il kernel a
    scegliere l'interfaccia di uscita: e' l'unico modo affidabile di
    rispondere quando ci sono WiFi, Ethernet e VPN insieme. Chiedere
    l'indirizzo dell'hostname locale restituirebbe la scelta sbagliata.
    """
    if not host:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.connect((host, 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def needs_update(attuale: str | None, voluto: str | None) -> bool:
    """Se il deck ha gia' l'indirizzo giusto non lo si riscrive.

    Ogni scrittura e' un ciclo di flash sul deck: a trenta secondi l'uno
    dall'altro, riscrivere lo stesso valore per anni sarebbe uno spreco
    gratuito di una memoria che ha un numero finito di cicli.
    """
    if not voluto:
        return False
    return (attuale or "").strip() != voluto.strip()


def read_secret(path: Path | str, chiave: str) -> str | None:
    """La chiave di cifratura dell'API sta in secrets.yaml, non altrove.

    Volutamente una riga di regex invece di PyYAML: questo file lo legge
    anche ESPHome, e non deve poter essere riscritto per sbaglio da noi.
    """
    try:
        testo = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(rf"^{re.escape(chiave)}\s*:\s*(.+?)\s*$", testo, re.M)
    if not m:
        return None
    return m.group(1).strip().strip("\"'") or None


# ----------------------------------------------------- implementazioni vere


def find_deck(timeout: float = DISCOVERY_TIMEOUT,
              name: str = DEVICE_NAME) -> str | None:
    """Cerca il deck via Bonjour e ne restituisce l'indirizzo IPv4."""
    from zeroconf import ServiceBrowser, Zeroconf

    trovato: list[str] = []
    fatto = threading.Event()

    class Ascolto:
        def add_service(self, zc, tipo, nome):
            if not nome.lower().startswith(name.lower()):
                return
            info = zc.get_service_info(tipo, nome, timeout=int(timeout * 1000))
            if not info:
                return
            for addr in info.parsed_scoped_addresses():
                # Solo IPv4: il firmware compone un URL, e un IPv6 andrebbe
                # racchiuso tra parentesi quadre. Non vale la complicazione.
                if ":" not in addr:
                    trovato.append(addr)
                    fatto.set()
                    return

        def update_service(self, zc, tipo, nome):
            self.add_service(zc, tipo, nome)

        def remove_service(self, zc, tipo, nome):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, "_esphomelib._tcp.local.", Ascolto())
        fatto.wait(timeout)
    finally:
        zc.close()
    return trovato[0] if trovato else None


def _default_client(ip: str, psk: str):
    from aioesphomeapi import APIClient
    return APIClient(ip, 6053, None, noise_psk=psk)


def _con_api(ip: str, psk: str, lavoro, client_factory=None):
    """Apre l'API nativa del deck, esegue `lavoro`, chiude sempre.

    La chiusura e' forzata e a parte, con un timeout suo, perche' un saluto
    ordinato attende una risposta dal deck — e subito dopo una scrittura il
    deck e' impegnato a ripartire verso l'indirizzo nuovo, quindi non
    risponde. Lasciare che quella attesa propaghi il suo errore farebbe
    riportare come fallita una scrittura che invece e' andata a buon fine,
    e l'agent la riproverebbe all'infinito.
    """
    import asyncio
    import contextlib

    factory = client_factory or _default_client

    async def gira():
        cli = factory(ip, psk)
        await cli.connect(login=True)
        try:
            return await lavoro(cli)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(cli.disconnect(force=True), timeout=2.0)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            asyncio.wait_for(gira(), timeout=API_TIMEOUT))
    finally:
        loop.close()


async def _trova_text(cli, etichetta: str):
    entita, _ = await cli.list_entities_services()
    for e in entita:
        if getattr(e, "name", None) == etichetta:
            return e
    return None


def read_agent_host(ip: str, psk: str,
                    etichetta: str = HOST_ENTITY,
                    client_factory=None) -> str | None:
    """Che indirizzo crede di dover usare il deck, adesso."""
    async def lavoro(cli):
        ent = await _trova_text(cli, etichetta)
        if ent is None:
            raise RuntimeError(
                f"il deck non espone l'entita' '{etichetta}': "
                "firmware da aggiornare")
        import asyncio
        visto: asyncio.Future = asyncio.get_running_loop().create_future()

        def arrivato(stato):
            if getattr(stato, "key", None) == ent.key and not visto.done():
                visto.set_result(getattr(stato, "state", None))

        cli.subscribe_states(arrivato)
        try:
            return await asyncio.wait_for(visto, timeout=5.0)
        except asyncio.TimeoutError:
            return None

    return _con_api(ip, psk, lavoro, client_factory)


def write_agent_host(ip: str, psk: str, valore: str,
                     etichetta: str = HOST_ENTITY,
                     client_factory=None) -> bool:
    """Dice al deck dove siamo. Lui lo salva in memoria permanente."""
    async def lavoro(cli):
        ent = await _trova_text(cli, etichetta)
        if ent is None:
            raise RuntimeError(
                f"il deck non espone l'entita' '{etichetta}': "
                "firmware da aggiornare")
        cli.text_command(ent.key, valore)
        return True

    return bool(_con_api(ip, psk, lavoro, client_factory))


# ------------------------------------------------------------ orchestrazione


class Announcer:
    """Ogni tot secondi: trova il deck, guarda cosa crede, correggilo.

    Le tre operazioni di rete sono iniettate perche' i test possano girare
    senza un deck acceso — stessa scelta fatta per l'esecutore.
    """

    def __init__(
        self,
        *,
        psk: str | None,
        interval: float = 30.0,
        finder=find_deck,
        reader=read_agent_host,
        writer=write_agent_host,
        local_ip=local_ip_towards,
    ) -> None:
        self.psk = psk
        self.interval = interval
        self._finder = finder
        self._reader = reader
        self._writer = writer
        self._local_ip = local_ip
        self._lock = threading.Lock()
        self._deck: str | None = None
        self._annunciato: str | None = None
        self._errore: str | None = None
        self._ultimo_giro: float = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- lettura

    def status(self) -> dict:
        with self._lock:
            return {
                "deck": self._deck,
                "annunciato": self._annunciato,
                "ultimo_errore": self._errore,
                "ultimo_giro": self._ultimo_giro,
            }

    # -------------------------------------------------------------- lavoro

    def tick(self) -> None:
        """Un giro completo. Non solleva mai."""
        errore: str | None = None
        deck: str | None = None
        annunciato: str | None = None
        try:
            if not self.psk:
                raise RuntimeError(
                    "chiave di cifratura dell'API assente in secrets.yaml")
            deck = self._finder(DISCOVERY_TIMEOUT)
            if deck:
                mio = self._local_ip(deck)
                if mio:
                    attuale = self._reader(deck, self.psk)
                    if needs_update(attuale, mio):
                        self._writer(deck, self.psk, mio)
                    annunciato = mio
        except Exception as exc:                       # noqa: BLE001
            errore = f"{exc}" or exc.__class__.__name__
        with self._lock:
            self._deck = deck
            self._errore = errore
            self._ultimo_giro = time.time()
            if annunciato is not None:
                self._annunciato = annunciato
            elif errore is not None:
                self._annunciato = None

    # -------------------------------------------------------------- thread

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="macdeck-announcer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t:
            t.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)


# ------------------------------------------------------------------ firewall

FIREWALL_TOOL = "/usr/libexec/ApplicationFirewall/socketfilterfw"


def firewall_state(ex) -> bool | None:
    """Il firewall di macOS e' acceso?

    Vale la pena saperlo solo fuori casa: alla prima rete nuova macOS puo'
    bloccare le connessioni in entrata verso l'interprete, e il sintomo e'
    identico a un deck che non trova il Mac. Distinguere i due casi a mano
    costa mezz'ora; qui costa una riga.

    `None` significa "non l'ho potuto leggere", che non e' "spento".
    """
    r = ex.run([FIREWALL_TOOL, "--getglobalstate"], timeout=5.0)
    if not r.ok or not r.out:
        return None
    testo = r.out.lower()
    if "state = 1" in testo or "state = 2" in testo:
        return True
    if "state = 0" in testo:
        return False
    return None
