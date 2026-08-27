# MacDeck — design

Stream Deck per macOS costruito su un display **Guition JC3248W535** (3.5",
320×480, ESP32-S3) con ESPHome + LVGL, pilotato da un agent Python sul Mac con
interfaccia web di configurazione.

Data: 2026-08-27 · Stato: approvato in brainstorming, pronto per il piano

---

## 1. Obiettivo

Un pannello touch da scrivania che mostri icone di applicazioni e scorciatoie,
le esegua sul MacBook, e mostri in tempo reale lo stato del Mac (volume, brano
in riproduzione, CPU, RAM, batteria).

I pulsanti si configurano da una **GUI web sul Mac**: aggiungere o rimappare un
pulsante non deve richiedere né ricompilazione né flash del firmware.

### Criteri di successo

1. Tocco → azione eseguita sul Mac in meno di 100 ms percepiti.
2. Salvataggio nella GUI → display aggiornato entro 3 s, senza flash.
3. Il firmware viene flashato una volta sola; ogni evoluzione successiva del
   contenuto del deck avviene lato Mac.
4. Aggiungere un nuovo *tipo* di azione costa una funzione Python.
5. Qualunque icona esistente (app installata, glifo MDI, immagine, testo) è
   utilizzabile senza toccare il firmware.

### Fuori scope (v1)

- Controllo di più Mac dallo stesso display.
- Funzionamento fuori dalla LAN del Mac.
- Emulazione USB/BLE HID (valutata e scartata: non dà feedback di stato).
- Integrazione con Home Assistant (il display è dedicato al Mac).

---

## 2. Hardware

Identificato leggendo il firmware di fabbrica in flash, che contiene le stringhe
`JC3248W535` e `guition`, più conferma via `esptool`.

| Parametro | Valore |
|---|---|
| Scheda | Guition JC3248W535 (venduta anche come "Diymore ESP32-S3 3.5\"") |
| Chip | ESP32-S3 rev v0.2 (QFN56), 16 MB flash quad, 8 MB PSRAM ottale (`N16R8`) |
| MAC | `AA:BB:CC:DD:EE:FF` |
| Display | 3.5" IPS 320×480, controller **AXS15231B**, bus **QSPI** |
| Touch | capacitivo integrato nell'AXS15231B, I²C addr `0x3B` |
| Extra | slot microSD (SDMMC), audio I²S, USB-Serial/JTAG nativo |
| Porta | `/dev/cu.usbmodem13301` |

### Pinout

| Funzione | GPIO |
|---|---|
| QSPI CLK | 47 |
| QSPI data | 21, 48, 40, 39 |
| QSPI CS | 45 (dal preset; è uno strapping pin → serve `ignore_strapping_warning`) |
| Backlight | 1 (PWM via LEDC) |
| Touch I²C SDA | 4 |
| Touch I²C SCL | 8 |

### Supporto ESPHome

ESPHome 2026.5.3 supporta la board **nativamente**, senza componenti custom —
differenza importante rispetto al progetto `plancia-ingresso`, che ha richiesto
`io_extension_ws` scritto a mano.

- `display: platform: mipi_spi` con `model: JC3248W535` (preset integrato: 320×480,
  cs_pin 45, data_rate 40 MHz, init sequence AXS15231, `draw_rounding: 8`)
- `touchscreen: platform: axs15231` (I²C `0x3B`, update interval 50 ms)
- `online_image` con `format: PNG` e azione `set_url` templatabile a runtime
- `http_request` con `capture_response` e `on_response`

Il backlight **non** è gestito dal preset: va dichiarato come `output: ledc` su
GPIO1 più `light: monochromatic`. Scelta deliberata — dà dimmerazione e
auto-spegnimento per inattività senza codice aggiuntivo.

Firmware di fabbrica salvato come backup (dump di `app0`, 2 MB) prima di
qualunque flash.

---

## 3. Architettura

Tre componenti, una sola sorgente di verità.

