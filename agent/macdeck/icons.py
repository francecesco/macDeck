"""Risoluzione delle icone: app:, mdi:, file:, emoji:, text:.

Contratto rigido: resolve() non solleva mai e restituisce sempre
un'immagine RGBA quadrata della dimensione richiesta. Un'app disinstallata o
un glifo inesistente devono dare una tile con un punto di domanda, non un
500 che lascia il display con l'immagine precedente.
"""

from __future__ import annotations

import json
import plistlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import paths

MDI_TTF_URL = (
    "https://raw.githubusercontent.com/Templarian/MaterialDesign-Webfont/"
    "master/fonts/materialdesignicons-webfont.ttf"
)
# Il repo del webfont non pubblica un meta.json: la mappa nome -> codepoint
# sta nel sorgente SCSS, che viene scaricato e convertito in JSON una volta.
MDI_MAP_URL = (
    "https://raw.githubusercontent.com/Templarian/MaterialDesign-Webfont/"
    "master/scss/_variables.scss"
)

EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
TEXT_FONT = "/System/Library/Fonts/SFNS.ttf"

# Apple Color Emoji e' bitmap: Pillow la carica solo alle taglie presenti
# nel font. Si prova in ordine e si scala il risultato.
_EMOJI_SIZES = (137, 160, 96, 109, 64, 32, 20)


def _mdi_ttf(root: Path | None = None) -> Path:
    return paths.fonts_dir(root) / "materialdesignicons-webfont.ttf"


def _mdi_map(root: Path | None = None) -> Path:
    return paths.fonts_dir(root) / "mdi-map.json"


def parse_mdi_scss(text: str) -> dict[str, str]:
    """Estrae la mappa nome -> codepoint dal sorgente SCSS di MDI.

    Le righe hanno la forma `  "abacus": F16E0,` dentro `$mdi-icons: (...)`.
    """
    return dict(re.findall(r'"([a-z0-9-]+)":\s*([0-9A-Fa-f]{4,6})', text))


def mdi_available(root: Path | None = None) -> bool:
    return _mdi_ttf(root).exists() and _mdi_map(root).exists()


def mdi_names(root: Path | None = None) -> list[str]:
    if not mdi_available(root):
        return []
    try:
        return sorted(json.loads(_mdi_map(root).read_text()))
    except (json.JSONDecodeError, OSError):
        return []


def fallback(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse(
        [(2, 2), (size - 3, size - 3)],
        outline=(255, 170, 60, 255),
        width=max(2, size // 24),
    )
    try:
        font = ImageFont.truetype(TEXT_FONT, int(size * 0.55))
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), "?", font=font, fill=(255, 170, 60, 255),
           anchor="mm")
    return im


def _icon_name_from_plist(bundle: Path) -> str | None:
    plist = bundle / "Contents" / "Info.plist"
    if not plist.exists():
        return None
    try:
        with plist.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception:
        return None
    name = data.get("CFBundleIconFile") or data.get("CFBundleIconName")
    return name if isinstance(name, str) else None


APP_DIRS = (
    "/Applications",
    "/Applications/Utilities",
    "/System/Applications",
    "/System/Applications/Utilities",
    "/System/Library/CoreServices",
    "~/Applications",
)


def app_dirs() -> list[Path]:
    return [d for d in (Path(p).expanduser() for p in APP_DIRS) if d.is_dir()]


def bundle_identifier(bundle: Path) -> str | None:
    plist = bundle / "Contents" / "Info.plist"
    if not plist.exists():
        return None
    try:
        with plist.open("rb") as fh:
            return plistlib.load(fh).get("CFBundleIdentifier")
    except Exception:
        return None



# ------------------------------------------------- i nomi che mostra il Finder

MDLS = "/usr/bin/mdls"
_display_cache: dict[str, str] | None = None


def reset_display_names() -> None:
    """Svuota la cache. Serve ai test e dopo l'installazione di un'app."""
    global _display_cache
    _display_cache = None


