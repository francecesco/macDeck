# MacDeck — app nativa per il Mac — design

Una `.app` che apre l'editor dei pulsanti già esistente in una finestra propria,
e aggiunge in nativo le tre cose che una pagina web non può procurarsi: il
percorso di un'app trascinata dal Finder, una combinazione di tasti premuta
davvero, lo stato di salute dell'agent quando l'agent è giù.

Data: 2026-08-31 · Stato: approvato in brainstorming, pronto per il piano

---

## 1. Obiettivo

Due fastidi concreti dell'uso quotidiano della GUI web, riferiti dall'utente:

1. **Arrivarci.** Ricordarsi l'indirizzo, aprire il browser, ritrovare la scheda
   fra le altre.
2. **Configurare i pulsanti.** Scegliere app, icone e scorciatoie è macchinoso:
   si vorrebbe trascinare un'app dal Finder e registrare una scorciatoia
   premendola.

L'app risolve entrambi. Non è un rifacimento dell'editor: l'editor è quello che
c'è già.

### Criteri di successo

- Si apre da Spotlight o dal Dock e mostra l'editor senza altri passaggi.
- Se l'agent non risponde, lo riavvia e va avanti da sé; se non ci riesce,
  spiega perché con la coda del log invece di una pagina bianca.
- Trascinare un `.app` dal Finder su uno slot lo configura: `target` e `icon`
  riempiti col percorso vero del bundle.
- Premere `ctrl+alt+f5` nel registratore scrive `ctrl+opt+f5` nel campo.
- La GUI web resta usabile dal browser esattamente come oggi.

### Fuori scope

- **Notarizzazione e distribuzione.** L'app è per il suo autore: firma ad-hoc,
  nessun account sviluppatore. Darla ad altri è un lavoro a parte.
- **Icona nella barra dei menu.** Esplicitamente non richiesta: serve un'app da
  aprire quando si configura, non un residente sempre presente.
- **Riscrittura nativa dell'editor.** Scartata in brainstorming: significherebbe
  due editor da tenere allineati.
- **Pannello di controllo del servizio** (stop, log a vista, installazione del
  LaunchAgent). L'app riavvia ciò che esiste; non amministra.

---

## 2. Architettura

```
┌──────────────────────────────────┐
│  MacDeck.app  (Swift, mac-app/)  │
│                                  │
│  ┌────────────────────────────┐  │   il guscio possiede:
│  │  WKWebView                 │  │   · finestra, Dock, Spotlight
│  │  → 127.0.0.1:8765          │  │   · drop dal Finder (percorsi veri)
│  │  l'editor che c'è già      │  │   · registrazione scorciatoie
│  └────────────┬───────────────┘  │   · avvio e salute dell'agent
│               │ ponte JS         │
│  ┌────────────┴───────────────┐  │   la pagina possiede:
│  │  MacDeckBridge (Swift)     │  │   · griglia, slot, anteprima
│  └────────────────────────────┘  │   · TUTTA la logica di editing
└───────────────┬──────────────────┘
                │ HTTP loopback
┌───────────────┴──────────────────┐
│  agent macdeck (Python, :8765)   │  ← sorgente unica: layout.yaml
└──────────────────────────────────┘
```

**La regola che tiene in piedi il resto:** il guscio non sa cosa siano uno slot,
una pagina, un'icona o un tasto. Trasporta fatti grezzi — un percorso, un key
code, una risposta HTTP — e lascia interpretarli a Python o alla pagina.

È lo stesso patto che già regge fra firmware e Mac: renderer muto, conoscenza da
una parte sola. Se il guscio comincia a sapere cos'è uno slot, tornano i due
editor da allineare che questo design esiste per evitare.

### Perché un guscio e non un'app nativa intera

Scartata la riscrittura in SwiftUI: il risultato sarebbe migliore da usare, ma
il costo è il grosso del lavoro e per tutto il tempo intermedio convivrebbero
due editor. Il fastidio riferito non è «l'editor è brutto» — è «non ci arrivo» e
«mancano tre gesti». Il guscio li dà tutti e tre.

Scartata anche l'ipotesi PyObjC dentro `agent/`: eviterebbe una seconda lingua,
ma impacchettare un `.app` da un venv è fragile, e il permesso Accessibilità —
che macOS lega all'eseguibile — è già concesso a `agent/.venv/bin/python`. Si
finirebbe a combattere l'impacchettamento invece di scrivere l'app.