```
┌─────────────────────────┐         ┌──────────────────────────────────┐
│  JC3248W535 (ESPHome)   │  WiFi   │  Mac — agent `macdeck` (Python)  │
│                         │  HTTP   │  FastAPI + uvicorn su :8765      │
│  griglia 4×3 di slot    │◄───────►│                                  │
│  + header di stato      │         │  ┌────────────────────────────┐  │
│                         │         │  │ web UI config → browser    │  │
│  NESSUNA logica         │         │  ├────────────────────────────┤  │
│  NESSUN nome di app     │         │  │ registry azioni            │  │
│  NESSUNA icona          │         │  ├────────────────────────────┤  │
│                         │         │  │ renderer tile (Pillow)     │  │
└─────────────────────────┘         │  ├────────────────────────────┤  │
                                    │  │ ~/.config/macdeck/         │  │
                                    │  │   layout.yaml  ← VERITÀ    │  │
                                    │  │   cache/ (PNG renderizzati)│  │
                                    │  └────────────────────────────┘  │
                                    └──────────────────────────────────┘
```

Il firmware non sa che esiste Slack. Sa che esiste "lo slot 5 della pagina 1" e
che al tocco va detto all'agent. Tutta la conoscenza del mondo sta in
`layout.yaml`.

### 3.1 Le tile sono renderizzate sul Mac

**Decisione centrale del design.** L'agent non manda al display "etichetta +
nome icona": manda l'**immagine finita della tile**, 115×80 px, icona e testo
già composti da Pillow.

Motivazioni:

- Elimina definitivamente il dolore dei font LVGL già documentato in
  `plancia-ingresso/NOTE-TECNICHE.md`: font ritagliati per taglia, il simbolo `°`
  assente dai font di default, glifi MDI da selezionare a compile time.
- Rende disponibili **tutti** i 7000+ glifi MDI, tutte le icone reali delle app
  installate, qualunque PNG, emoji a colori — senza che il firmware ne sappia
  nulla e senza reflash.
- Tipografia, colori, badge, barre di stato sulla tile diventano codice Python
  modificabile a caldo.

Costo accettato: 12 tile × 115×80 × 2 byte (RGB565) ≈ 220 KB di PSRAM, su 8 MB
disponibili. E 12 GET HTTP al boot, ~1-2 s sulla LAN.

L'header di stato (ora, volume, brano, CPU) usa invece un font LVGL compilato
nel firmware, perché cambia ogni 2 s e re-renderizzarlo come PNG sarebbe
sprecato. Il font include il range latin-1 per gestire gli accenti nei titoli
dei brani.

### 3.2 Gli endpoint

| Endpoint | Chi chiama | Quando | Cosa fa |
|---|---|---|---|
| `GET /layout?page=N` | display | boot, cambio versione | JSON: per ogni slot posizione, url tile, se è vuoto |
| `GET /tile/{page}/{slot}.png` | display | dopo `/layout` | PNG 115×80 RGB, renderizzato e cacheato |
| `POST /press` | display | al tocco | `{"page":1,"slot":5}` → esegue, risponde `{"ok":true}` |
| `GET /state` | display | polling 2 s | volume, mute, brano, CPU, RAM, batteria, **`layout_version`** |
| `GET /` | browser | configurazione | web UI |
| `GET/PUT /api/config` | browser | configurazione | lettura/scrittura `layout.yaml` |
| `POST /api/test` | browser | configurazione | esegue un'azione senza salvarla |
| `GET /api/icons?q=` | browser | configurazione | ricerca fra app installate e glifi MDI |

### 3.3 Nessun canale di push

Il problema evitato: quando salvi nella GUI, come lo si dice al display? Un ESP
non è raggiungibile in modo affidabile su un IP che cambia, e mettere un server
HTTP sul display raddoppierebbe la superficie da mantenere.

La risposta di `/state` — che il display già interroga ogni 2 s per volume e
brano — porta un campo `layout_version`, contatore incrementato dall'agent a
ogni salvataggio. Il display lo confronta con quello in memoria e, se differisce,
richiama `/layout` e ricarica le tile cambiate.

Il canale di feedback necessario comunque trasporta gratis l'invalidazione della
cache.

Latenze che ne derivano:

- **tocco → azione: ~20-30 ms** (POST diretto, nessun intermediario)
- **salvataggio GUI → display aggiornato: fino a 2 s** (un ciclo di polling)

### 3.4 Discovery e sicurezza

