# MacDeck — note tecniche

Trappole incontrate e decisioni con un perché non ovvio. Il design completo sta
in `docs/specs/2026-08-27-macdeck-design.md`.

## Hardware

### Identificare la board dal firmware di fabbrica

La scheda è venduta senza documentazione utile. È stata identificata leggendo la
flash prima di toccarla:

```bash
esptool --port /dev/cu.usbmodem13301 read-flash 0x10000 0x200000 app0.bin
strings app0.bin | grep -iE "jc[0-9]{4}w[0-9]{3}|guition|axs15231"
```

Il firmware Arduino di fabbrica contiene le stringhe `JC3248W535` e `guition`.
Il dump è conservato in `backup/app0-factory.bin`: senza di esso non si torna
indietro.

### Il landscape si ottiene da LVGL, non dal display

`esphome config` rifiuta il `transform` sul display con **"Axis swapping not
supported by this model"**: nel preset l'AXS15231 ha `swap_xy=cv.UNDEFINED`.

La soluzione **non** è rassegnarsi al verticale. ESPHome dice esplicitamente
dove va messa la rotazione:

> use of 'rotation' in the display config is not compatible with LVGL, please
> set rotation in the LVGL config instead

`lvgl: rotation: 90` usa la rotazione **software** quando il driver non ha
quella hardware, e ruota anche le coordinate del touch perché il touchscreen è
registrato dentro LVGL. Lo spazio utile diventa 480×320 e tutte le coordinate
dei widget si esprimono in quello spazio.

### Il preset non gestisce il backlight

`mipi_spi` con `model: JC3248W535` fornisce dimensioni, `cs_pin`, `data_rate` e
init sequence, ma **non** il backlight. Va dichiarato a parte come `output: ledc`
su GPIO1 più `light: monochromatic`. È anche la scelta migliore: dà dimmerazione e
auto-spegnimento per inattività senza codice aggiuntivo.

`dimensions` resta obbligatorio anche con un preset che le contiene già.

### PSRAM a 80 MHz, non 120

Le config community usano `speed: 120MHz`. Qui si sta a 80: la plancia da 7" ha
insegnato che l'instabilità della PSRAM si manifesta come **rollback OTA
silenzioso**, che è la modalità di guasto più costosa da diagnosticare.

## Firmware

### La geometria si applica con l'API C di LVGL, non con `lvgl.widget.update`

`x`, `y`, `width` e `height` **sono** in `BASE_PROPS` di ESPHome, quindi
teoricamente aggiornabili via `lvgl.widget.update`. Ma non è verificato che
accettino `!lambda`, e il valore arriva da un JSON parsato a runtime.

Si chiamano quindi `lv_obj_set_pos`, `lv_obj_set_size` e `lv_obj_add_flag`
direttamente da lambda: funziona con certezza e non dipende da quell'incognita.
È ciò che permette al Mac di decidere l'intera geometria, non solo il contenuto.

### `headers` si chiama `request_headers`

Nelle azioni `http_request.*` la chiave `headers` è stata rinominata in
`request_headers`. `esphome config` lo dice chiaramente, `esphome compile` no.

### I lambda devono restituire `std::string`, non `const char*`

```yaml
# NO: il ternario fra due letterali da' const char*, e ESPHome ci chiama .c_str()
text: !lambda 'return id(agent_online) ? "" : "Mac non raggiungibile";'
# SI:
text: !lambda 'return std::string(id(agent_online) ? "" : "Mac non raggiungibile");'
```

### Risoluzione dei nomi `.local`

Il display chiama il Mac per nome Bonjour, che macOS pubblica su qualunque rete.
Perché il resolver di ESP-IDF risolva gli `.local` serve:

```yaml
sdkconfig_options:
  CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES: "y"
```

**Da verificare al primo flash.** Il ripiego è mettere l'IP in `agent_host` e
riservarlo nel DHCP del router.

### Il primo tocco a schermo spento non deve premere nulla

`on_idle` spegne il backlight **e mette LVGL in pausa**. Il tocco successivo
riaccende e chiama `lvgl.resume`, ma non arriva a nessun widget perché LVGL era
in pausa. Senza la pausa, riaccendere lo schermo lancerebbe un'app a caso.

### Dodici `online_image` non funzionano: ne serve una

La prima versione aveva un `online_image` per tile. Sul dispositivo ha prodotto
**due guasti distinti**, entrambi invisibili in compilazione:

```
E esp-tls: Failed to create socket (family 2 socktype 1 protocol 0)
E transport_base: Failed to open a new connection: 32770
E online_image: Download failed.
```

ESP-IDF ha **10 socket** di default: dodici download paralleli ne esauriscono
la scorta e le ultime quattro tile non arrivavano mai. E anche le otto
scaricate **restavano invisibili**, perché dopo il download il descrittore
dell'immagine cambia e `lvgl.widget.redraw` (che si limita a invalidare) non
basta: serve ri-assegnare `src` con `lvgl.image.update`.

