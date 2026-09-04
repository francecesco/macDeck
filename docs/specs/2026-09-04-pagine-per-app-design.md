# MacDeck — pagine per app — design

Il deck segue l'app che hai davanti sul Mac. La griglia di app resta la base
quando nessuna app ha una pagina sua; quando davanti c'è Spotify, Mail, Slack,
Calendar o un terminale con Claude Code, il deck salta da solo alla pagina
dedicata: comandi di quell'app e i suoi dati vivi (brano, non lette, modello e
contesto rimanente…).

Data: 2026-09-04 · Stato: approvato in brainstorming, pronto per il piano

---

## 1. Obiettivo

Oggi il deck mostra le stesse pagine qualunque cosa si stia facendo, e le
pagine condizionali (`when:`) reagiscono solo a volume, player e sistema. Il
deck deve invece **capire su quale app si sta lavorando** e mostrare la pagina
di quell'app, con i suoi dati.

### Criteri di successo

- Porto Spotify in primo piano: entro 4 s il deck mostra la pagina Spotify con
  brano e artista in corso, e i comandi ⏮ ⏯ ⏭.
- Da quella pagina uno swipe mi porta alla griglia; se poi cambia il brano,
  resto sulla griglia. Se porto davanti Mail, il deck salta alla pagina Mail.
- Con iTerm o Terminal davanti e una sessione Claude Code viva, vedo modello,
  contesto rimanente, cartella e branch; senza Claude Code vedo la griglia.
- Aggiungere una pagina per un'altra app è YAML (o GUI), non Python. Aggiungere
  una sorgente di dati nuova è **una funzione decorata** in `state.py`.
- I `layout.yaml` di oggi restano validi senza modifiche.
- Il firmware non si tocca e non si riflasha.

### Fuori scope

- **Push dal Mac al display.** La latenza di 1–4 s deriva dal polling di
  `/state` ogni 2 s, che il progetto ha scelto al posto di un canale di push.
  Resta così.
- **Segnaposto dentro le azioni** (es. `open {claude.dir}`). Utile, ma è
  un'altra funzione.
- **Claude.app desktop.** Non espone modello né contesto. Nessuna pagina nella
  v1.
- **Span verticale** delle tile. Solo colonne.
- **Modifica automatica di `~/.claude/settings.json`.** Il ponte con Claude
  Code è una riga che l'utente aggiunge a mano (§5).

---

## 2. Architettura

Nulla cambia nella divisione dei ruoli: il firmware resta un renderer muto, il
Mac decide tutto. Le novità stanno tutte nell'agent:

```
front (app davanti) ─┐
media ───────────────┤   StateProbe          _resolve()              /layout
mail ────────────────┼─► registro sonde ─► snapshot ─► mazzo ordinato ─► page
claude (file) ───────┤   thread di sfondo      │    segnaposto risolti    autoritativo
slack ───────────────┤                         │
calendar ────────────┘                         └─► firma = impronta del risultato
```

- **Sonde a registro** (§3): una funzione per sorgente, eseguita nel thread di
  sfondo con la propria cadenza, solo se l'app è in esecuzione.
- **`app:` sulla pagina** (§4): la pagina è visibile quando quell'app è davanti.
- **Il server sceglie la pagina** (§6): al cambio dell'app davanti risponde
  `page: 0`, che il firmware già adotta come autoritativo.
- **Segnaposto e tile informative** (§4, §7): le etichette leggono lo stato, la
  firma del layout le ridisegna da sola quando i valori cambiano.

---

## 3. Sonde e snapshot di `/state`

`StateProbe.refresh()` oggi chiama a mano volume, media, sistema e
accessibilità. Diventa un **registro di sorgenti**, sullo stampo di
`actions.py`:

```python
@source("mail", app="Mail", every=5.0, empty={"unread": None})
def mail(ex: Executor, ctx: ProbeContext) -> dict:
    ...
```

Il ciclo di sfondo resta uno, ogni secondo. Per ogni sorgente decide se è il
suo turno (`every`), e:

- **Gira solo se l'app è in esecuzione.** `tell application "Mail"` lancerebbe
  Mail se fosse chiuso. Il ciclo legge una volta per giro l'elenco dei processi
  e lo passa alle sonde nel `ctx`; una sonda con `app=` dichiarato e app assente
  restituisce `empty` senza eseguire nulla. Vale per Mail, Calendar, Spotify e
  Slack.
