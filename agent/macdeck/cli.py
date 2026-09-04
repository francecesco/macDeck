"""Interfaccia a riga di comando.

`doctor` esiste perche' il permesso Accessibilita' e' il punto in cui questo
progetto si rompe in modo silenzioso: osascript non da' un errore chiaro, i
tasti semplicemente non arrivano.
"""

from __future__ import annotations

import argparse
import time
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import icons, paths
from .app import create_app
from .executor import Executor
from .layout import LayoutStore
from .render import TileCache
from . import pairing
from .discovery import Announcer, firewall_state, read_secret
from .state import ACCESSIBILITY_SCRIPT, StateProbe

PLIST_LABEL = "io.macdeck.agent"
PORT = 8765


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def render_plist(python: str, module_dir: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>macdeck.cli</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key><string>{module_dir}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/macdeck.log</string>
  <key>StandardErrorPath</key><string>/tmp/macdeck.err</string>
</dict>
</plist>
"""


def build_serve_app(root: Path | None = None, *, start_probe: bool = False):
    store = LayoutStore(paths.layout_file(root))
    store.load()
    token = paths.load_or_create_token(root)
    probe = StateProbe(Executor(), root=root)
    if start_probe:
        # Le sonde girano in sfondo: /state deve costare ~1 ms, altrimenti il
        # loop del display resta bloccato a ogni poll. Non si avvia nei test.
        probe.start()
    # Il Mac si presenta al deck: lo cerca via Bonjour e gli scrive dove
    # trovarsi. Sta accanto alle sonde perche' ha la stessa natura — un
    # thread che tocca la rete e non deve mai bloccare una richiesta HTTP.
    announcer = Announcer(
        psk=read_secret(paths.firmware_secrets(root), "api_key"))
    if start_probe:
        announcer.start()
    app = create_app(
        store=store,
        cache=TileCache(),
        probe=probe,
        executor=Executor(),
        token=token,
        root=root,
    )
    app.state.announcer = announcer
    return app, token


def _serve(args) -> int:
    import uvicorn

    app, token = build_serve_app(args.root, start_probe=True)
    print(f"MacDeck su http://127.0.0.1:{PORT}  (token: {token})", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    return 0


CLAUDE_STATUSLINE_SNIPPET = (
    'input=$(cat); mkdir -p ~/.config/macdeck/claude; '
    'printf \'%s\' "$input" > ~/.config/macdeck/claude/'
    '$(echo "$input" | jq -r .session_id).json; # ...poi il resto del comando'
)


def claude_bridge_status(root: Path | None) -> tuple[bool, str]:
    """Se la statusLine di Claude Code sta lasciando i suoi file."""
    from .sources import CLAUDE_STALE_S, newest_claude_file
    f = newest_claude_file(paths.claude_dir(root))
    if f is None:
        return False, (
            "ponte Claude Code assente: nessun file in ~/.config/macdeck/claude.\n"
            "       Aggiungi in testa al comando statusLine di ~/.claude/settings.json:\n"
            f"       {CLAUDE_STATUSLINE_SNIPPET}"
        )
    eta = time.time() - f.stat().st_mtime
    if eta > CLAUDE_STALE_S:
        return False, f"ponte Claude Code: ultimo file {f.name} di {eta / 3600:.1f} ore fa (nessuna sessione viva)"
    return True, f"ponte Claude Code: {f.name} aggiornato {int(eta)} s fa"


def pagine_con_app_assente(layout: dict) -> list[tuple[str, str]]:
    """Pagine con `app:` che non corrisponde a nessuna app installata."""
    fuori = []
    for p in layout["pages"]:
        for a in p.get("app") or []:
            if icons.locate_bundle(a) is None:
                fuori.append((p["name"], a))
    return fuori


def _doctor(args) -> int:
    ok = True
    ex = Executor()

    probe = ex.osascript(ACCESSIBILITY_SCRIPT, timeout=3.0)
    if probe.ok:
        print("  OK   Accessibilità: i tasti possono essere inviati")
    else:
        ok = False
        print("  KO   Accessibilità: NON concessa")
        print(f"       Autorizza questo interprete: {sys.executable}")
        print("       Impostazioni di Sistema -> Privacy e Sicurezza -> Accessibilità")
        print("       Aprilo con: open "
              "'x-apple.systempreferences:com.apple.preference.security"
              "?Privacy_Accessibility'")

    store = LayoutStore(paths.layout_file(args.root))
    store.load()
    if store.error:
        ok = False
        print(f"  KO   layout.yaml: {store.error}")
    else:
        pagine = len(store.layout["pages"])
        slot = sum(len(p["slots"]) for p in store.layout["pages"])
        print(f"  OK   layout.yaml: {pagine} pagine, {slot} slot, "
              f"versione {store.version}")
        for nome, app in pagine_con_app_assente(store.layout):
            print(f"  --   pagina {nome!r}: app {app!r} non trovata, la pagina non comparira'")

    ok_ponte, msg = claude_bridge_status(args.root)
    print(f"  {'OK' if ok_ponte else '--'}   {msg}")

    if icons.mdi_available(args.root):
        print("  OK   font MDI presente")
    else:
        print("  --   font MDI assente: esegui `macdeck fetch-fonts` "
              "se vuoi usare le icone mdi:")

    stato = StateProbe(ex, root=args.root).refresh()
    vol = stato["volume"]["level"]
    print(f"  {'OK' if vol is not None else 'KO'}   volume leggibile: {vol}")
    print(f"  --   media: {stato['media']['app'] or 'nessun player attivo'}")

    ann = Announcer(psk=read_secret(paths.firmware_secrets(args.root), "api_key"))
    if ann.psk is None:
        print("  KO   chiave API assente: l'agent non sa parlare col deck")
        print(f"       attesa in {paths.firmware_secrets(args.root)}")
    else:
        ann.tick()
        st = ann.status()
        if st["deck"]:
            print(f"  OK   deck trovato via Bonjour su {st['deck']}")
            print(f"  OK   indirizzo annunciato al deck: {st['annunciato']}")
        else:
            print("  --   deck non trovato in rete (spento, o su un'altra rete)")
        if st["ultimo_errore"]:
            print(f"  KO   annuncio: {st['ultimo_errore']}")

    fw = firewall_state(ex)
    if fw is True:
        print("  --   firewall di macOS attivo: alla prima rete nuova puo'")
        print("       chiedere di autorizzare l'interprete. Se il deck non")
        print("       si aggiorna fuori casa, il colpevole e' quasi sempre qui.")
    elif fw is False:
        print("  OK   firewall di macOS spento: nessuna richiesta in entrata bloccata")

    if plist_path().exists():
        print(f"  OK   LaunchAgent installato in {plist_path()}")
    else:
        print("  --   LaunchAgent non installato: `macdeck install-agent`")

    print(f"  --   token: {paths.load_or_create_token(args.root)}")
    return 0 if ok else 1


def _fetch_fonts(args) -> int:
    target_dir = paths.fonts_dir(args.root)

    ttf = target_dir / "materialdesignicons-webfont.ttf"
    print("scarico materialdesignicons-webfont.ttf ...", end=" ", flush=True)
    with urllib.request.urlopen(icons.MDI_TTF_URL, timeout=60) as r:
        ttf.write_bytes(r.read())
    print(f"{ttf.stat().st_size // 1024} KB")

    print("scarico la mappa dei glifi ...", end=" ", flush=True)
    with urllib.request.urlopen(icons.MDI_MAP_URL, timeout=60) as r:
        scss = r.read().decode("utf-8", errors="replace")
    mapping = icons.parse_mdi_scss(scss)
    if not mapping:
        print("FALLITA: il formato SCSS di MDI e' cambiato")
        return 1
    (target_dir / "mdi-map.json").write_text(json.dumps(mapping))
    print(f"{len(mapping)} glifi MDI disponibili")
    return 0


def _install_agent(args) -> int:
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    module_dir = str(Path(__file__).resolve().parent.parent)
    p.write_text(render_plist(sys.executable, module_dir))
    subprocess.run(["/bin/launchctl", "unload", str(p)], capture_output=True,
                   check=False)
    r = subprocess.run(["/bin/launchctl", "load", str(p)], capture_output=True,
                       text=True, check=False)
    if r.returncode != 0:
        print(f"launchctl load ha risposto: {r.stderr.strip()}")
        return 1
    print(f"LaunchAgent installato e caricato: {p}")
    print("Log in /tmp/macdeck.log e /tmp/macdeck.err")
    return 0


def _uninstall_agent(args) -> int:
    p = plist_path()
    if not p.exists():
        print("nessun LaunchAgent installato")
        return 0
    subprocess.run(["/bin/launchctl", "unload", str(p)], capture_output=True,
                   check=False)
    p.unlink()
    print("LaunchAgent rimosso")
    return 0


def _token(args) -> int:
    print(paths.load_or_create_token(args.root))
    return 0


def _pair(args) -> int:
    """Insegna al deck la rete a cui il Mac e' attaccato adesso."""
    # Il firmware non alza piu' l'access point di ripiego ne' il portale, e
    # senza portale la via WiFi non ha nessuno con cui parlare. Dirlo qui,
    # prima di qualunque chiamata di rete, invece di lasciar cercare per
    # un'ora un access point che non c'e': e' esattamente il tempo che e'
    # costato scoprirlo dal lato sbagliato. Il ramo WiFi qui sotto resta
    # perche' vale per un firmware che il portale lo alzi: la guardia dice
    # che questo non e' quel firmware, non che quel codice sia sbagliato.
    if not args.usb:
        print("  KO   questo firmware non alza piu' un portale WiFi: le due")
        print("       reti (casa e ufficio) stanno in firmware/secrets.yaml,")
        print("       e una rete insegnata a caldo le CANCELLEREBBE.")
        print("       Per una rete nuova sul momento: `macdeck pair --usb`.")
        print("       Per una rete stabile: secrets.yaml + `esphome run`.")
        return 1

    ex = Executor()

    if args.usb:
        porte = sorted(Path("/dev").glob("cu.usbmodem*"))
        porta = args.port or (str(porte[0]) if porte else None)
        if not porta:
            print("  KO   nessun cavo dati trovato in /dev/cu.usbmodem*")
            print("       Collega il deck al Mac con un cavo DATI: molti cavi")
            print("       da ricarica non hanno i fili per i dati.")
            return 1
        ssid = args.ssid or pairing.current_ssid(ex)
        if not ssid:
            print(f"  KO   {pairing.SSID_ILLEGGIBILE}")
            return 1
        psk = args.password or pairing.wifi_password(ex, ssid)
        if not psk:
            print(f"  KO   password di '{ssid}' non leggibile dal portachiavi")
            print("       Riprova con --password 'la-password'")
            return 1
        print(f"  --   passo '{ssid}' al deck sul cavo {porta}...")
        esito = pairing.pair_over_usb(porta, ssid, psk)
    else:
        ssid_ora = args.ssid or pairing.current_ssid(ex)
        print(f"  --   rete attuale: {ssid_ora or 'nessuna'}")
        print("  --   il Mac restera' senza rete un minuto circa, e fino a")
        print("       tre minuti e mezzo nel caso peggiore: cinque agganci")
        print("       da 30 s di timeout piu' un minuto di attesa in tutto.")
        esito = pairing.pair_over_wifi(ex, ssid=args.ssid,
                                       password=args.password)

    if not esito.ok:
        print(f"  KO   {esito.error}")
        if not args.usb:
            print("       Ripiego: collega il cavo dati e usa `macdeck pair --usb`.")
        return 1

    print(f"  OK   rete '{esito.ssid}' passata al deck")
    print("  --   aspetto che si faccia vivo...")
    for _ in range(12):
        time.sleep(2.5)
        trovato = discovery_find()
        if trovato:
            print(f"  OK   deck in rete su {trovato}")
            return 0
    print("  --   non si e' ancora visto. Puo' metterci qualche secondo in piu';")
    print("       `macdeck doctor` dice se e' arrivato.")
    return 0


def discovery_find():
    from .discovery import find_deck
    try:
        return find_deck(4.0)
    except Exception:                                          # noqa: BLE001
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="macdeck")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn, help_text in (
        ("serve", _serve, "avvia il server"),
        ("doctor", _doctor, "diagnostica permessi e configurazione"),
        ("fetch-fonts", _fetch_fonts, "scarica il font MDI"),
        ("install-agent", _install_agent, "installa il LaunchAgent"),
        ("uninstall-agent", _uninstall_agent, "rimuove il LaunchAgent"),
        ("token", _token, "stampa il token condiviso"),
        ("pair", _pair, "insegna al deck una rete nuova, sul cavo dati"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--root", type=Path, default=None,
                       help="directory di configurazione alternativa (per i test)")
        if name == "pair":
            p.add_argument("--usb", action="store_true",
                           help="passa la rete sul cavo dati: con questo "
                                "firmware e' l'unica via, ed e' obbligatoria")
            p.add_argument("--port", default=None,
                           help="porta seriale (di norma si trova da sola)")
            p.add_argument("--ssid", default=None,
                           help="rete da passare (di norma quella attuale)")
            p.add_argument("--password", default=None,
                           help="password della rete, se il portachiavi non la da'")
        p.set_defaults(func=fn)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 2)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