La correzione non è alzare `CONFIG_LWIP_MAX_SOCKETS` e aggiungere dodici
`lvgl.image.update`. È **una sola immagine a schermo intero**, renderizzata dal
Mac, con sopra dodici `obj` trasparenti che raccolgono i tocchi: un socket, un
refresh, e in più il Mac guadagna il controllo di ogni pixel.

Le aree di tocco trasparenti hanno un secondo uso: il bordo di accento per la
chiave di `state` è l'unica cosa che disegnano.

### Il tempo di decodifica PNG sul dispositivo

`online_image took a long time for an operation (271 ms), max is 30 ms` è un
avviso di ESPHome, non un errore: decodificare PNG sull'ESP32 costa. Con una
sola immagine scaricata solo al cambio di `layout_version` è un costo pagato
raramente. Se diventasse fastidioso, servire BMP invece di PNG toglie la
decompressione zlib al prezzo di più byte sulla rete.

## Agent

### `osascript -e 'path to application …'` può appendersi per sempre

Risolvere un'app per nome via AppleScript attende il prompt di permesso
**Automazione**. In un contesto non interattivo il comando non torna mai — è
stato osservato bloccarsi oltre i 120 s.

`icons._locate_bundle` fa quindi una **ricerca su filesystem** nelle directory
standard delle applicazioni: immediata, e non chiede permessi. Un agent che si
appende mentre renderizza una tile è molto peggio di un'icona non trovata.

### I `.icns` non vanno aperti impostando `im.size`

```python
# NO: le voci di info["sizes"] sono terne (w, h, scala), Pillow 12 vuole una coppia
im.size = max(im.info["sizes"], key=lambda s: s[0])   # TypeError in load()
# SI: Pillow apre gia' un .icns alla risoluzione maggiore disponibile
im.convert("RGBA").resize((size, size), Image.LANCZOS)
```

Il test che controllava solo `im.size == (64, 64)` **non ha visto questo bug**,
perché il ripiego con il punto di domanda ha la dimensione giusta. Ora i test
verificano che l'icona sia reale e a colori. Vale in generale: quando esiste un
ripiego, asserire la forma non basta — va asserito che non sia il ripiego.

### La mappa dei glifi MDI non sta in un `meta.json`

Il repo del webfont non pubblica `meta.json` (404). La mappa nome → codepoint sta
in `scss/_variables.scss`, nella forma `"abacus": F16E0,`. `macdeck fetch-fonts`
lo scarica e lo converte: 7447 glifi.

### Il permesso Accessibilità fallisce in silenzio

È il gotcha n°1 del progetto. Senza autorizzazione, `osascript` che invia tasti
**non** dà errore: i tasti non arrivano e basta. Tre difese:

- `macdeck doctor` fa un probe e stampa l'interprete esatto da autorizzare;
- `/state` espone `accessibility_ok`, e il display mostra `!` nell'header;
- la web UI mostra un banner rosso con il comando per aprire il pannello giusto.

### Sincrono contro asincrono in `/press`

`app`, `keys`, `text`, `volume`, `media`, `url`, `page` sono attese fino al
timeout e riportano l'esito reale. `shell`, `applescript`, `shortcut`, `sequence`
hanno durata arbitraria: `/press` risponde `{"ok": true, "accepted": true}` appena
partono, e `ok: true` lì significa "accettata", non "riuscita". Un fallimento
asincrono finisce in `last_error` di `/state`, che il display mostra come toast.

### Lo store non perde mai il layout

`LayoutStore` mantiene in memoria l'**ultimo layout valido**. Un `layout.yaml`
rotto a mano riempie `store.error` e la web UI lo mostra, ma il deck continua a
funzionare. `save()` valida **prima** di scrivere: nessuna scrittura parziale.

### La GUI e il server devono concordare sulla geometria

La web UI replica in JavaScript il calcolo di `slot_boxes`. `test_web.py`
verifica che le costanti nell'HTML combacino con quelle di `layout.py`: se
qualcuno cambia `DISPLAY_H` senza aggiornare la GUI, l'anteprima mentirebbe.

### /state non deve mai essere calcolato sul percorso della richiesta

Il guasto più costoso di questo progetto, e la sua diagnosi merita di essere
ricordata perché il sintomo puntava dalla parte sbagliata.

**Sintomo:** il display crashava al tocco, con
`Reason: Fault - Unknown, Crashed core: 1` e un backtrace che diceva
`esp_cpu_wait_for_intr → prvIdleTask`. Sembrava un problema del firmware.

**Quel backtrace è il core 1 fermo in idle**, cioè nessuna informazione: è la
firma di un watchdog, non del punto di guasto. Il dato vero era nei log
seriali, non nel canale API:

```
[W][component:522]: interval took a long time for an operation (8013 ms)
```

8013 ms è esattamente il timeout di `http_request`. Il loop principale di
ESPHome restava bloccato per secondi a ogni poll di `/state`, e i blocchi
accumulati facevano scattare il watchdog.