- **Un timeout per sonda.** Se la sonda fallisce o va in timeout, tiene
  l'ultimo valore noto (come fa già l'accessibilità). Dopo **tre fallimenti
  consecutivi** torna a `empty`, così una tile non mostra per ore un brano
  finito.
- **Nessuna eccezione esce dal ciclo**, come oggi.
- `EMPTY_SNAPSHOT` si compone dagli `empty` dichiarati, così `value_at` non
  trova mai buchi e i test hanno una forma nota.
- Le sonde esistenti (`volume`, `media`, `system`, `accessibility_ok`) si
  registrano allo stesso modo, senza cambiare il loro output.

### Sorgenti della prima versione

| chiave | contenuto | come | cadenza |
|---|---|---|---|
| `front` | `app` (nome del processo), `name` (nome visibile), `bundle`, `changed` | `lsappinfo front` + `lsappinfo info -only name,bundleid <asn>`; ~10 ms, niente AppleScript | 1 s |
| `media` | esiste già | invariato | 2 s |
| `mail` | `unread` | `tell application "Mail" to unread count of inbox` | 5 s |
| `claude` | `alive`, `model`, `remaining`, `dir`, `branch`, `session` | file scritto dalla statusLine (§5) + `git branch --show-current` | 5 s |
| `slack` | `badge` (testo del badge o `null`) | `AXStatusLabel` dell'icona Slack nel Dock, via System Events; **best effort** (non verificato con un badge presente) | 5 s |
| `calendar` | `next` (titolo), `next_at` (ora `HH:MM`), `count_today` | AppleScript su Calendar, eventi da adesso a fine giornata, timeout 10 s | 60 s |

`front.changed` è vero per il solo giro in cui l'app davanti è cambiata; serve
ai test e alla diagnostica, non al protocollo (che confronta per conto suo, §6).

---

## 4. Formato di `layout.yaml`

Tre novità, tutte retrocompatibili. Schema resta `1`.

```yaml
pages:
  - name: Griglia              # senza app: pagina base, sempre visibile
    slots: [...]

  - name: Spotify
    app: com.spotify.client    # nome, bundle id o percorso, come `target` di `app`
    grid: {cols: 3, rows: 2}
    slots:
      - pos: [0, 0]
        kind: info             # tile informativa
        label: "{media.title}"
        caption: "{media.artist}"
        icon: "mdi:music"
        span: 3                # occupa tre colonne
      - pos: [0, 1]
        label: Indietro
        icon: "mdi:skip-previous"
        action: {type: media, op: prev}

  - name: Claude Code
    app: [iTerm2, Terminal]    # una lista è ammessa
    when: claude.alive         # si combina con app:
    slots: [...]
```

### `app:` sulla pagina

- Stringa o lista di stringhe. La pagina è visibile quando l'app davanti
  corrisponde a una voce, confrontata a caldo con **nome del processo, nome
  visibile e bundle id** (`front.app`, `front.name`, `front.bundle`), senza
  distinzione di maiuscole. Un percorso `.app` si riduce al nome del bundle.
- Non è un errore di validazione indicare un'app non installata: la pagina non
  compare mai. `macdeck doctor` lo segnala come avviso.
- `app:` e `when:` si combinano in AND.
- Le pagine senza `app:` si comportano come oggi.

### Segnaposto nelle etichette

- In `label` e `caption`, `{percorso.puntato}` diventa il valore dello
  snapshot, con la stessa sintassi di `when:` e `state:`.
- Un solo filtro, `|int`: `{claude.remaining|int}%` evita "38.4%". Nessun'altra
  formula: se serve logica, la fa la sonda.
- Valore assente o `None` → stringa vuota. Non è un errore.
- La sostituzione avviene in `_resolve()`, prima del rendering: il renderer
  riceve etichette finite e non conosce lo stato. La firma del layout, che è
  già l'impronta del risultato risolto, cambia quando cambia un valore.

### Tile informativa