### Dove vive

Cartella `mac-app/`, accanto ad `agent/` e `firmware/`. Bundle id
`io.macdeck.app`, distinto da `io.macdeck.agent` del LaunchAgent.

---

## 3. Il ponte

All'avvio l'app inietta uno `WKUserScript` che definisce `window.macdeck`. La
pagina si adatta:

```js
if (window.macdeck) { /* zona di drop, tasto "registra scorciatoia" */ }
```

Fuori dall'app quell'oggetto non esiste e la pagina è identica a oggi. Un solo
`index.html`, due contesti, nessun ramo morto.

### 3.1 Drop dal Finder

Una sottoclasse di `WKWebView` intercetta `performDragOperation` — l'unico punto
in cui il percorso del bundle esiste ancora: dentro la pagina, un file
trascinato arriva come `File` senza percorso, ed è precisamente il motivo per
cui serve il nativo.

Il guscio converte il punto del drop in coordinate del viewport e chiama:

```js
window.macdeck.onDrop({ x, y, paths: ["/Applications/Slack.app"] })
```

La pagina fa `document.elementFromPoint(x, y)`, risale allo slot e riempie
`target` e `icon: app:…` con quello che già sa fare. Le coordinate del viewport
sono la scelta giusta perché `elementFromPoint` lavora esattamente in quel
sistema: nessuna conversione di scroll da mantenere.

Se il drop cade fuori da uno slot, la pagina lo ignora. Il guscio non lo sa e
non deve saperlo.

`paths` è un array perché il Finder consente di trascinare più file insieme,
ma **la pagina usa solo il primo**: uno slot è uno. Il guscio consegna tutto
quello che ha ricevuto senza decidere — la scelta è della pagina, che è l'unica
a sapere che gli slot non si sdoppiano.

### 3.2 Registrazione delle scorciatoie

`KEY_CODES` in `keymap.py` usa i key code di Carbon, che sono esattamente ciò
che `NSEvent.keyCode` consegna. La tabella che serve al registratore quindi
esiste già, in Python.

Il pannello nativo ascolta i tasti veri e manda al Mac il **fatto grezzo**, non
un'interpretazione:

```
POST /api/keys-canon   { keyCode: 118, modifiers: ["cmd", "shift"] }
  →  { keys: "cmd+shift+f4" }
```

**Forma canonica dell'uscita.** `MODIFIERS` accetta piu' nomi per lo stesso
tasto (`opt`/`option`/`alt`, `cmd`/`command`, `ctrl`/`control`): il rovescio ne
deve scegliere **uno**, e sceglie il più corto — `cmd`, `ctrl`, `opt`, `shift` —
nell'ordine già fissato da `_MOD_ORDER` (command, control, option, shift). Così
la stessa combinazione produce sempre la stessa stringa, e i test la possono
confrontare per uguaglianza.

Il rovescio di `KEY_CODES` vive accanto al dritto e si testa in Python senza
toccare Swift. La seconda lingua del repo non impara nulla che Python già sappia:
se un giorno la tabella cresce, cresce in un posto solo.

**Limite dichiarato.** Alcune combinazioni riservate dal sistema — `cmd+shift+4`,
`cmd+space` e simili — vengono consumate da macOS prima di qualunque app: il
registratore non le vedrà. Per quelle resta il campo di testo, che continua a
funzionare. Il pannello lo dice quando non riceve nulla, invece di restare
apparentemente in ascolto.

### 3.3 Cosa NON passa dal ponte

Lo stato di salute. È nativo e basta: vedi sezione 4. Il permesso Accessibilità
in particolare resta un banner della pagina, che già esiste e funziona;
duplicarlo nel guscio sarebbe una seconda verità da tenere allineata.

---

## 4. Avvio, salute, errori

### 4.1 Sequenza d'avvio

```
GET /api/config  (loopback, niente token)
├─ risponde ────────────────► carica la pagina. Fine.
└─ non risponde
   ├─ LaunchAgent caricato ─► launchctl kickstart -k gui/<uid>/io.macdeck.agent
   │                          poi interroga ogni 300 ms fino a 10 s
   │                          ├─ arriva ──► carica la pagina
   │                          └─ non arriva ► schermata nativa d'errore
   └─ non caricato ────────► schermata nativa: "manca il LaunchAgent"
                              col comando esatto da lanciare
```