**Prima ipotesi, sbagliata:** risoluzione mDNS lenta. Il nome Bonjour del Mac
annuncia davvero sei indirizzi, `127.0.0.1` compreso, il che è un problema
reale — ma passare all'IP **non** ha cambiato nulla. Ipotesi refutata.

**Causa vera, misurata:** `/state` costava 1,1–1,8 s perché veniva calcolato
sul percorso della richiesta lanciando tre `osascript`, e la cache a TTL di
1,5 s era più corta dei 2 s di polling del display: non serviva mai il
dispositivo. Il dato decisivo è stato misurare la latenza lato Mac invece di
speculare sul firmware:

```
connect 0.005s → totale 1.814s     ← calcolo a freddo
connect 0.004s → totale 0.010s     ← cache TTL
```

Connessione istantanea, risposta lentissima: il problema era il server.

**Correzione:** le sonde girano in un thread di sfondo e `snapshot()` legge
dalla memoria. `/state` è passato da 1100–1800 ms a **7–37 ms**, e i blocchi
sul display sono spariti (75 s di regime, zero messaggi).

Il test `test_snapshot_non_interroga_mai_il_mac` esiste per impedire che
qualcuno rimetta una chiamata costosa dentro `snapshot()`.

### Le icone sono limitate dall'altezza, non dalla larghezza

Passare da 4×3 a 3×3 allarga le tile da 115 a 154 px ma **non le alza**: le
righe restano tre nella stessa fascia di 256 px, quindi l'icona cresce di
pochissimo. Per ingrandirla davvero bisogna togliere una riga: 3×2 dà tile
154×122 e icone del 60% più grandi.

`theme.icon_scale` permette di regolare la dimensione senza toccare il
codice, e il renderer limita comunque l'icona al box della tile.

### La pagina corrente del display può sparire sotto i piedi

Con le pagine condizionali (`when:`) l'insieme delle pagine visibili cambia da
solo mentre il display ne sta guardando una. Se il display è sulla pagina 2 e
quella pagina smette di essere visibile, `GET /layout?page=2` non deve dare
404: il display non avrebbe modo di sapere dove andare e resterebbe bloccato lì.

**Il protocollo dice invece:** il server riporta l'indice dentro l'intervallo
valido e comunica nel campo `page` della risposta su quale pagina si trova
davvero. Il firmware adotta quel valore. Vale per `/layout`, `/screen` e
`/press`, che devono clampare allo stesso modo, altrimenti il tocco agirebbe
su una pagina diversa da quella mostrata.

### Non registrare una versione prima di averla applicata

Errore gemello del precedente, e più insidioso. `fetch_state` faceva così:

```cpp
if (v != id(layout_version)) {
  id(layout_version) = v;        // SBAGLIATO: segnato come applicato
  id(fetch_layout).execute();    // ...ma questa puo' fallire
}
```

Quando `fetch_layout` falliva anche una sola volta — nel caso reale, un 404
sulla pagina sparita — la versione risultava già registrata e il display non
aveva più motivo di riprovare: cristallizzato su un layout vecchio, in modo
permanente e silenzioso.

`id(layout_version)` va scritto **solo** dentro il ramo di successo di
`fetch_layout`. `fetch_state` confronta e innesca, non registra.

### `when:` vale sugli slot, non solo sulle pagine

Stesso campo a due livelli, perché è lo stesso concetto: un percorso dentro
`/state` che, se veritiero, rende visibile qualcosa.

Sugli **slot** serve a far cambiare contenuto a una casella: la riga in basso
mostra Slack / Spotify / Screenshot normalmente, e diventa ⏮ ⏯ ⏭ quando c'è un
player attivo. Più slot condividono la stessa `pos`, e la regola è
**order-independent**: a parità di casella vince quello condizionale
soddisfatto; se nessuno lo è, vince quello senza condizione. Due slot
incondizionati sulla stessa casella restano un errore di validazione, perché
quello è un refuso e non un'intenzione.

### Niente fascia fissa in alto

`HEADER_H = 0`. Volume, brano e CPU erano decorazione su un aggeggio che serve
a lanciare cose, e costavano 36 px **permanenti**.

Quello che invece conta davvero — Mac irraggiungibile, permesso Accessibilità
mancante — non è stato buttato ma spostato su una **sovrapposizione LVGL
normalmente nascosta**, che occupa spazio solo quando c'è qualcosa da dire.
Deve essere LVGL e non un pixel dell'immagine, perché l'avviso "Mac non
raggiungibile" serve esattamente quando il Mac non può più disegnare nulla.

La terza è la **barra in fondo** con le frecce e il numero di pagina: altri
28 px permanenti per due bottoni che si usano di rado, e brutta da vedere. Si
cambia pagina strisciando il dito.

Risultato cumulativo sulla griglia 3×3: tile da 115×80 (4×3 con fascia e
barra) a **154×101**, icone circa il 49% più grandi.

### Lo swipe deve annullare il tocco che sta sotto

