# MacDeck — Stream Deck per macOS su Guition JC3248W535

Un pannello touch da scrivania che lancia app, invia scorciatoie ed esegue script
sul MacBook, e mostra in tempo reale volume, brano in riproduzione e stato del
sistema. I pulsanti si configurano da una **GUI web sul Mac**: il firmware si
flasha una volta e non lo si tocca più.

![anteprima delle tile](docs/provino-tile.png)

## Come funziona

Il firmware ESPHome è un **renderer muto**: non conosce né app né icone né la
griglia. Scarica da `/screen/{page}.png` **una sola immagine a schermo intero**
composta dal Mac, ci sovrappone dodici rettangoli trasparenti posizionati da
`/layout`, e al tocco manda `{page, slot}` a `/press`. Tutta la conoscenza del
mondo sta in `~/.config/macdeck/layout.yaml`.

```
┌─────────────────────────┐         ┌──────────────────────────────────┐
│  JC3248W535 (ESPHome)   │  WiFi   │  Mac — agent `macdeck` (Python)  │
│  1 immagine + 12 tocchi │◄───────►│  FastAPI su :8765                │
│  + header di stato      │  HTTP   │  web UI · azioni · renderer      │
│  NESSUNA logica         │         │  layout.yaml  ← sorgente unica   │
└─────────────────────────┘         └──────────────────────────────────┘
```

**Non c'è nessun canale di push.** La risposta di `/state`, che il display
interroga ogni 2 s per volume e brano, porta anche `layout_version`: quando
cambia, il display ricarica il layout da sé. Il canale che serve comunque
trasporta gratis l'invalidazione della cache.

Conseguenze misurate: **tocco → azione in ~20-30 ms**; salvataggio nella GUI →
display aggiornato entro 2 s.

## Perché le tile sono renderizzate sul Mac

È la decisione centrale del progetto. Il Mac non manda "etichetta + nome icona",
manda l'immagine finita della tile, composta da Pillow. Questo:

- **elimina il problema dei font LVGL** documentato in `plancia-ingresso`: font
  ritagliati per taglia, il simbolo `°` assente, glifi MDI da scegliere a compile
  time;
- rende disponibili le **icone reali delle app installate** (estratte dai `.icns`
  dei bundle), tutti i **7447 glifi MDI**, qualunque PNG ed emoji a colori;
- sposta tipografia, colori e composizione in codice Python modificabile a caldo.

Costo: ~250 KB di PSRAM su 8 MB, e **una** GET HTTP quando cambia il layout.

## Hardware

| Parametro | Valore |
|---|---|
| Scheda | Guition JC3248W535 (venduta anche come "Diymore ESP32-S3 3.5\"") |
| Chip | ESP32-S3, 16 MB flash, 8 MB PSRAM ottale (`N16R8`) |
| Display | 3.5" IPS 320×480, AXS15231B su bus QSPI — usato in **landscape 480×320** |
| Touch | capacitivo integrato nell'AXS15231B, I²C `0x3B` |

**Il landscape passa da LVGL, non dal display:** l'AXS15231B non supporta lo
scambio degli assi e ESPHome rifiuta il `transform`. Si usa `lvgl: rotation: 90`,
che ruota via software e ruota anche il touch. Lo spazio utile è 480×320 e la
griglia di default è 3×3 con tile da 154×80. Meno righe = icone più grandi: 3×2 dà tile 154×122.

Pinout: QSPI CLK 47, data 21/48/40/39, CS 45 · backlight GPIO1 (LEDC) · touch I²C
SDA 4, SCL 8. Supporto ESPHome **nativo** (`mipi_spi` con preset `JC3248W535` +
`axs15231`): nessun componente custom, a differenza della plancia da 7".

## Installazione

### 1. Agent sul Mac

```bash
cd agent
/usr/local/bin/python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m macdeck.cli fetch-fonts     # 7447 glifi MDI
.venv/bin/python -m macdeck.cli doctor          # diagnosi
.venv/bin/python -m macdeck.cli serve
```

Poi apri **http://127.0.0.1:8765** per configurare i pulsanti.

Per farlo partire al login:

```bash
.venv/bin/python -m macdeck.cli install-agent   # LaunchAgent, log in /tmp/macdeck.log
```

### 2. Il passo manuale che non si può automatizzare

Inviare tasti richiede il permesso **Accessibilità** per l'interprete del venv.
Se manca, `osascript` **non dà errore**: i tasti semplicemente non arrivano.

```bash
open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'
```

