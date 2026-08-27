"""Interfaccia a riga di comando.

`doctor` esiste perche' il permesso Accessibilita' e' il punto in cui questo
progetto si rompe in modo silenzioso: osascript non da' un errore chiaro, i
tasti semplicemente non arrivano.
"""

from __future__ import annotations

import argparse
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


def build_serve_app(root: Path | None = None):
    store = LayoutStore(paths.layout_file(root))
    store.load()
    token = paths.load_or_create_token(root)
    app = create_app(
        store=store,
        cache=TileCache(),
        probe=StateProbe(Executor()),
        executor=Executor(),
        token=token,
        root=root,
    )
    return app, token


def _serve(args) -> int:
    import uvicorn

    app, token = build_serve_app(args.root)
    print(f"MacDeck su http://127.0.0.1:{PORT}  (token: {token})", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    return 0


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

    if icons.mdi_available(args.root):
        print("  OK   font MDI presente")
    else:
        print("  --   font MDI assente: esegui `macdeck fetch-fonts` "
              "se vuoi usare le icone mdi:")

    stato = StateProbe(ex, ttl=0.0).snapshot()
    vol = stato["volume"]["level"]
    print(f"  {'OK' if vol is not None else 'KO'}   volume leggibile: {vol}")
    print(f"  --   media: {stato['media']['app'] or 'nessun player attivo'}")

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
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--root", type=Path, default=None,
                       help="directory di configurazione alternativa (per i test)")
        p.set_defaults(func=fn)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 2)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