LVGL manda `LV_EVENT_GESTURE` mentre il dito è ancora giù, e al rilascio manda
**comunque** `LV_EVENT_CLICKED` all'oggetto sotto (`indev_proc_release` non
guarda `gesture_sent`). Le aree di tocco coprono quasi tutto lo schermo, quindi
senza contromisure ogni strisciata lancerebbe anche l'azione della tile da cui
è partita: 60 px di movimento dentro una tile larga 154 finiscono ancora dentro
quella tile.

`lv_indev_wait_release(lv_indev_active())` nel gestore del gesto azzera
`act_obj`: il rilascio manda un `PRESS_LOST` — che spegne anche il velo bianco
sotto il dito — e salta il `CLICKED`.

Il gesto arriva alla pagina e non alla tile perché `LV_OBJ_FLAG_GESTURE_BUBBLE`
è acceso di default su ogni oggetto con un genitore, e la pagina, essendo uno
schermo, non ce l'ha: la risalita si ferma lì.

### La geometria non si può calcolare in validazione

Il `box` di uno slot dipende da quali **pagine e quali slot sono visibili**,
che dipende dallo stato vivo del Mac. Per questo `validate()` calcola solo
`index` e il `box` si calcola al momento di servire la richiesta.

### La versione è l'impronta del risultato, non del file

`_signature()` fa l'hash di **ciò che il display riceverebbe**: pagine visibili
e slot risolti. Così qualunque cosa cambi l'aspetto del deck
cambia la versione, senza doverci pensare caso per caso — ed è la lezione dei
tre bug di versione che l'hanno preceduta.

### Il primo caricamento non può partire a tempo

`on_boot: delay: 3s` non basta: associarsi al WiFi ne richiede sei, la
richiesta falliva con `HTTP Request failed; Not connected to network`, e la
schermata non veniva **mai** disegnata. Si parte da `wifi: on_connect:`.

### Non lanciare una richiesta HTTP da dentro il callback di un'altra

`fetch_state` chiamava `id(fetch_layout).execute()` da dentro il proprio
`on_response`. Il componente `http_request` è occupato con la richiesta in
corso e la seconda non parte — silenziosamente, senza errore.

Il difetto è rimasto coperto a lungo perché la schermata arrivava comunque dal
caricamento al boot. Quando quello è fallito (vedi sopra), è saltato fuori che
**non esisteva nessun ritentativo**: il display è rimasto nero in modo
permanente.

La correzione è una bandiera: il confronto di versione alza `serve_layout`, e
la si guarda **dopo** che la richiesta è finita, fuori dal callback. Come
effetto secondario il deck si ripara da solo, perché un tentativo fallito
viene ripetuto al ciclo dopo — verificato sul dispositivo, dove un
`Connection reset` durante il riavvio dell'agent è stato recuperato al ciclo
successivo.

### Una sonda che non riesce a girare non è un permesso negato

`_accessibility` faceva `return osascript(...).ok`, quindi un **timeout**
diventava "permesso negato". Con il Mac sotto carico (load average 40 durante
una compilazione) osascript sfora i 3 secondi, e sul display compariva un
allarme rosso falso: *"Permesso Accessibilità mancante"* mentre il permesso
c'era eccome.

Ora si distingue: un diniego vero si riconosce dal messaggio d'errore
(`-1743`, `not allowed`, `not authorized`, `assistive`); qualunque altro esito
è **incerto**, e in caso di incertezza si tiene l'ultimo valore noto — in
mancanza, si sta ottimisti. Un avviso falso è peggio di un avviso mancante,
perché insegna a ignorarlo.

### Niente spegnimento per inattività

Il display sta su una scrivania, alimentato dal cavo, e senza cavo non c'è
batteria: non c'è niente da risparmiare. `on_idle` è stato rimosso. La
retroilluminazione resta un'entità controllabile, quindi si può sempre
spegnere a comando.

### L'avanzo della divisione va diviso fra i due bordi

Con l'origine fissa a `GUTTER` tutti i pixel che la divisione intera non copre
si accumulano in fondo e a destra. Sulla 3×3 il margine destro era 6 contro 4 a
sinistra — impercettibile — ma in basso, tolta la barra di navigazione, restava
una fascia vuota di 14 px contro i 4 fra una tile e l'altra: si notava, e
sembrava che l'ultima riga fosse tagliata via.

`slot_boxes` centra la griglia su entrambi gli assi: margini 5/5 ai lati e 4/5
sopra e sotto, tutti dell'ordine dello spazio fra le tile.

`USABLE_H` esiste per il caso opposto. Il pannello è 320 nominali ma gli ultimi
pixel in fondo di questo esemplare stanno sotto la cornice, e un primo tentativo
con l'ultima riga a y=315 era sembrato tagliato. È **l'unico numero da girare**
se ricapita: la griglia si ricentra da sola.

### Il componente `http_request` ne regge una alla volta

Terza manifestazione dello stesso vincolo, dopo la chiamata annidata nel
callback. Toccando un pulsante compariva "Mac non raggiungibile": la POST di
`press_slot` e la GET del polling da 2 s si sovrapponevano, la seconda
falliva, e tre fallimenti accendevano il banner.

Tre correzioni, dalla causa all'effetto:

