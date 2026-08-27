import stat

from macdeck import paths


def test_config_dir_viene_creata_con_permessi_ristretti(tmp_path):
    d = paths.config_dir(root=tmp_path)
    assert d.is_dir()
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_token_e_stabile_fra_chiamate(tmp_path):
    primo = paths.load_or_create_token(root=tmp_path)
    secondo = paths.load_or_create_token(root=tmp_path)
    assert primo == secondo
    assert len(primo) == 32
    assert stat.S_IMODE(paths.token_file(root=tmp_path).stat().st_mode) == 0o600


def test_sottodirectory_previste(tmp_path):
    assert paths.fonts_dir(root=tmp_path).is_dir()
    assert paths.cache_dir(root=tmp_path).is_dir()
    assert paths.layout_file(root=tmp_path).name == "layout.yaml"