Il display chiama `http://nome-del-mac.local:8765`, nome che macOS
pubblica via Bonjour su qualunque rete. Perché la risoluzione `.local` funzioni
dal lato ESP-IDF serve l'opzione sdkconfig
`CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES: y`, che fa passare `gethostbyname` per
mDNS. **Da verificare empiricamente al primo flash**; il ripiego è la riserva
DHCP sul router, stesso pattern già in roadmap per `plancia-ingresso`.

`POST /press` esegue AppleScript e comandi shell arbitrari, quindi non può stare
aperto sulla LAN di casa:

- gli endpoint del display ascoltano su `0.0.0.0` ma richiedono l'header
  `X-Deck-Token`, confrontato con `compare_digest`;
- la web UI e gli endpoint `/api/*` ascoltano **solo su `127.0.0.1`**, quindi non
  sono raggiungibili dalla rete nemmeno con il token;
- il token è generato al primo avvio in `~/.config/macdeck/token`, permessi 600,
  e va copiato nei `secrets.yaml` di ESPHome.

---

## 4. Formato di `layout.yaml`

Sorgente di verità, in `~/.config/macdeck/layout.yaml`.

```yaml
schema: 1                  # versione del formato, per migrazioni future

grid:                      # default per tutte le pagine
  cols: 4
  rows: 3

theme:                     # usato dal renderer Pillow
  background: "#12141A"
  tile: "#1E222B"
  text: "#E8EAF0"
  accent: "#4A9EFF"
  font: "Poppins"

pages:
  - name: Dev
    slots:
      - pos: [0, 0]                              # colonna, riga
        label: DataGrip
        icon: "app:/Applications/DataGrip.app"
        action: {type: app, target: DataGrip}

      - pos: [1, 0]
        label: "Commit\n& push"
        icon: "mdi:source-branch"
        color: "#2A5CAA"                         # override del colore tile
        action:
          type: sequence
          steps:
            - {type: keys, keys: "cmd+s"}
            - {type: delay, ms: 200}
            - {type: shell, cmd: "cd ~/src/foo && git add -A && git commit -m wip && git push"}

      - pos: [3, 2]
        label: Muto
        icon: "mdi:volume-off"
        action: {type: volume, op: mute_toggle}
        state: volume.muted                      # opzionale: illumina la tile

  - name: Media
    grid: {cols: 3, rows: 2}                     # override per pagina
    slots: []
```

Le posizioni sono **sparse** (`pos` esplicita) invece di una lista piatta: i
buchi nella griglia diventano naturali e riordinare non richiede di riscrivere
gli indici.

### 4.1 Tipi di azione

Registry estensibile: aggiungere un tipo è una funzione decorata, niente altro.

```python
@action("app")
def _app(spec: dict, ctx: Context) -> None: ...
```

| Tipo | Campi | Effetto |
|---|---|---|
| `app` | `target` (nome, path o bundle id) | apre o porta in primo piano |
| `keys` | `keys` (`"cmd+shift+4"`), `to` opzionale | combinazione di tasti |
| `text` | `text` | digita una stringa |
| `shell` | `cmd`, `cwd` opzionale | comando shell |
| `applescript` | `script` | AppleScript grezzo |
| `shortcut` | `name` | esegue una Shortcut di macOS |
| `url` | `url` | apre nel browser di default |
| `volume` | `op`: `set`/`up`/`down`/`mute_toggle`, `value` | volume di sistema |
| `media` | `op`: `play_pause`/`next`/`prev` | tasti media |
| `page` | `to` (indice o nome) | cambia pagina del deck |
| `sequence` | `steps` | esegue in ordine; `delay` è un passo valido |
| `delay` | `ms` | pausa dentro una sequence |
| `noop` | — | segnaposto |

`sequence` che accetta a sua volta qualunque tipo (compreso un altro
`sequence`) è ciò che rende il set componibile senza aggiungere tipi.

### 4.1.1 Il campo opzionale `state`

Uno slot può dichiarare `state: <chiave>`, dove la chiave è un percorso dentro
il JSON di `/state` (es. `volume.muted`, `media.playing`). Quando il valore è
veritiero, il display disegna un bordo di accento attorno alla tile.

La valutazione avviene **sul display**, dentro il ciclo di polling che gira
comunque: `/layout` include la chiave per ogni slot che la dichiara, e il
firmware la confronta con il JSON di `/state`. Nessun round-trip aggiuntivo,
nessun re-render della tile.

