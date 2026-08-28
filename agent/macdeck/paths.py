"""Posizioni su disco e token condiviso.

Ogni funzione accetta `root` per permettere ai test di lavorare in una
tmp_path invece che nella home reale.
"""

from __future__ import annotations

import secrets
from pathlib import Path


def _root(root: Path | None) -> Path:
    return root if root is not None else Path.home() / ".config"


def config_dir(root: Path | None = None) -> Path:
    d = _root(root) / "macdeck"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def _subdir(name: str, root: Path | None) -> Path:
    d = config_dir(root) / name
    d.mkdir(exist_ok=True)
    return d


def fonts_dir(root: Path | None = None) -> Path:
    return _subdir("fonts", root)


def cache_dir(root: Path | None = None) -> Path:
    return _subdir("cache", root)


def layout_file(root: Path | None = None) -> Path:
    return config_dir(root) / "layout.yaml"


def token_file(root: Path | None = None) -> Path:
    return config_dir(root) / "token"


def load_or_create_token(root: Path | None = None) -> str:
    f = token_file(root)
    if f.exists():
        existing = f.read_text().strip()
        if existing:
            return existing
    token = secrets.token_hex(16)
    f.write_text(token + "\n")
    f.chmod(0o600)
    return token


def firmware_secrets(root: Path | None = None) -> Path:
    """Dove sta la chiave di cifratura dell'API del deck.

    E' lo stesso file che legge ESPHome quando compila: tenerne una copia
    nella configurazione dell'agent significherebbe due verita' che prima o
    poi divergono. Un file omonimo dentro la cartella di configurazione ha
    comunque la precedenza, per chi tiene il firmware altrove.
    """
    override = config_dir(root) / "secrets.yaml"
    if override.exists():
        return override
    return Path(__file__).resolve().parent.parent.parent / "firmware" / "secrets.yaml"