- `press_slot` è **`mode: queued`** e non `parallel`: due tocchi ravvicinati
  non si fanno più fallire a vicenda;
- un contatore `press_in_corso` fa **saltare un ciclo** al polling quando c'è
  un tocco in volo — il tocco ha la precedenza, lo stato può aspettare 2 s;
- la soglia del banner passa da 3 a **5 cicli**, una decina di secondi:
  un'assenza vera dura, una collisione no. Un banner che lampeggia al primo
  intoppo insegna a ignorarlo.

### `timeout: 8s` su http_request faceva buttare via gli aggiornamenti

La trappola più costosa dopo `/state`, ed era rimasta nascosta dietro un
sintomo che non la nominava: si manifesta **solo quando il Mac non risponde**,
cioè mai, durante lo sviluppo, con l'agent sempre acceso a un metro di
distanza. Salta fuori esattamente nello scenario per cui il deck è nato:
portarlo fuori casa.

`http_request` è sincrono: ogni richiesta senza risposta blocca il loop
principale per l'intero timeout. A 8 s si superava il watchdog di ESP-IDF
(5 s), il deck si riavviava, e dopo qualche riavvio ravvicinato **ESP-IDF
tornava alla partizione precedente**, scartando in silenzio il firmware
appena caricato.

Il sintomo osservato: subito dopo un OTA riuscito l'entità nuova rispondeva;
qualche minuto dopo era sparita, e `device_info().compilation_time` diceva
`10:15` mentre il binario in `.pioenvs` era delle `10:23`. Il deck stava
eseguendo la build precedente.

```bash
# Come si accerta un rollback, invece di indovinarlo:
.venv/bin/python -c "...; print(d.compilation_time)"
stat -f "%Sm" .esphome/build/macdeck/.pioenvs/macdeck/firmware.bin
```

`8013 ms` compare anche nel log del vecchio crash da watchdog: era lo stesso
timeout, che allora era stato letto come conseguenza e non come causa.

Ora è **3 s**, e non va rialzato: sotto il watchdog, e comunque tre volte il
tempo reale di risposta del Mac. In più, quando `agent_online` è falso il
polling rallenta da 2 s a 14: un deck senza Mac — fuori casa, o col portatile
chiuso — non deve passare la vita dentro un timeout.

### Il feedback al tocco non deve passare dalla rete

Sono due cose diverse e vanno tenute separate:

- **"ho sentito"** — il velo bianco sotto il dito. È lo stato `pressed` di
  LVGL, dichiarato nel widget: lo disegna il display da solo, in zero
  millisecondi, e funziona anche col Mac spento.
- **"il Mac ha eseguito"** — il lampo verde o rosso. Arriva per forza dopo,
  quando la POST ha una risposta.

Far dipendere il primo dalla risposta del Mac significherebbe un pulsante che
sembra rotto ogni volta che la rete respira.

`flash_esito` è `mode: restart` e **spegne tutte le caselle** prima di
accenderne una: due tocchi ravvicinati interrompono il primo flash a metà, e
senza quella pulizia una casella resterebbe accesa per sempre. Il parametro
`slot` non è leggibile dentro `on_response`, quindi passa da un global
(`slot_in_volo`), che è sicuro solo perché `press_slot` è `mode: queued`.

### Il Mac si presenta al deck, non viceversa