Aggiungi `agent/.venv/bin/python` all'elenco. `macdeck doctor` ti dice se è a
posto, `/state` espone `accessibility_ok`, e la web UI mostra un banner rosso.

### 3. Firmware

```bash
cd firmware
cp secrets.yaml.example secrets.yaml     # wifi + il token di `macdeck token`
esphome compile macdeck.yaml             # validazione, senza flashare
esphome run macdeck.yaml --device /dev/cu.usbmodem13301
```

Il firmware di fabbrica è salvato in `backup/app0-factory.bin` prima di
qualunque flash.

## Configurazione

`~/.config/macdeck/layout.yaml`:

```yaml
schema: 1
grid: {cols: 3, rows: 3}
theme:
  background: "#12141A"
  tile: "#1E222B"
  text: "#E8EAF0"
  accent: "#4A9EFF"
  font: SFNS
  icon_scale: 1.0        # moltiplicatore della dimensione dell'icona
pages:
  - name: Dev
    slots:
      - pos: [0, 0]                       # colonna, riga
        label: DataGrip
        icon: "app:/Applications/DataGrip.app"
        action: {type: app, target: DataGrip}
      - pos: [1, 0]
        label: "Commit\n& push"
        icon: "mdi:source-branch"
        color: "#2A5CAA"
        action:
          type: sequence
          steps:
            - {type: keys, keys: "cmd+s"}
            - {type: delay, ms: 200}
            - {type: shell, cmd: "cd ~/src/foo && git add -A && git commit -m wip"}
      - pos: [2, 2]
        label: Muto
        icon: "mdi:volume-off"
        action: {type: volume, op: mute_toggle}
        state: volume.muted                # bordo di accento quando è vero
```

Le posizioni sono **sparse**: i buchi nella griglia sono naturali e riordinare non
richiede di riscrivere indici.

### Tipi di azione

| Tipo | Campi | Note |
|---|---|---|
| `app` | `target` | nome, percorso o bundle id |
| `keys` | `keys`, `to` | `cmd+shift+4`, `ctrl+up`, `cmd+return`… |
| `text` | `text` | digita una stringa |
| `url` | `url` | apre nel browser di default |
| `shell` | `cmd`, `cwd` | asincrona |
| `applescript` | `script` | asincrona |
| `shortcut` | `name` | Shortcut di macOS, asincrona |
| `volume` | `op` (`set`/`up`/`down`/`mute_toggle`), `value`, `step` | |
| `media` | `op` (`play_pause`/`next`/`prev`) | solo Spotify e Music, vedi limiti |
| `page` | — | gestito dal firmware in locale |
| `sequence` | `steps` | componibile, anche annidata |
| `delay` | `ms` | passo dentro una sequence |
| `noop` | — | segnaposto |

Aggiungere un tipo costa **una funzione decorata** in `agent/macdeck/actions.py`:
la validazione del layout e la GUI leggono lo stesso registry.

### Schemi delle icone

`app:` (percorso, nome o bundle id) · `mdi:` (7447 glifi) · `file:` (qualunque
immagine) · `emoji:` (a colori) · `text:` (fino a 3 caratteri).

## Limiti dichiarati

- **Il brano in riproduzione si legge solo da Spotify e Music.** Su macOS recente
  MediaRemote è chiuso, quindi non esiste un "now playing" di sistema leggibile
  senza helper esterni. L'audio da browser non è visibile: è un limite, non un bug.
- **Il deck è muto fuori dalla LAN del Mac.** È un accessorio da scrivania; il
  display mostra "Mac non raggiungibile" invece di fallire in silenzio.
- **Ogni keystroke costa ~60-100 ms** perché passa da `osascript`. Se diventasse
  un problema, il piano B è `cliclick`: una funzione da riscrivere, non un rewrite.
- **12 slot per pagina al massimo**, che è il numero di widget immagine nel firmware. Le
  pagine sono illimitate.

## Test

```bash
cd agent && .venv/bin/python -m pytest        # 150 test, nessun hardware richiesto
```

Il 90% del sistema è testabile senza il display: `executor.py` è l'unico modulo
che tocca `subprocess`, e i test lo sostituiscono con un fake che registra le
chiamate invece di eseguirle.

## Documenti

- [Design](docs/specs/2026-08-27-macdeck-design.md) — architettura,
  protocollo, decisioni e alternative scartate
- [Piano di implementazione](docs/plans/2026-08-27-macdeck.md) — 14 task
- [NOTE-TECNICHE.md](NOTE-TECNICHE.md) — note tecniche e trappole incontrate