Aggiungere una nuova grandezza osservabile significa aggiungerla a `/state`:
il firmware non va toccato.

### 4.2 Specifica delle icone

Stringa con prefisso di schema, così aggiungere una sorgente è un caso in più
nel dispatcher:

| Schema | Esempio | Risoluzione |
|---|---|---|
| `app:` | `app:/Applications/Slack.app` | estrae il `.icns` dal bundle con Pillow |
| `app:` | `app:com.tinyspeck.slackmacgap` | risolve il bundle id, poi come sopra |
| `mdi:` | `mdi:volume-high` | renderizza il glifo dal TTF Material Design Icons |
| `file:` | `file:~/Pictures/x.png` | qualunque immagine leggibile da Pillow |
| `emoji:` | `emoji:🚀` | Apple Color Emoji, `embedded_color=True` |
| `text:` | `text:PR` | testo grande al posto dell'icona |

Il TTF di MDI viene scaricato una volta in `~/.config/macdeck/fonts/`.

---

## 5. UI

### 5.1 Display — 480×320 landscape

```
┌──────────────────────────────────────────────────────────┐
│ 18:42   🔊 62%  ♫ Anagrafe — Marlene Kuntz    CPU 14% 🔋 │  header 36 px
├──────────┬──────────┬──────────┬──────────────────────────┤
│          │          │          │          │
│ DataGrip │  Commit  │  Slack   │  Chrome  │   griglia 4×3
│          │  & push  │          │          │   tile 115×80
├──────────┼──────────┼──────────┼──────────┤
│  ...     │   ...    │   ...    │   ...    │
├──────────┼──────────┼──────────┼──────────┤
│  ...     │   ...    │   ...    │   ...    │
├──────────┴──────────┴──────────┴──────────┤
│  ‹    Dev  ●  Media  ○  Casa  ○        ›  │  navbar 28 px
└───────────────────────────────────────────┘
```

`transform: swap_xy` per il landscape. Il touchscreen richiede la stessa
trasformazione, altrimenti gli assi risultano scambiati.

Le pagine sono **illimitate**: la navbar mostra frecce e indicatori, il firmware
non ha un numero di pagine compilato. Cambio pagina gestito localmente in LVGL
(nessun round-trip), poi le tile della nuova pagina si caricano da `/layout`.

Gli slot vuoti sono tile trasparenti non toccabili — il firmware ne ha sempre 12,
`layout.yaml` decide quanti sono visibili.

Backlight: `light: monochromatic` su LEDC GPIO1, spegnimento dopo 5 min di
inattività, riaccensione al tocco (il primo tocco a schermo spento accende e
**non** attiva il pulsante sotto).

### 5.2 Web UI di configurazione

Single page servita dall'agent su `http://127.0.0.1:8765`, HTML+JS senza build
step né dipendenze esterne.

- griglia 4×3 in scala reale che riproduce il display, con le tile renderizzate
  dallo stesso endpoint `/tile/...` che usa il display — quello che vedi nel
  browser è esattamente quello che appare sul display;
- drag & drop per spostare una tile fra slot e fra pagine;
- pannello di dettaglio: etichetta, ricerca icona (app installate + glifi MDI),
  colore, tipo di azione con campi che cambiano in base al tipo;
- editor di `sequence` come lista di passi ordinabile;
- pulsante **Prova** che esegue l'azione via `POST /api/test` senza salvare;
- pulsante **Salva** che scrive `layout.yaml`, invalida la cache delle tile
  toccate e incrementa `layout_version`.

---

## 6. Gestione degli errori

### 6.1 Mac non raggiungibile (dormiente, altra rete, agent giù)

Il caso più frequente: il MacBook chiude il coperchio. Il display resta acceso
(alimentato da carica-batterie o dalla porta USB del Mac, che in sleep taglia la
corrente).

- `/state` in timeout → dopo 3 tentativi falliti il display mostra un banner
  "Mac non raggiungibile" e attenua le tile al 40%;
- i tocchi restano attivi ma mostrano un toast di errore invece di fallire in
  silenzio;
- il polling continua con backoff (2 s → 10 s → 30 s) e il banner sparisce al
  primo `/state` riuscito;
- `layout_version` viene riconfrontato alla riconnessione: se hai riconfigurato
  mentre il display era isolato, si riallinea da sé.

### 6.2 Permesso Accessibilità mancante