L'IP dell'agent scritto nel firmware funziona in una sola rete. Invertendo la
direzione il problema sparisce: il deck si annuncia già via Bonjour
(`_esphomelib._tcp`, gratis con l'API ESPHome), quindi è **l'agent a cercarlo
e a scrivergli dove trovarsi**, in un'entità `text` con `restore_value: true`.
Il valore sopravvive al riavvio; su una rete mai vista non c'è nulla da
configurare.

Dettagli che non sono ovvi:

- L'indirizzo da annunciare si ricava con un socket UDP *connesso* verso il
  deck (`local_ip_towards`). Con WiFi, Ethernet e VPN insieme, chiedere
  l'indirizzo dell'hostname locale restituisce la scelta sbagliata.
- Si riscrive **solo se cambia**: ogni scrittura è un ciclo di flash sul deck.
- Un `text` template accetta `restore_value: true`, e diventa scrivibile da
  qualsiasi client API — Home Assistant compreso — senza codice custom.

### La chiusura dell'API si appende subito dopo una scrittura

`await cli.disconnect()` attende una risposta dal deck. Ma appena ricevuto
l'indirizzo nuovo il deck riparte a chiamarlo, e mentre ci prova **non
risponde all'API**: il saluto ordinato va in timeout e faceva riportare come
fallita una scrittura che era invece riuscita — con l'agent che la riprovava
all'infinito.

La chiusura va quindi forzata, con un timeout suo, e i suoi errori ignorati:
il lavoro utile è già stato fatto prima.

### Un SSID vuoto non disattiva una rete

`wifi: networks:` rifiuta `ssid: ""` con "SSID can't be empty". Per lasciare
uno slot libero serve un **segnaposto**: il deck sceglie fra le reti che
effettivamente vede, quindi un SSID inesistente non costa nulla e non viene
mai tentato.

### Una rete salvata non si aggiunge alle compilate: le cancella

Questo è il difetto più costoso incontrato nel progetto, e non era nostro:
sta nel modo in cui ESPHome carica le credenziali salvate.

```cpp
SavedWifiSettings save{};
if (this->pref_.load(&save)) {
  WiFiAP sta{};
  sta.set_ssid(save.ssid);
  sta.set_password(save.password);
  this->set_sta(sta);          // <- set_sta, non add_sta
}
```

`set_sta` comincia con `clear_sta()`. Quindi **una sola** rete insegnata a
caldo — dal portale o da improv — azzera tutte le reti compilate, per
sempre, comprese quelle che funzionavano. La memoria del deck tiene una
coppia SSID/password sola: `SavedWifiSettings` non è una lista.

Il sintomo, misurato: un deck con `FASTWEB-45H7P7` e la password giusta
compilate dentro è rimasto un giorno fuori dalla rete di casa mostrando
"Mac non raggiungibile", perché all'avvio la voce in flash cancellava le tre
reti del firmware e ne imponeva una che non c'era più. La password non era
mai stata il problema.

Da qui la modalità di connessione attuale: **due reti nel firmware — casa e
ufficio — e nessun canale che possa riscriverle**. Fuori l'access point di
ripiego, fuori `captive_portal`. Resta `improv_serial` sul cavo come unica
via di riserva, con la stessa avvertenza scritta accanto: quello che passa
da lì sostituisce le due reti finché non si riflasha.

Un effetto collaterale che lavora a favore: la chiave della preferenza
deriva dall'hash della configurazione, quindi **cambiare il blocco `wifi:`
fa dimenticare al deck la rete salvata**. Il flash è anche la cura.

E il ritentativo non va aggiunto, c'è già: lo stato "solo access point" si
imposta unicamente `if (!this->has_sta())`, e con reti configurate quel ramo
non si prende mai. Il ciclo scan → connect → cooldown non si arrende. A
`reboot_timeout: 10min` resta il caso diverso, la radio impuntata, dove solo
un riavvio rimette in moto.

### Insegnare al deck una rete che non esisteva alla compilazione

> Questa sezione descrive `macdeck pair` **via WiFi**, che dipende dal
> portale: il firmware attuale non lo alza più, per la ragione sopra. Vale
> solo la variante `--usb`. Il resto è conservato perché le due trappole di
> macOS qui sotto restano vere e costano tempo a chiunque le riscopra.

Fuori casa il Mac si attacca al WiFi del posto, e quella password non
esisteva quando il firmware è stato compilato. Il pezzo che serviva c'era
già: il portale del deck esponeva `/wifisave?ssid=…&psk=…` e ESPHome salvava
quelle credenziali **in modo permanente**. `macdeck pair` si limitava a
guidarlo al posto nostro.

La regola che governa `pairing.py` è una sola, e vale più del resto:

> **La rete di partenza va ripristinata sempre**, in un `finally`, anche
> quando tutto il resto fallisce.

Il comando stacca il Mac dalla sua rete per attaccarlo all'access point del
deck. Un Mac lasciato appeso al WiFi di un display è un guaio peggiore di un
accoppiamento non riuscito — e per lo stesso motivo, **senza la password
della rete di partenza il giro non comincia nemmeno**: staccarsi senza
saperla significa non poterci più tornare.

Tre trappole di macOS lungo la strada:

- `networksetup -setairportnetwork` stampa *"Could not find network X."* ed
  esce con **codice 0**. Chi guarda il codice di uscita crede di essersi
  spostato di rete mentre non è successo niente, e la richiesta successiva
  parte dalla rete sbagliata e scade: un timeout che sembra un dispositivo
  muto. Sono due diagnosi finite fuori strada prima di leggere l'output.
  `join()` ora controlla il testo, e c'è un test che fallisce solo per
  quello.
- Da macOS 14 `networksetup -getairportnetwork` risponde *"You are not
  associated with an AirPort network"* **anche quando lo sei**: il nome della
  rete è diventato un dato protetto. Si legge da `ipconfig getsummary en0`.
- Il parsing di quell'output va ancorato a `^\s*SSID`, altrimenti la riga
  `BSSID :` viene prima e si finisce con l'indirizzo hardware al posto del
  nome. C'è un test apposta.

### Il ripiego sul cavo, quando è il WiFi a non funzionare

`macdeck pair` fallisce proprio nei casi in cui l'access point del deck non
si vede o il Mac non riesce a spostarsi di rete — cioè quando il problema è
il WiFi. Il ripiego non può quindi passare dal WiFi.

`improv_serial:` (una riga nel firmware) apre un canale sul cavo dati.
ESPHome non offre un comando per usarlo, quindi il client sta in
`pairing.improv_packet` / `pair_over_usb`: il protocollo è
`"IMPROV" | versione | tipo | lunghezza | corpo | checksum`, con ogni stringa
preceduta dalla propria lunghezza in **un byte** — da cui il limite di 255
caratteri, che va verificato prima e non scoperto dopo.

Sulla stessa porta viaggiano anche i log dell'ESP: il parser cerca
l'intestazione nel rumore e **verifica il checksum**, invece di assumere un
flusso pulito.

Il cavo serve solo per questo passaggio. Subito dopo il deck torna in WiFi
con la sola alimentazione.

### Quello che resta scoperto: l'isolamento dei client

Su molte reti pubbliche — alberghi, aeroporti, catene di bar — il router
impedisce ai dispositivi collegati di parlarsi fra loro. Lì Mac e deck
finiscono sulla stessa rete e **restano comunque invisibili l'uno
all'altro**: nessun accoppiamento può rimediare, perché non è un problema di
credenziali.

L'unica via è una rete propria, cioè l'hotspot del telefono con entrambi
attaccati. Farlo funzionare sulle reti isolate richiederebbe un secondo
trasporto completo sul cavo, immagine compresa: è un progetto a sé, non un
ripiego.

### Il Finder mostra un nome, il disco ne tiene un altro

Su un Mac in italiano **81 app su 224** hanno un nome visualizzato diverso da
quello del bundle: Anteprima/`Preview.app`, Calcolatrice/`Calculator.app`,
Terminale/`Terminal.app`. Chi configura legge il nome tradotto, lo scrive, e
non viene trovato niente — il difetto sembrava riguardare le app di sistema,
e invece riguardava la lingua.

Il nome tradotto **non sta nel bundle**: per le app Apple `InfoPlist.strings`
nella `it.lproj` non esiste proprio, lo sa solo LaunchServices. Va chiesto a
`mdls`, ma in **una sola invocazione per tutte le app** — una per bundle
significherebbe centinaia di processi:

```
mdls -name kMDItemDisplayName -raw app1.app app2.app ...   # 224 app in 0,13 s
```

Con `-raw` e più file i valori escono separati da NUL, e le app sconosciute
danno `(null)`. Il risultato sta in cache di modulo (`reset_display_names()`
per svuotarla). La ricerca per nome prova prima il disco, poi il tradotto;
`/api/icons` cerca su entrambi e **mostra** quello tradotto.

### Un limite innocuo diventa dannoso se cambia chi filtra

`/api/icons` tagliava sia le app sia i glifi a 120. Finche' il filtro stava
sul server — si passava `?q=` e tornavano i primi 120 risultati *di quella
ricerca* — il limite non si notava mai.

Il selettore ha spostato il filtro nel browser: scarica l'elenco intero e
cerca lì. Da quel momento il taglio a 120 morde **sempre**, e in ordine
alfabetico: l'ultima app servita era `MDMMigrationTrampoline`, quindi tutto
da M in poi era invisibile. Cercando "terminal" compariva iTerm — che sta
sotto la i — e Terminale no. Sembrava un difetto della ricerca, era un
difetto dell'elenco.

Le app non si tagliano più: sono 222, il JSON pesa 37 KB e viaggia su
loopback. I glifi MDI restano tagliati, perche' quelli sono settemila — ed
era per loro che il limite esisteva.

La lezione non è sul numero: **spostare il filtro da una parte all'altra
cambia il significato di ogni limite che sta in mezzo**.

### In configurazione servono tutte le tile, non quella che si vedrebbe adesso

Con gli slot condizionali più tile condividono una casella. La GUI mostrava
`tutti.find(s=>s.when) || tutti[0]`, cioè preferiva la condizionale: la terza
riga appariva come ⏮ ⏯ ⏭ e Slack/Spotify/Screenshot **non erano
raggiungibili**, nascoste dietro un contatore che non si poteva cliccare.

L'anteprima del display e l'editor hanno bisogni opposti: il display mostra
una cosa sola, l'editor deve arrivare a tutte. Ora `slotsAt` ordina in modo
stabile — prima le incondizionate, poi le condizionali — la variante 0 è lo
stato di riposo, e il contatore è un pulsante che le scorre. Il pannello dice
quale si sta modificando e sotto quale condizione compare: senza quella riga
si finisce per correggere la tile sbagliata.

### Un percorso senza prefisso non è un errore dell'utente

`resolve` cominciava con `if ":" not in spec: return fallback(size)`. Quindi
`app:/Applications/Slack.app` funzionava e `/System/Applications/Mail.app`
no — **in silenzio**, con la tile che mostrava un punto di domanda e nessuna
spiegazione da nessuna parte. Sono rimaste così tre tile configurate a mano,
e il sintomo sembrava riguardare le app di sistema.

Chi configura incolla un percorso, o scrive il nome dell'app. Pretendere il
prefisso significa fallire proprio sul gesto più naturale. Ora `guess_scheme`
guarda com'è fatto il valore — finisce in `.app`, è un file immagine, è il
nome di un'app installata — e sceglie da sé. I prefissi espliciti continuano
a vincere, e uno schema sconosciuto non viene scambiato per una cartella.

Vale in generale: quando una sintassi ha una forma "giusta" e una forma
ovvia, e sono diverse, è il codice a doversi adattare.

### Le icone stanno fuori dal layout, quindi la versione non basta

La versione è l'impronta del layout risolto — ma un'icona vive nel bundle di
un'app, sul disco. Se cambia l'icona di un'app, o cambia il modo in cui la
risolviamo, **il layout resta identico e il display non riscarica niente**:
continua a mostrare l'immagine vecchia finché non cambia altro.

È successo esattamente correggendo il difetto qui sopra: le icone si
risolvevano, i test passavano, e sul deck non cambiava nulla.

`POST /api/refresh` incrementa un contatore che entra nella firma, svuota la
cache delle tile e azzera quella dei nomi visualizzati. Nella GUI è il
pulsante "Ridisegna il display". Serve anche dopo aver aggiornato un'app che
ha cambiato icona.

### L'app davanti si legge con `lsappinfo`, non con System Events

`tell application "System Events" to get name of first process whose frontmost
is true` costa ~300 ms e passa dal permesso Accessibilità. `lsappinfo front` +
`lsappinfo info -only name,bundleid,executablepath <ASN>` costa ~10 ms e non
chiede nulla. Restituisce tre nomi che **non coincidono**: eseguibile
(`iTerm2`), nome visibile (`iTerm`, ma `Calendario` su un Mac in italiano),
bundle id (`com.googlecode.iterm2`). `app:` nel layout accetta uno qualunque
dei tre; per il gate «app in esecuzione» delle sonde si usa il bundle id, che
non dipende dalla lingua.

### Una sonda non deve mai aprire un'app

`tell application "Mail" to …` **lancia Mail** se è chiuso. Per questo le sonde
dichiarano `app=` e `StateProbe` le esegue solo se `lsappinfo list` riporta
quell'app. `media` non lo dichiara perché il suo script controlla da sé
l'elenco dei processi prima di parlare al player.

### Il server sceglie la pagina, il firmware non lo sa

Il campo `page` di `/layout` era già autoritativo (il display lo adotta quando
la pagina corrente sparisce). Per far saltare il deck alla pagina dell'app
davanti non serve altro: il server ricorda quali pagine con `app:` c'erano nel
mazzo che ha servito l'ultima volta e, se quell'insieme è cambiato, risponde
`0`. Se non è cambiato, clampa e basta — altrimenti un brano nuovo
riporterebbe alla pagina Spotify chi era andato sulla griglia. Il ricordo è
sul mazzo, non sull'app davanti, perché un cambio fra due app senza pagina
(Chrome → Safari) non cambia il mazzo e non deve mangiare lo swipe
successivo. Il ricordo si aggiorna **solo** in `/layout`: `/screen` e
`/press` non lo toccano.

### I segnaposto si risolvono in `_resolve()`, non nel renderer

`{media.title}` diventa testo prima del rendering. Così il renderer non
conosce lo stato, la cache delle tile funziona per chiave d'etichetta come
prima, e la firma del layout — che è già l'impronta del risultato risolto —
cambia da sola quando cambia un valore. Nessun codice per «invalidare quando
cambia il brano»: è gratis per costruzione.

### `toRaw` nella GUI perdeva `when:` di pagina

La funzione che riconverte il layout risolto in YAML da salvare elencava i
campi a mano e non includeva `when` sulle pagine: salvare dalla GUI cancellava
la condizione. Ora elenca anche `app`, `when`, `kind`, `caption`, `span`, e
omette `action` se manca. Regola: ogni campo nuovo di `validate()` va aggiunto
anche lì, o la GUI lo cancella in silenzio.

### Il display agisce su quello che gli è stato promesso

`/press` e `/screen?v=` non risolvono il mazzo dal vivo: leggono quello
servito all'ultimo `/layout`, tenuto in memoria dal server. Senza questa
garanzia, nella finestra fino a 2 s fra un cambio di app e il prossimo poll
del display, un tocco eseguirebbe l'azione di un'altra pagina alla stessa
casella — riprodotto prima della correzione. `/state` resta invece calcolato
dal vivo: `layout_version` cambia comunque, così il display impara lo stesso
che il mazzo è cambiato e al giro successivo chiede un nuovo `/layout`. Il
ricordo si scrive **solo** in `/layout`.

## Comandi utili

```bash
cd agent
.venv/bin/python -m pytest                        # 412 test (1 skipped), nessun hardware
.venv/bin/python -m macdeck.cli doctor            # permessi e configurazione
.venv/bin/python -m macdeck.cli token             # token da mettere nei secrets
.venv/bin/python -m macdeck.cli pair              # insegna al deck la rete di adesso
.venv/bin/python -m macdeck.cli pair --usb        # idem, sul cavo dati
.venv/bin/python -m macdeck.cli serve             # server in foreground

cd firmware
esphome config macdeck.yaml                       # validazione schema, ~10 s
esphome compile macdeck.yaml                      # compilazione C++, ~4 min
esphome run macdeck.yaml --device /dev/cu.usbmodem13301
esphome logs macdeck.yaml --device /dev/cu.usbmodem13301
```