def display_names(bundles=None, ex=None) -> dict[str, str]:
    """Mappa percorso -> nome come lo mostra il Finder.

    Serve perche' sul disco un'app si chiama in inglese ma il Finder la
    mostra tradotta: su un Mac italiano succede a piu' di un terzo delle app
    installate (Preview/Anteprima, Calculator/Calcolatrice...). Chi configura
    legge il nome tradotto, lo scrive, e non viene trovato niente.

    Il nome tradotto non sta nel bundle — per le app Apple lo sa solo
    LaunchServices — quindi tocca chiedere a `mdls`. Una sola invocazione per
    tutte le app: farne una per bundle significherebbe centinaia di processi.
    """
    global _display_cache
    if bundles is None:
        if _display_cache is not None:
            return _display_cache
        bundles = [b for d in app_dirs() for b in sorted(d.glob("*.app"))]
        salva = True
    else:
        salva = False
    bundles = list(bundles)
    if not bundles:
        return {}

    from .executor import Executor
    ex = ex or Executor()
    r = ex.run([MDLS, "-name", "kMDItemDisplayName", "-raw",
                *[str(b) for b in bundles]], timeout=20.0)
    mappa: dict[str, str] = {}
    if r.ok and r.out:
        for bundle, nome in zip(bundles, r.out.split("\0")):
            nome = nome.strip().removesuffix(".app")
            if nome and nome != "(null)":
                mappa[str(bundle)] = nome
    if salva:
        _display_cache = mappa
    return mappa


def locate_bundle(target: str):
    """Come _locate_bundle, ma pubblica: la usano la GUI e i test."""
    return _locate_bundle(target)

def _locate_bundle(target: str) -> Path | None:
    """Risolve un nome ("Slack") o un bundle id in un percorso .app.

    Deliberatamente NON usa `osascript -e 'path to application ...'`: quella
    strada puo' bloccarsi indefinitamente in attesa del prompt di permesso
    Automazione, e un agent che si appende mentre renderizza una tile e' molto
    peggio di un'icona non trovata. La ricerca su filesystem e' immediata e
    non chiede permessi.
    """
    wanted = target.lower().removesuffix(".app")
    is_id = "." in target and "/" not in target

    for folder in app_dirs():
        for bundle in folder.glob("*.app"):
            if bundle.stem.lower() == wanted:
                return bundle
    if is_id:
        for folder in app_dirs():
            for bundle in folder.glob("*.app"):
                if (bundle_identifier(bundle) or "").lower() == target.lower():
                    return bundle
    # il nome tradotto che mostra il Finder: "Anteprima" -> Preview.app
    for percorso, mostrato in display_names().items():
        if mostrato.lower() == wanted:
            return Path(percorso)

    # ultimo tentativo: corrispondenza parziale, prima sul nome su disco e
    # poi su quello tradotto
    for folder in app_dirs():
        for bundle in sorted(folder.glob("*.app")):
            if wanted in bundle.stem.lower():
                return bundle
    for percorso, mostrato in display_names().items():
        if wanted in mostrato.lower():
            return Path(percorso)
    return None


def app_icon_path(target: str) -> Path | None:
    """Percorso del .icns dentro un bundle .app, o None."""
    bundle = Path(target).expanduser()
    if not bundle.is_dir():
        located = _locate_bundle(target)
        if located is None:
            return None
        bundle = located
    resources = bundle / "Contents" / "Resources"
    if not resources.is_dir():
        return None
    named = _icon_name_from_plist(bundle)
    if named:
        candidate = resources / named
        if candidate.suffix != ".icns":
            candidate = candidate.with_suffix(".icns")
        if candidate.exists():
            return candidate
    icns = sorted(
        resources.glob("*.icns"), key=lambda p: p.stat().st_size, reverse=True
    )
    return icns[0] if icns else None