- `kind: info` (assente = `button`, invariato).
- `caption`: testo piccolo sotto il valore, con segnaposto.
- `span`: colonne occupate, intero ≥ 1, opzionale. Ammesso anche sui pulsanti.
- `action` facoltativa. Senza azione la tile non risponde al tocco.
- Validazione: `span` deve stare dentro la griglia; le caselle coperte contano
  come occupate ai fini della regola "due slot incondizionati sulla stessa
  casella sono un errore".

### Layout di default

Il `DEFAULT_LAYOUT` diventa: la griglia di oggi, più cinque pagine con `app:`
(Spotify, Mail, Claude Code, Slack, Calendar). Chi installa da zero le trova.
Chi ha già un `layout.yaml` non vede cambiare nulla finché non le aggiunge dalla
GUI o copiandole dal README.

Contenuto indicativo delle cinque pagine:

| pagina | `app:` | riga info | pulsanti |
|---|---|---|---|
| Spotify | `com.spotify.client` | brano · artista (span 3) | ⏮ ⏯ ⏭ · vol− · muto · vol+ |
| Mail | `com.apple.mail` | `{mail.unread}` non lette | nuovo (`cmd+n`) · rispondi (`cmd+r`) · archivia (`ctrl+cmd+a`) · segna letto (`shift+cmd+u`) · cerca (`opt+cmd+f`) |
| Claude Code | `[iTerm2, Terminal]` + `when: claude.alive` | modello · `{claude.remaining\|int}%` · cartella con branch in didascalia | Esc · Invio · `/compact` · `/clear` · shift+tab · tre liberi |
| Slack | `com.tinyspeck.slackmacgap` | badge | non letti (`shift+cmd+a`) · cerca (`cmd+k`) · thread (`shift+cmd+t`) · stato · muto microfono (`m` in chiamata) |
| Calendar | `com.apple.iCal` | prossimo evento · ora | oggi (`cmd+t`) · giorno/settimana/mese (`cmd+1/2/3`) · nuovo evento (`cmd+n`) |

Le scorciatoie vanno verificate una per una in implementazione: sono la parte
del default che più facilmente è sbagliata, e si correggono dalla GUI.

---

## 5. Il ponte con Claude Code

Claude Code invoca il comando `statusLine` di `~/.claude/settings.json` a ogni
aggiornamento, passandogli su stdin un JSON con `session_id`,
`model.display_name`, `workspace.current_dir`,
`context_window.remaining_percentage`. Il ponte è **una riga in più** in quel
comando, che salva il JSON prima di formattarlo:

```sh
input=$(cat); mkdir -p ~/.config/macdeck/claude; printf '%s' "$input" > ~/.config/macdeck/claude/$(echo "$input" | jq -r .session_id).json; # ...resto come oggi
```

- **`settings.json` non si tocca in automatico.** È un file dell'utente con
  dentro cose che non riguardano MacDeck. `macdeck doctor` controlla che la
  cartella riceva file freschi e, se no, stampa la riga da aggiungere. Il README
  la documenta.
- **La sonda `claude`** legge il file **modificato più di recente**: con più
  sessioni aperte vince quella con cui si è parlato per ultima, che è quasi
  sempre quella davanti. `alive` è vero se esiste un processo `claude` **e** il
  file ha meno di 30 minuti. File più vecchi di 24 ore si cancellano al
  passaggio.
- `remaining` è la percentuale di contesto rimanente; `dir` è la cartella
  abbreviata con `~`; `branch` lo calcola la sonda con `git branch
  --show-current` sulla cartella (ogni 5 s, costo trascurabile); `session` è
  il `session_id`, utile per la diagnostica.
- Un file malformato o senza i campi attesi vale come assente.

---

## 6. Protocollo: chi decide la pagina

Il firmware non cambia. Il campo `page` di `/layout` è già autoritativo (il
display lo adotta, NOTE-TECNICHE «La pagina corrente può sparire sotto i
piedi»); il server, oltre a clampare l'indice, ora può **sceglierlo**.

- **Ordine del mazzo.** `_resolve()` mette per prime le pagine la cui `app:`
  corrisponde all'app davanti (e con `when:` soddisfatto, se c'è), nell'ordine
  del file; poi le pagine senza `app:` visibili, nell'ordine del file. Le pagine
  con `app:` di app *non* davanti non ci sono: non si raggiungono con lo swipe,
  perché sarebbero comandi per una finestra che non c'è.