**Il gotcha numero uno di questo progetto.** Inviare tasti richiede che il
binario che esegue l'agent sia autorizzato in *Impostazioni di Sistema → Privacy
e Sicurezza → Accessibilità*. Se manca, `osascript` **non** dà un errore chiaro:
i tasti semplicemente non arrivano.

Contromisure:

- all'avvio l'agent esegue un probe innocuo (`osascript -e 'tell application
  "System Events" to return name of first process'`) e, se fallisce, logga un
  messaggio esplicito con il percorso esatto da autorizzare;
- `GET /state` include `accessibility_ok`; se è falso il display mostra un'icona
  di allarme nell'header;
- la web UI mostra un banner rosso con le istruzioni e un pulsante che apre
  direttamente il pannello di sistema giusto.

### 6.3 Errori di esecuzione azione

`POST /press` non fallisce mai con un 500 silenzioso: risponde sempre
`{"ok": bool, "error": str|null}`. Il display mostra un toast di 2 s in caso di
errore. Ogni esecuzione finisce nel log dell'agent con timestamp, slot, tipo e
esito, consultabile dalla web UI.

Timeout per azione: 5 s di default, `timeout_ms` sovrascrivibile per slot.
L'esecuzione avviene in un thread pool, così un comando shell che si impianta
non blocca il deck.

Le azioni si dividono in due classi, e la differenza è visibile nella risposta:

- **sincrone** (`app`, `keys`, `text`, `volume`, `media`, `url`, `page`): rapide
  e con esito noto. `/press` le attende fino al timeout e riporta l'esito reale
  in `ok` ed `error`.
- **asincrone** (`shell`, `applescript`, `shortcut`, `sequence`): durata
  arbitraria. `/press` risponde `{"ok": true, "accepted": true}` appena
  l'azione è partita. `ok: true` qui significa "accettata", non "riuscita".

Un fallimento asincrono non si perde: finisce nel log e nel campo `last_error`
di `/state`, che il display mostra come toast al ciclo di polling successivo.

### 6.4 Icona non risolvibile

App disinstallata, path sbagliato, glifo MDI inesistente: il renderer produce
una tile di fallback con un punto di domanda e l'etichetta, e la web UI segnala
lo slot in giallo. Mai una tile nera né un 404 che lascia il display con
l'immagine precedente.

### 6.5 `layout.yaml` malformato

L'agent valida con uno schema al caricamento. Se il file è invalido, **mantiene
in memoria l'ultimo layout valido**, serve quello, e la web UI mostra l'errore di
validazione con riga e colonna. Un errore di editing manuale non deve spegnere
il deck.

---

## 7. Strategia di test

### 7.1 Agent — test automatici, nessun hardware

Il valore di questa architettura è che il 90% del sistema è testabile senza il
display e senza il Mac in stato particolare.

- **Registry azioni**: ogni tipo testato con l'esecutore di comandi iniettato
  come fake che registra le chiamate. Verifica che `keys: "cmd+shift+4"` produca
  l'AppleScript corretto, che `sequence` esegua in ordine e rispetti i `delay`,
  che i tipi ignoti diano errore pulito.
- **Renderer tile**: rendering deterministico verificato su dimensioni, formato
  e non-vuotezza; il fallback per icona mancante verificato esplicitamente.
  Confronto pixel-perfect evitato (fragile fra versioni di Pillow).
- **Endpoint HTTP**: `TestClient` di FastAPI. Auth (token mancante/errato →
  401), `/state` in forma corretta, `layout_version` che si incrementa al PUT,
  `/layout` coerente con il file.
- **Parsing e validazione del layout**: file validi, file rotti, il fallback
  all'ultimo layout valido.
- **Binding di rete**: `/api/*` rifiuta connessioni non-loopback.

TDD per tutto il modulo azioni, che è dove i bug sono silenziosi.

### 7.2 Firmware — verifica a due stadi

1. **`esphome compile`** senza flashare: valida YAML, pin, preset e font. Si può
   fare subito, senza toccare il display.
