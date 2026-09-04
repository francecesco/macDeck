from macdeck import sources
from macdeck.executor import Result as R

LSAPPINFO_LIST = '''\
 1) "loginwindow" ASN:0x0-0x3003:
    bundleID="com.apple.loginwindow"
    pid = 410 type="UIElement"
 2) "Mail" ASN:0x0-0x1a01a:
    bundleID="com.apple.mail"
    bundle path="/System/Applications/Mail.app"
 3) "Slack" ASN:0x0-0x6ba0d9a2:
    bundleID="com.tinyspeck.slackmacgap"
'''


def test_il_registro_conosce_le_sonde_di_base():
    assert {"volume", "media", "system"} <= set(sources.REGISTRY)


def test_known_keys_elenca_chiave_punto_campo():
    keys = sources.known_keys()
    assert "volume.level" in keys
    assert "media.title" in keys
    assert keys == sorted(keys)


def test_empty_snapshot_ha_una_voce_per_sonda_con_tutti_i_campi():
    snap = sources.empty_snapshot()
    assert snap["volume"] == {"level": None, "muted": None}
    assert snap["media"]["app"] is None


def test_running_apps_legge_nomi_e_bundle_id_in_minuscolo(fake_ex):
    fake_ex.replies = {"lsappinfo list": R(True, out=LSAPPINFO_LIST)}
    running = sources.running_apps(fake_ex)
    assert "mail" in running
    assert "com.apple.mail" in running
    assert "com.tinyspeck.slackmacgap" in running
    assert "slack" in running


def test_running_apps_su_errore_e_vuoto(fake_ex):
    fake_ex.replies = {"lsappinfo list": R(False, error="boom")}
    assert sources.running_apps(fake_ex) == frozenset()
