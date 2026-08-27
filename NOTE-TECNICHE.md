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

### L'orientamento è verticale e non è una scelta

`esphome config` rifiuta il `transform` sul display con **"Axis swapping not
supported by this model"**: nel preset di ESPHome l'AXS15231 ha
`swap_xy=cv.UNDEFINED`. Il display resta 320×480 verticale.

Non è un problema da aggirare: in verticale una griglia 3×4 dà tile da 101×99,
quasi quadrate, che per uno Stream Deck legge meglio del 4×3 orizzontale.

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
