#!/usr/bin/env python3
"""Genera l'icona di MacDeck.app.

Disegna una griglia di tasti (uno acceso) su fondo scuro, in tutte le
misure che serve un .icns, e la impacchetta con `iconutil`.

    agent/.venv/bin/python mac-app/icona.py

Il risultato, mac-app/Resources/MacDeck.icns, e' committato: cosi' la
build Swift non ha bisogno ne' di Python ne' di Pillow.
"""
import pathlib
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter

QUI = pathlib.Path(__file__).resolve().parent
ICNS = QUI / "Resources" / "MacDeck.icns"

FONDO = (24, 27, 34, 255)      # grigio molto scuro, come la cornice del deck
TASTO = (43, 68, 102, 255)     # blu spento: i tasti a riposo
ACCESO = (74, 158, 255, 255)   # #4A9EFF, l'accento del deck
BAGLIORE = (74, 158, 255, 90)

SUPER = 4                      # disegno 4x e rimpicciolisco: bordi puliti


def disegna(lato: int) -> Image.Image:
    s = lato * SUPER
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Fondo: quadrato con gli angoli tondi, nei margini che usa macOS.
    bordo = s * 0.055
    d.rounded_rectangle([bordo, bordo, s - bordo, s - bordo],
                        radius=s * 0.225, fill=FONDO)

    # Griglia 3x3 centrata. Niente scritte: deve reggere a 16 pixel.
    area = s * 0.62
    salto = area * 0.115
    tasto = (area - 2 * salto) / 3
    x0 = y0 = (s - area) / 2
    raggio = tasto * 0.26

    # Il bagliore del tasto acceso va sotto ai tasti, sfocato.
    cx = x0 + tasto + salto
    alone = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(alone).rounded_rectangle(
        [cx - tasto * 0.35, cx - tasto * 0.35,
         cx + tasto * 1.35, cx + tasto * 1.35],
        radius=raggio * 2, fill=BAGLIORE)
    img.alpha_composite(alone.filter(ImageFilter.GaussianBlur(tasto * 0.30)))

    for riga in range(3):
        for col in range(3):
            x = x0 + col * (tasto + salto)
            y = y0 + riga * (tasto + salto)
            colore = ACCESO if (riga, col) == (1, 1) else TASTO
            d.rounded_rectangle([x, y, x + tasto, y + tasto],
                                radius=raggio, fill=colore)

    return img.resize((lato, lato), Image.LANCZOS)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = pathlib.Path(tmp) / "MacDeck.iconset"
        iconset.mkdir()
        for lato in (16, 32, 128, 256, 512):
            disegna(lato).save(iconset / f"icon_{lato}x{lato}.png")
            disegna(lato * 2).save(iconset / f"icon_{lato}x{lato}@2x.png")

        ICNS.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset),
                        "-o", str(ICNS)], check=True)

        # Un'anteprima da guardare a occhio, fuori dal repo.
        anteprima = pathlib.Path(tempfile.gettempdir()) / "macdeck-icona.png"
        shutil.copy(iconset / "icon_512x512.png", anteprima)
        print(f"fatto: {ICNS}\nanteprima: {anteprima}")


if __name__ == "__main__":
    main()
