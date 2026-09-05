import json
import os
import time
from pathlib import Path

from macdeck import paths, sources
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


def test_mail_legge_le_non_lette(fake_ex):
    fake_ex.replies = {"unread count": R(True, out="12\n")}
    assert sources.mail(fake_ex, _ctx()) == {
        "unread": 12, "latest_subject": None, "latest_sender": None, "drafts": None}


def test_mail_con_output_strano_fallisce(fake_ex):
    fake_ex.replies = {"unread count": R(True, out="boh\n")}
    assert sources.mail(fake_ex, _ctx()) is None


def test_mail_e_vincolata_allapp_in_esecuzione():
    assert sources.REGISTRY["mail"].app == ("com.apple.mail",)
    assert sources.REGISTRY["mail"].every == 5.0


def test_slack_badge_presente(fake_ex):
    fake_ex.replies = {"AXStatusLabel": R(True, out="3\n")}
    assert sources.slack(fake_ex, _ctx()) == {"badge": "3"}


def test_slack_senza_badge_da_null_non_fallimento(fake_ex):
    fake_ex.replies = {"AXStatusLabel": R(True, out="missing value\n")}
    assert sources.slack(fake_ex, _ctx()) == {"badge": None}


def test_slack_errore_di_accessibilita_e_un_fallimento(fake_ex):
    fake_ex.replies = {"AXStatusLabel": R(False, error="-1719")}
    assert sources.slack(fake_ex, _ctx()) is None


def test_slack_e_vincolata_allapp():
    assert sources.REGISTRY["slack"].app == ("com.tinyspeck.slackmacgap",)


def test_calendar_legge_conteggio_e_prossimo_evento(fake_ex):
    fake_ex.replies = {"every event of c": R(True, out="3\n14:30\tRiunione sprint\n")}
    assert sources.calendar(fake_ex, _ctx()) == {
        "next": "Riunione sprint", "next_at": "14:30", "count_today": 3}


def test_calendar_senza_eventi(fake_ex):
    fake_ex.replies = {"every event of c": R(True, out="0\n\n")}
    assert sources.calendar(fake_ex, _ctx()) == {
        "next": None, "next_at": None, "count_today": 0}


def test_calendar_ha_cadenza_lenta_e_app_vincolata():
    src = sources.REGISTRY["calendar"]
    assert src.every == 60.0
    assert src.app == ("com.apple.ical",)


def test_calendar_timeout_e_un_fallimento(fake_ex):
    fake_ex.replies = {"every event of c": R(False, error="timeout dopo 10.0s")}
    assert sources.calendar(fake_ex, _ctx()) is None


def test_calendar_conteggio_non_numerico_fallisce(fake_ex):
    fake_ex.replies = {"every event of c": R(True, out="boh\n14:30\tX\n")}
    assert sources.calendar(fake_ex, _ctx()) is None


# --------------------------------------------------------- Claude Code

STATUS = {
    "session_id": "abc-123",
    "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
    "workspace": {"current_dir": str(Path.home() / "macdeck")},
    "context_window": {"remaining_percentage": 38.4},
}


def _scrivi(dir_, nome, dati, eta_s=0):
    p = dir_ / f"{nome}.json"
    p.write_text(json.dumps(dati))
    if eta_s:
        t = time.time() - eta_s
        os.utime(p, (t, t))
    return p


def _ctx_root(root, running=()):
    return sources.ProbeContext(running=frozenset(running), now=0.0, root=root)


def test_claude_vivo_legge_modello_percentuale_cartella_e_branch(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS)
    fake_ex.replies = {
        "pgrep": R(True, out="4242\n"),
        "branch --show-current": R(True, out="main\n"),
    }
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c == {"alive": True, "model": "Fable 5.1", "remaining": 38.4,
                 "dir": "~/macdeck", "branch": "main", "session": "abc-123",
                 "session_used": None, "week_used": None, "session_resets": None}


def test_claude_senza_processo_non_e_vivo_ma_i_dati_restano(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS)
    fake_ex.replies = {"pgrep": R(False, error="exit 1"),
                       "branch --show-current": R(True, out="main\n")}
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["alive"] is False
    assert c["model"] == "Fable 5.1"


def test_claude_con_file_vecchio_non_e_vivo(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS, eta_s=sources.CLAUDE_STALE_S + 60)
    fake_ex.replies = {"pgrep": R(True, out="4242\n")}
    assert sources.claude(fake_ex, _ctx_root(tmp_path))["alive"] is False