2. **Flash e verifica manuale** su hardware, in ordine, perché ogni passo
   dipende dal precedente:
   a. il display si accende, backlight dimmerabile;
   b. i colori sono corretti (rosso è rosso, non blu);
   c. il landscape è giusto e il touch è allineato agli assi;
   d. `.local` si risolve (il punto di verifica dichiarato al §3.4);
   e. le 12 tile si caricano da `/tile/...`;
   f. un tocco lancia un'app;
   g. l'header si aggiorna;
   h. salvataggio nella GUI → il display cambia entro 2 s.

### 7.3 Test end-to-end manuali

Lo scenario che vale la pena ripetere a ogni modifica: chiudere il coperchio del
Mac, verificare il banner di errore, riaprire, verificare che tutto torni senza
intervento.

---

## 8. Struttura del progetto

```
macdeck/
├── agent/
│   ├── macdeck/
│   │   ├── __init__.py
│   │   ├── app.py            # FastAPI, endpoint, auth
│   │   ├── layout.py         # parsing e validazione layout.yaml
│   │   ├── actions.py        # registry + implementazioni
│   │   ├── executor.py       # osascript/open/shell, iniettabile nei test
│   │   ├── icons.py          # risoluzione icon: app:/mdi:/file:/emoji:/text:
│   │   ├── render.py         # composizione tile con Pillow
│   │   ├── state.py          # volume, media, cpu, ram, batteria
│   │   └── web/              # web UI statica
│   ├── tests/
│   └── pyproject.toml
├── firmware/
│   ├── macdeck.yaml
│   ├── secrets.yaml.example
│   └── fonts/
├── docs/specs/
├── backup/app0-factory.bin   # firmware di fabbrica, per tornare indietro
├── NOTE-TECNICHE.md
└── README.md
```

Un modulo per responsabilità, tutti sotto le 300 righe. `executor.py` isolato
dal resto è la scelta che rende testabile `actions.py` senza eseguire nulla di
reale sul Mac.

---

## 9. Decisioni prese e alternative scartate

| Decisione | Alternativa scartata | Perché |
|---|---|---|
| Agent + HTTP diretto | Passare da Home Assistant | Serve un agent sul Mac in ogni caso; HA aggiunge un hop e una dipendenza per zero guadagno, dato che il display è dedicato al Mac |
| Agent + HTTP diretto | USB/BLE HID | Nessun software sul Mac, ma solo scorciatoie da tastiera e **nessun feedback di stato**, che era un requisito |
| Config dinamica sul Mac | Config compilata nel firmware | Una GUI che richiede compilazione + OTA a ogni modifica non è una GUI, è un generatore di codice |
| Tile renderizzate sul Mac | Font MDI compilato nel firmware | Elimina il dolore dei font LVGL già documentato nella plancia, e apre a tutte le icone esistenti senza reflash |
| Polling `/state` a 2 s | Push verso il display | Il canale serve comunque per volume e brano; trasporta gratis anche `layout_version` |
| Python + `osascript` | Swift + `CGEvent` | Iterare costa minuti, non un ciclo Xcode. Se `osascript` si rivelasse inaffidabile, il passaggio a `cliclick` è una funzione da riscrivere |
| Backlight come `light` separato | `brightness` del preset display | Dà dimmerazione e auto-spegnimento per inattività senza codice in più |
| `pos` sparse nel layout | Lista piatta di 12 slot | Buchi naturali, riordino senza riscrivere indici |

---

## 10. Rischi

| Rischio | Impatto | Mitigazione |
|---|---|---|
| Risoluzione `.local` non funzionante da `http_request` | Il display non trova il Mac | `CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES`; ripiego su riserva DHCP. **Prima verifica al flash** |
| Permesso Accessibilità non concesso | I tasti non arrivano, senza errore | Probe all'avvio, flag in `/state`, banner nella web UI |
| PSRAM ottale a 120 MHz instabile | Boot instabile, rollback OTA silenzioso | Partire da 80 MHz. La plancia ha già insegnato che il rollback OTA è silenzioso |
| Alcuni lotti di JC3248W535 ignorano i comandi di window-address | Artefatti a schermo | Il preset ha `draw_rounding: 8`; se serve, aggiornamenti a schermo pieno |
| Il MacBook cambia rete | Deck muto fuori casa | Accettato: è un accessorio da scrivania. Il banner di errore lo rende evidente |
| `osascript` lento (~60-100 ms per keystroke) | Latenza percepita sulle scorciatoie | Accettato in v1; `cliclick` è il piano B già identificato |
