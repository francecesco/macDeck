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
refresh, e in più il Mac guadagna il controllo di ogni pixel, navbar inclusa.

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

Risultato cumulativo sulla griglia 3×3: tile da 115×80 (4×3 con fascia e
barra) a **154×101**, icone circa il 49% più grandi.

### La geometria non si può calcolare in validazione

`slot_boxes` dipende da `navbar`, che dipende da quante pagine sono **visibili**,
che dipende dallo stato vivo del Mac. Per questo `validate()` calcola solo
`index` e il `box` si calcola al momento di servire la richiesta.

Con una pagina sola la navbar sparisce e i suoi 28 px vanno alle tile: la
griglia 3×3 passa da 154×80 a 154×89. Il firmware deve nascondere le due aree
di tocco delle frecce quando `/layout` risponde `"nav": false`, altrimenti
restano sopra la riga in basso e rubano i tocchi.

### La versione è l'impronta del risultato, non del file

`_signature()` fa l'hash di **ciò che il display riceverebbe**: pagine visibili,
slot risolti, flag della navbar. Così qualunque cosa cambi l'aspetto del deck
cambia la versione, senza doverci pensare caso per caso — ed è la lezione dei
tre bug di versione che l'hanno preceduta.

## Comandi utili

```bash
cd agent
.venv/bin/python -m pytest                        # 150 test, nessun hardware
.venv/bin/python -m macdeck.cli doctor            # permessi e configurazione
.venv/bin/python -m macdeck.cli token             # token da mettere nei secrets
.venv/bin/python -m macdeck.cli serve             # server in foreground

cd firmware
esphome config macdeck.yaml                       # validazione schema, ~10 s
esphome compile macdeck.yaml                      # compilazione C++, ~4 min
esphome run macdeck.yaml --device /dev/cu.usbmodem13301
esphome logs macdeck.yaml --device /dev/cu.usbmodem13301
```