def test_claude_sceglie_il_file_piu_recente(fake_ex, tmp_path):
    d = paths.claude_dir(tmp_path)
    _scrivi(d, "vecchia", {**STATUS, "session_id": "vecchia",
                           "model": {"display_name": "Vecchio"}}, eta_s=300)
    _scrivi(d, "nuova", {**STATUS, "session_id": "nuova"})
    fake_ex.replies = {"pgrep": R(True, out="1\n")}
    assert sources.claude(fake_ex, _ctx_root(tmp_path))["session"] == "nuova"


def test_claude_cancella_i_file_piu_vecchi_di_un_giorno(fake_ex, tmp_path):
    d = paths.claude_dir(tmp_path)
    stantio = _scrivi(d, "stantio", STATUS, eta_s=sources.CLAUDE_PURGE_S + 10)
    _scrivi(d, "nuova", STATUS)
    sources.claude(fake_ex, _ctx_root(tmp_path))
    assert not stantio.exists()


def test_claude_senza_file_e_il_valore_vuoto_non_un_fallimento(fake_ex, tmp_path):
    paths.claude_dir(tmp_path)
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c == {"alive": False, "model": None, "remaining": None,
                 "dir": None, "branch": None, "session": None,
                 "session_used": None, "week_used": None, "session_resets": None}


def test_claude_file_malformato_vale_come_assente(fake_ex, tmp_path):
    (paths.claude_dir(tmp_path) / "rotto.json").write_text("{non json")
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["alive"] is False and c["model"] is None


def test_claude_senza_branch_lascia_null(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS)
    fake_ex.replies = {"pgrep": R(True, out="1\n"),
                       "branch --show-current": R(False, error="not a git repository")}
    assert sources.claude(fake_ex, _ctx_root(tmp_path))["branch"] is None


def test_claude_non_abbrevia_una_cartella_che_somiglia_alla_home(fake_ex, tmp_path):
    fake_home = str(Path.home())
    fake_cwd = fake_home + "X/repo"
    _scrivi(paths.claude_dir(tmp_path), "abc-123",
            {**STATUS, "workspace": {"current_dir": fake_cwd}})
    fake_ex.replies = {"pgrep": R(True, out="1\n"),
                       "branch --show-current": R(True, out="main\n")}
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["dir"] == fake_cwd


def test_claude_la_home_stessa_diventa_tilde(fake_ex, tmp_path):
    fake_home = str(Path.home())
    _scrivi(paths.claude_dir(tmp_path), "abc-123",
            {**STATUS, "workspace": {"current_dir": fake_home}})
    fake_ex.replies = {"pgrep": R(True, out="1\n"),
                       "branch --show-current": R(True, out="main\n")}
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["dir"] == "~"


def test_claude_non_e_vincolata_a_unapp_gui():
    assert sources.REGISTRY["claude"].app == ()
    assert sources.REGISTRY["claude"].every == 5.0


# --------------------------------------------------- claude: limiti di utilizzo

def test_claude_legge_utilizzo_sessione_settimana_e_reset(fake_ex, tmp_path):
    import datetime as dt
    reset = dt.datetime(2026, 9, 5, 19, 0).timestamp()      # locale
    dati = {**STATUS, "rate_limits": {
        "five_hour": {"used_percentage": 11, "resets_at": reset},
        "seven_day": {"used_percentage": 1, "resets_at": reset + 86400},
    }}
    _scrivi(paths.claude_dir(tmp_path), "abc-123", dati)
    fake_ex.replies = {"pgrep": R(True, out="1\n")}
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["session_used"] == 11.0
    assert c["week_used"] == 1.0
    assert c["session_resets"] == "19:00"


def test_claude_senza_rate_limits_lascia_le_chiavi_vuote(fake_ex, tmp_path):
    _scrivi(paths.claude_dir(tmp_path), "abc-123", STATUS)
    fake_ex.replies = {"pgrep": R(True, out="1\n")}
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["session_used"] is None
    assert c["week_used"] is None
    assert c["session_resets"] is None


def test_claude_rate_limits_malformati_non_rompono_la_sonda(fake_ex, tmp_path):
    dati = {**STATUS, "rate_limits": {"five_hour": {"used_percentage": "boh",
                                                    "resets_at": "ieri"}}}
    _scrivi(paths.claude_dir(tmp_path), "abc-123", dati)
    fake_ex.replies = {"pgrep": R(True, out="1\n")}
    c = sources.claude(fake_ex, _ctx_root(tmp_path))
    assert c["alive"] is True
    assert c["session_used"] is None and c["session_resets"] is None