- **Il salto.** Il server ricorda quali pagine con `app:` c'erano nel mazzo
  servito all'ultimo `/layout`. Se l'insieme è cambiato, risponde
  `page: 0` qualunque indice il display abbia chiesto, e aggiorna il ricordo.
  Se non è cambiato, clampa come oggi. Conseguenze:
  - apri Mail → il mazzo cambia → la versione cambia → il display chiede
    `/layout?page=<sua>` → il server risponde `0` → il display adotta;
  - cambia il brano mentre sei sulla griglia → la versione cambia (etichetta)
    ma l'app davanti no → resti sulla griglia;
  - passi da Chrome a Safari → nessuna pagina in gioco, il mazzo non cambia,
    nessuna richiesta;
  - passi da Chrome a Safari con un'altra pagina base aperta → il mazzo non
    cambia, il ricordo non cambia, lo swipe successivo funziona.
- **Coerenza fra `/layout`, `/screen`, `/press`.** Tutti risolvono lo stesso
  mazzo dallo stesso snapshot, come già ora. La finestra di gara fra `/layout`
  e il download dello schermo esiste già ed è coperta dal giro successivo di
  `/state`.
- **Tile informative senza azione** non compaiono nell'elenco `slots` di
  `/layout`: niente area di tocco, niente velo al dito. Sono solo pixel nel
  PNG. Una tile info **con** azione compare come un pulsante.
- **Latenza attesa:** cambio app → sonda `front` (≤1 s) → poll `/state` (≤2 s)
  → `/layout` + PNG (~0,5 s). Fra 1 e 4 s.
- **Costo dei ridisegni.** Ogni valore che cambia in un'etichetta produce un
  nuovo PNG. Al massimo uno ogni 2 s, per costruzione del polling.

---

## 7. Rendering della tile informativa

Tutto in `render.py`.

- **Geometria.** `slot_boxes` resta com'è; `span` si applica dopo: il
  rettangolo si allarga a `span·w + (span−1)·GUTTER`.
- **Aspetto.** Sfondo come una tile normale (`color` o `theme.tile`). Icona
  piccola a sinistra, se c'è. Il **valore** (`label` risolto) in grande, su una
  riga: si parte da `h·0,45` px e si scende fino a un minimo di 12 px, poi si
  tronca con l'ellissi. La **didascalia** sotto, piccola, nel colore del testo
  attenuato. Valore vuoto: si mostra la sola didascalia; la tile non sparisce,
  così la pagina non balla quando Spotify è in pausa.
- **Cache.** `TileCache._key` include già etichetta e geometria; si aggiungono
  `kind` e `caption`. Nessun altro cambio.
- **Anteprima nella GUI.** `/api/tile-preview` accetta `kind`, `caption`,
  `span` e risolve i segnaposto con lo snapshot corrente: nell'editor si vede
  la tile con i dati veri del momento, se l'app è aperta, altrimenti con i
  valori vuoti.

---

## 8. Web UI

- **Pannello Pagina:** campo **App** con il selettore di app già usato per gli
  slot (accetta anche più voci), e il campo `when:` che oggi esiste solo nel
  YAML. Vuoto = pagina base.
- **Pannello Slot:** interruttore **Pulsante / Informativa**. In modalità
  informativa compaiono `caption` e `span`, e l'azione diventa facoltativa.
  Accanto a `label` un menu **Inserisci valore** che elenca le chiavi
  disponibili; la lista arriva da `/api/config`, che la prende dal registro
  delle sonde (ogni sorgente dichiara le sue chiavi), così una sorgente nuova
  compare da sola.
- **Avviso:** un segnaposto con chiave non nel registro è evidenziato in
  giallo. Non blocca il salvataggio: nove volte su dieci è un refuso, ma il
  layout resta valido.
- **Schede pagina:** le pagine con `app:` mostrano l'icona dell'app sulla
  scheda.
- **App nativa:** nessuna modifica.

---

## 9. Errori e casi limite