**L'app non avvia mai un python per conto suo.** Sarebbe un secondo server sulla
stessa porta e, peggio, un eseguibile diverso agli occhi di macOS — che lega il
permesso Accessibilità all'eseguibile. L'invio dei tasti smetterebbe di
funzionare *senza dare errore*, che è il modo di rompersi contro cui questo
progetto si è già dovuto difendere una volta (§6.2 del design principale).
L'app riavvia il servizio che esiste, oppure dice perché non può.

La schermata d'errore mostra **le ultime righe di `/tmp/macdeck.err` e di
`/tmp/macdeck.log`**, stderr per primo: il LaunchAgent manda lì i traceback
Python, mentre `.log` (stdout) raccoglie solo i banner degli avvii riusciti —
mostrare solo quest'ultimo nasconderebbe proprio il motivo del fallimento.
Quando l'agent non parte, il motivo è lì; una pagina bianca del `WKWebView`
non aiuta nessuno.

### 4.2 La barra di salute

Endpoint nuovo, `GET /api/health` (solo loopback), che espone ciò che
l'`Announcer` già tiene in memoria:

```json
{ "deck": "192.168.0.174", "announced": "192.168.0.165",
  "error": null, "last_round": 12.4, "accessibility_ok": true }
```

`deck` è l'indirizzo trovato via Bonjour, `null` se non trovato. `announced`
è l'indirizzo del Mac che il deck ha ricevuto. `last_round` sono i **secondi
trascorsi dall'ultimo giro** dell'`Announcer`, non un orario: serve a distinguere
"non l'ha trovato" da "non ha ancora guardato", che a display sono lo stesso
pallino spento ma non sono lo stesso problema.

L'app lo interroga ogni 5 secondi e disegna due indicatori, **agent** e **deck**.

Nessun Bonjour in Swift, benché `NWBrowser` lo renderebbe facile: chi parla col
deck è l'agent, e un secondo osservatore che dicesse il contrario sarebbe peggio
di nessuno. Se l'agent è giù, la barra lo dice e del deck ammette di non sapere.

`accessibility_ok` viaggia nel payload ma la barra non lo mostra (§3.3).

### 4.3 I casi previsti

| Situazione | Cosa vede l'utente |
|---|---|
| Tutto a posto | pagina normale, due indicatori verdi |
| Deck spento o su altra rete | barra: «deck non in rete» |
| Agent morto, riparte | «avvio l'agent…» per un paio di secondi, poi la pagina |
| Agent morto, non riparte | schermata nativa con la coda del log |
| LaunchAgent non installato | schermata nativa col comando `macdeck install-agent` |
| `layout.yaml` malformato | la pagina lo dice già: non cambia nulla |

---

## 5. Struttura e build

```
mac-app/
  Package.swift
  build.sh                 → MacDeck.app
  Sources/MacDeck/
    App.swift              finestra, ciclo di vita
    AgentLauncher.swift    la sequenza di §4.1, launchctl iniettato
    HealthBar.swift        due indicatori, polling di /api/health
    DropWebView.swift      sottoclasse di WKWebView, §3.1
    ShortcutRecorder.swift pannello nativo, §3.2
    Bridge.swift           WKScriptMessageHandler e user script
  Tests/MacDeckTests/
```

Il bundle assemblato:

```
MacDeck.app/Contents/
  Info.plist          id io.macdeck.app, LSMinimumSystemVersion
  MacOS/MacDeck       il binario di SwiftPM
  Resources/          icona
```

**SwiftPM più uno script, non un progetto Xcode.** In questo repo tutto si fa da
riga di comando e si rifà uguale: `esphome run`, `pytest`, `macdeck doctor`. Un
`.xcodeproj` si aggiorna solo aprendo Xcode e non si legge in una diff. Xcode
resta necessario come toolchain, non come passaggio obbligato.

**Firma ad-hoc** (`codesign -s -`): basta per girare sulla macchina dell'autore.

**Niente sandbox.** L'app deve leggere i percorsi dei bundle trascinati e
invocare `launchctl`; sandboxarla significherebbe chiedere deroghe per entrambe
le cose per poi concederle a sé stessa. Per un accessorio da scrivania è
cerimonia senza guadagno.

`mac-app/.build/` va in `.gitignore`. L'app costruita si copia in `/Applications`
a mano: da lì Spotlight la trova.