# ------------------------------------------------ finestra in primo piano

def test_window_legge_il_titolo_della_finestra(fake_ex):
    fake_ex.replies = {"front window": R(True, out="macdeck — sources.py\n")}
    assert sources.window(fake_ex, _ctx()) == {"title": "macdeck — sources.py"}


def test_window_senza_finestre_e_vuoto_non_fallimento(fake_ex):
    fake_ex.replies = {"front window": R(False, error="Impossibile ottenere front window")}
    assert sources.window(fake_ex, _ctx()) == {"title": None}


def test_window_ha_cadenza_di_due_secondi_senza_gate():
    assert sources.REGISTRY["window"].every == 2.0
    assert sources.REGISTRY["window"].app == ()


# ------------------------------------------------------ media, campi extra

def test_media_legge_album_durata_shuffle_ripeti_volume(fake_ex):
    fake_ex.replies = {"running_apps": R(True, out=(
        "Spotify\ntrue\nAnagrafe\nMarlene Kuntz\nHo ucciso paranoia\n"
        "245000\ntrue\nfalse\n30\n"))}
    m = sources.media(fake_ex, _ctx())
    assert m["album"] == "Ho ucciso paranoia"
    assert m["duration"] == "4:05"
    assert m["shuffle"] is True and m["repeat"] is False
    assert m["volume"] == 30


def test_media_con_le_sole_quattro_righe_di_ieri_resta_valido(fake_ex):
    fake_ex.replies = {"running_apps": R(True, out="Spotify\ntrue\nAnagrafe\nMarlene Kuntz\n")}
    m = sources.media(fake_ex, _ctx())
    assert m["title"] == "Anagrafe"
    assert m["album"] is None and m["duration"] is None
    assert m["shuffle"] is False and m["volume"] is None


def test_media_empty_ha_i_campi_nuovi():
    e = sources.REGISTRY["media"].empty
    assert {"album", "duration", "shuffle", "repeat", "volume"} <= set(e)


# ------------------------------------------------------- mail, campi extra

def test_mail_legge_ultimo_messaggio_e_bozze(fake_ex):
    fake_ex.replies = {"unread count": R(True, out=(
        "12\nFwd: SI.Ter - Datawarehouse\nLorena Brunetti <l.b@example.it>\n2\n"))}
    m = sources.mail(fake_ex, _ctx())
    assert m == {"unread": 12, "latest_subject": "Fwd: SI.Ter - Datawarehouse",
                 "latest_sender": "Lorena Brunetti", "drafts": 2}


def test_mail_con_inbox_vuota(fake_ex):
    fake_ex.replies = {"unread count": R(True, out="0\n\n\n0\n")}
    m = sources.mail(fake_ex, _ctx())
    assert m["unread"] == 0 and m["latest_subject"] is None and m["latest_sender"] is None


def test_mail_mittente_senza_nome_tiene_lindirizzo(fake_ex):
    fake_ex.replies = {"unread count": R(True, out="1\nCiao\n<solo@indirizzo.it>\n0\n")}
    assert sources.mail(fake_ex, _ctx())["latest_sender"] == "solo@indirizzo.it"


# ------------------------------------------------------------- WhatsApp

def test_whatsapp_badge(fake_ex):
    fake_ex.replies = {"AXStatusLabel": R(True, out="4\n")}
    assert sources.whatsapp(fake_ex, _ctx()) == {"badge": "4"}
    assert sources.REGISTRY["whatsapp"].app == ("net.whatsapp.whatsapp",)


# --------------------------------------------------------------- Chrome

def test_chrome_legge_titolo_sito_e_schede(fake_ex):
    fake_ex.replies = {"active tab": R(True, out=(
        "MacDeck — GitHub\nhttps://www.github.com/francecesco/macDeck/pulls\n17\n"))}
    assert sources.chrome(fake_ex, _ctx()) == {
        "title": "MacDeck — GitHub", "host": "github.com", "tabs": 17}


def test_chrome_senza_finestre(fake_ex):
    fake_ex.replies = {"active tab": R(True, out="\n\n0\n")}
    c = sources.chrome(fake_ex, _ctx())
    assert c["title"] is None and c["host"] is None and c["tabs"] == 0


def test_chrome_e_vincolato_allapp():
    assert sources.REGISTRY["chrome"].app == ("com.google.chrome",)