| caso | comportamento |
|---|---|
| `app:` di un'app non installata | la pagina non compare mai; avviso in `doctor` |
| sonda in errore o timeout | ultimo valore noto; dopo tre fallimenti consecutivi, `empty` |
| app chiusa | la sonda non gira e non la apre; valore `empty` |
| segnaposto su chiave inesistente | stringa vuota; avviso giallo nella GUI |
| cambio app durante uno swipe | il salto vince: `page: 0`, il display adotta |
| Alt‑Tab veloce fra due app con pagina | due salti in ~4 s; è ciò che l'utente ha fatto |
| Mac sotto carico | le sonde hanno i propri timeout, `/state` resta ~1 ms (invariato) |
| più sessioni Claude Code | vince il file più recente; se nessun processo `claude`, `alive` falso |
| badge Slack non leggibile | `badge: null`, la tile mostra la sola didascalia |

---

## 10. Test

- **`state.py`:** registro con executor finto; cadenza per sorgente (orologio
  iniettato); "app non in esecuzione ⇒ `empty` senza chiamate"; parsing di ogni
  sonda su output finti, vuoti e malformati; tre fallimenti ⇒ `empty`; sonda
  `claude` su una cartella temporanea con file di età diverse e processo
  presente/assente.
- **`layout.py`:** `app:` stringa e lista; `kind`, `caption`, `span` (fuori
  griglia, sovrapposto, azione assente); i layout di esempio esistenti
  validano identici a prima.
- **`app.py`:** ordine del mazzo con app davanti sì/no; salto a `0` al cambio
  app e **nessun** salto al cambio di brano; tile info senza azione assenti da
  `slots`, con azione presenti; segnaposto risolti nell'etichetta e riflessi
  nella firma; `/press` su una casella info senza azione → 404.
- **`render.py`:** tile info con valore lungo, vuoto, con span; dimensioni
  dell'immagine.
- **Firmware:** nessuna modifica, nessun flash.
- **End‑to‑end manuale:** apri Spotify → pagina entro 4 s; swipe alla griglia;
  cambia brano → resti sulla griglia; apri Mail → salto; apri iTerm senza
  Claude Code → griglia; con Claude Code → pagina con modello e percentuale.

---

## 11. Decisioni prese e alternative scartate

- **Pagine in YAML, non in Python.** Un renderer per app sarebbe stato più
  rapido all'inizio, ma non modificabile dalla GUI e contrario alla decisione
  centrale: tutta la conoscenza del mondo sta in `layout.yaml`.
- **`app:` come campo proprio, non `when: front.bundle == ...`.** Il confronto
  su tre nomi e la lista di alternative non stanno in un percorso puntato, e la
  GUI può offrire il selettore di app.
- **Il salto lo decide il server nel `page` di `/layout`,** non un nuovo campo
  in `/state` né un cambio al firmware: il meccanismo esiste già ed è
  collaudato.
- **Il mazzo esclude le pagine delle app non davanti** invece di lasciarle
  raggiungibili con lo swipe: comandi per una finestra che non c'è confondono.
- **`lsappinfo` per l'app davanti** invece di AppleScript su System Events:
  10 ms contro 300, e non dipende dal permesso Accessibilità.
- **File scritto dalla statusLine** invece di leggere i transcript in
  `~/.claude/projects`: il JSON della statusLine ha già i campi giusti, i
  transcript sono grandi e privati.
- **Niente EventKit per Calendar.** Avrebbe richiesto pyobjc e un permesso in
  più al Python del venv; AppleScript basta quando Calendar è aperto, e la
  pagina si vede solo allora.

## 12. Rischi

- **Badge di Slack via Accessibilità:** non verificato con un badge presente.
  Se non funziona, la tile resta con la sola didascalia; nessun altro effetto.
- **AppleScript su Calendar può essere lento** con molti calendari: timeout
  10 s e cadenza 60 s lo contengono; nel peggiore dei casi la tile mostra
  l'ultimo valore noto.
- **Nomi delle app** (processo, visibile, bundle) non coincidono sempre:
  iTerm è `iTerm2` come processo, Terminale è `Terminal`. Il confronto su tre
  nomi è la mitigazione; il README elenca i casi noti.
- **Nuovo PNG a ogni messaggio di Claude Code:** una decodifica sul display
  ogni pochi secondi nelle sessioni fitte. Se desse fastidio, la cadenza della
  sonda `claude` si alza; non è un problema di architettura.
