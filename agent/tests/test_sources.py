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


LSAPPINFO_FRONT = 'ASN:0x0-0x6ba0d9a2:\n'
LSAPPINFO_INFO = '''\
"LSDisplayName"="iTerm"
"CFBundleIdentifier"="com.googlecode.iterm2"
"CFBundleExecutablePath"="/Applications/iTerm.app/Contents/MacOS/iTerm2"
'''


def _ctx(last=None):
    return sources.ProbeContext(running=frozenset(), now=0.0, last=last or {})


def test_parse_lsappinfo_info_legge_le_coppie_chiave_valore():
    d = sources.parse_lsappinfo_info(LSAPPINFO_INFO)
    assert d["LSDisplayName"] == "iTerm"
    assert d["CFBundleIdentifier"] == "com.googlecode.iterm2"


def test_front_espone_eseguibile_nome_visibile_e_bundle(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out=LSAPPINFO_INFO),
    }
    f = sources.front(fake_ex, _ctx())
    assert f == {"app": "iTerm2", "name": "iTerm",
                 "bundle": "com.googlecode.iterm2", "changed": True}


def test_front_passa_lasn_a_info(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out=LSAPPINFO_INFO),
    }
    sources.front(fake_ex, _ctx())
    info = [c for c in fake_ex.calls if c[1] == "info"][0]
    assert "ASN:0x0-0x6ba0d9a2:" in info


def test_front_changed_e_falso_se_lapp_e_la_stessa_di_prima(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out=LSAPPINFO_INFO),
    }
    prima = {"app": "iTerm2", "name": "iTerm",
             "bundle": "com.googlecode.iterm2", "changed": True}
    assert sources.front(fake_ex, _ctx(last=prima))["changed"] is False


def test_front_senza_bundle_lascia_bundle_none(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out='"LSDisplayName"="Boh"\n"CFBundleExecutablePath"="/x/Boh.app/Contents/MacOS/Boh"\n'),
    }
    f = sources.front(fake_ex, _ctx())
    assert f["bundle"] is None and f["app"] == "Boh"


def test_front_fallisce_se_lsappinfo_fallisce(fake_ex):
    fake_ex.replies = {"lsappinfo front": R(False, error="boh")}
    assert sources.front(fake_ex, _ctx()) is None


def test_front_senza_eseguibile_usa_il_nome_visibile(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out=LSAPPINFO_FRONT),
        "lsappinfo info": R(True, out='"LSDisplayName"="Boh"\n"CFBundleIdentifier"="it.boh.app"\n'),
    }
    f = sources.front(fake_ex, _ctx())
    assert f["app"] == "Boh"
    assert f["bundle"] == "it.boh.app"


def test_front_fallisce_se_info_fallisce(fake_ex):
    fake_ex.replies = {
        "lsappinfo front": R(True, out="ASN:0x0-0x1:\n"),
        "lsappinfo info": R(False, error="boh"),
    }
    assert sources.front(fake_ex, _ctx()) is None


def test_front_e_registrato_con_cadenza_di_un_secondo():
    assert sources.REGISTRY["front"].every == 1.0
    assert sources.REGISTRY["front"].app == ()