def _from_app(target: str, size: int) -> Image.Image:
    icns = app_icon_path(target)
    if icns is None:
        raise FileNotFoundError(f"nessun .icns per {target!r}")
    with Image.open(icns) as im:
        # Pillow apre un .icns direttamente alla risoluzione maggiore
        # disponibile. Non si tocca im.size: le voci di info["sizes"] sono
        # terne (w, h, scala) e assegnarle rompe load() da Pillow 12.
        return im.convert("RGBA").resize((size, size), Image.LANCZOS)


def _from_file(target: str, size: int) -> Image.Image:
    p = Path(target).expanduser()
    with Image.open(p) as im:
        return im.convert("RGBA").resize((size, size), Image.LANCZOS)


def _fit(im: Image.Image, size: int) -> Image.Image:
    box = im.getbbox()
    if box:
        im = im.crop(box)
    im.thumbnail((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    return out


def _render_mono_glyph(glyph: str, size: int, font_path: str) -> Image.Image:
    px = int(size * 0.9)
    font = ImageFont.truetype(font_path, px)
    im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((size, size), glyph, font=font, anchor="mm", fill=(255, 255, 255, 255))
    return _fit(im, size)


def _render_emoji(glyph: str, size: int) -> Image.Image:
    last: Exception | None = None
    for px in _EMOJI_SIZES:
        try:
            font = ImageFont.truetype(EMOJI_FONT, px)
        except OSError as e:
            last = e
            continue
        canvas = px * 2
        im = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.text((canvas / 2, canvas / 2), glyph, font=font, anchor="mm",
               embedded_color=True)
        if im.getbbox():
            return _fit(im, size)
    raise OSError(f"Apple Color Emoji non caricabile: {last}")


def _from_mdi(name: str, size: int, root: Path | None) -> Image.Image:
    if not mdi_available(root):
        raise FileNotFoundError("font MDI assente: esegui `macdeck fetch-fonts`")
    mapping = json.loads(_mdi_map(root).read_text())
    codepoint = mapping.get(name)
    if codepoint is None:
        raise KeyError(f"glifo MDI ignoto: {name!r}")
    return _render_mono_glyph(chr(int(codepoint, 16)), size, str(_mdi_ttf(root)))


def _from_text(text: str, size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    shown = text[:3]
    ratio = 0.62 if len(shown) == 1 else 0.42 if len(shown) == 2 else 0.32
    try:
        font = ImageFont.truetype(TEXT_FONT, max(8, int(size * ratio)))
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), shown, font=font, anchor="mm",
           fill=(255, 255, 255, 255))
    return im


SCHEMES = ("app", "file", "mdi", "emoji", "text")

IMG_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".icns",
                ".tiff", ".tif")


def guess_scheme(spec: str) -> tuple[str, str]:
    """Indovina lo schema quando non c'e'.

    Chi configura incolla un percorso, o scrive il nome dell'app, e si
    aspetta che funzioni: pretendere il prefisso "app:" significa fallire in
    silenzio proprio sul gesto piu' naturale — ed e' successo davvero, con
    tre tile rimaste senza icona e nessun messaggio a dire perche'.
    """
    testo = spec.strip()
    p = Path(testo).expanduser()
    if testo.endswith(".app") or p.suffix == ".app":
        return "app", testo
    if p.suffix.lower() in IMG_SUFFIXES:
        return "file", testo
    if p.is_file():
        return "file", testo
    if "/" not in testo and _locate_bundle(testo) is not None:
        return "app", testo
    return "text", testo


def resolve(spec: str, size: int, *, root: Path | None = None) -> Image.Image:
    if not spec:
        return fallback(size)
    scheme, _, target = spec.partition(":")
    if scheme not in SCHEMES:
        # Nessuno schema, o uno che non conosciamo: si guarda com'e' fatto
        # il valore invece di arrendersi.
        scheme, target = guess_scheme(spec)
    try:
        if scheme == "app":
            return _from_app(target, size)
        if scheme == "file":
            return _from_file(target, size)
        if scheme == "mdi":
            return _from_mdi(target, size, root)
        if scheme == "emoji":
            return _render_emoji(target, size)
        if scheme == "text":
            return _from_text(target, size)
    except Exception:
        return fallback(size)
    return fallback(size)