---

## 6. File toccati fuori da `mac-app/`

| File | Cosa |
|---|---|
| `agent/macdeck/keymap.py` | rovescio della tabella: key code + modificatori → `"cmd+shift+f4"` |
| `agent/macdeck/app.py` | `POST /api/keys-canon`, `GET /api/health` — entrambi solo loopback |
| `agent/macdeck/discovery.py` | l'`Announcer` espone ciò che già sa (letture, nessun comportamento nuovo) |
| `agent/macdeck/web/index.html` | i rami `if (window.macdeck)`: zona di drop, tasto registra |
| `.gitignore` | `mac-app/.build/` |

Le modifiche Python sono additive: nessun endpoint esistente cambia forma.
`index.html` è il punto delicato — va cambiato senza peggiorare l'uso dal
browser, ed è ciò che il test di §7 sorveglia.

---

## 7. Strategia di test

| Pezzo | Come | Serve un umano? |
|---|---|---|
| Rovescio di `KEY_CODES`, `/api/keys-canon`, `/api/health` | `pytest`, TDD | no |
| Coordinate del drop → payload JS | test SwiftPM su tipi puri, AppKit fuori | no |
| Decisione «riavvio o mi arrendo» | test SwiftPM, `launchctl` iniettato | no |
| `index.html` fuori dall'app | `test_web.py`: nessun uso di `window.macdeck` senza guardia | no |
| Trascinare davvero un'app, premere davvero i tasti | a mano | **sì** |

Sul lato Swift vale la scelta che il repo fa già con `executor.py`: i comandi
esterni — `launchctl`, la rete — sono iniettati, così i test girano senza agent
né deck.

**Due cose non si possono testare senza un umano, e sono le due per cui l'app
esiste.** Il piano le isola il più possibile: logica attorno testata da sola,
pezzo AppKit sottile abbastanza da leggerlo tutto in una schermata. Non c'è una
copertura automatica onesta da promettere lì.

---

## 8. Ordine di lavoro

1. **Python** — `keymap` rovescio, `/api/keys-canon`, `/api/health`. Testabile
   per intero, e utile anche senza l'app.
2. **Guscio nudo** — finestra, `WKWebView`, barra di salute, avvio dell'agent.
   A questo punto «arrivarci» è risolto.
3. **I due pezzi nativi** — drop e registratore, con le modifiche a `index.html`.

Dopo il passo 2 c'è una app usabile in mano: se il passo 3 si rivelasse più
fastidioso del previsto, ci si ferma lì senza aver buttato niente.

---

## 9. Rischi

| Rischio | Gravità | Mitigazione |
|---|---|---|
| Le combinazioni riservate dal sistema non si registrano | media | limite dichiarato in §3.2; il campo di testo resta |
| `performDragOperation` su `WKWebView` dipende da interni di WebKit | media | il pezzo è piccolo e isolato in `DropWebView.swift`; se WebKit cambia, si rompe una cosa sola e visibilmente |
| Il permesso Accessibilità cade avviando l'agent dall'app | **alta** | §4.1: l'app non lancia mai un eseguibile proprio, solo `launchctl kickstart` del servizio esistente. Da verificare al passo 2, non alla fine |
| Il guscio accumula conoscenza dell'editor | media | ogni aggiunta al ponte va giustificata come «fatto che la pagina non può procurarsi» |

---

## 10. Decisioni prese e alternative scartate

- **Guscio + editor esistente**, non riscrittura SwiftUI. Il fastidio riferito è
  l'accesso e tre gesti mancanti, non l'editor.
- **Swift**, non PyObjC: l'impacchettamento da venv è fragile proprio dove il
  permesso Accessibilità è delicato.
- **Canonicalizzazione dei tasti in Python**, non in Swift: la tabella Carbon
  esiste già in `keymap.py`, e i key code di `NSEvent` sono gli stessi.
- **Nessun Bonjour nativo**: una seconda opinione sulla presenza del deck sarebbe
  peggio di nessuna.
- **La barra non mostra l'Accessibilità**: quel banner è della pagina e resta
  uno solo.
- **L'app non avvia un agent proprio**, nemmeno per essere più utile: preferisce
  mostrare un errore.
- **SwiftPM e uno script**, non `.xcodeproj`: si legge in una diff e si rifà da
  riga di comando.
