# Manuale d'uso di Storycast

Questo manuale descrive il comportamento del progetto Storycast. I comandi e i percorsi sono stati verificati sul codice, non dedotti soltanto dai README storici.

## 1. Cos'è Storycast

Storycast trasforma un dialogo scritto in italiano in un episodio audiovisivo con più personaggi. Usa una voce TTS diversa per ogni personaggio, crea un WAV per ogni battuta, unisce l'audio, costruisce una timeline, sceglie immagini coerenti con chi parla e infine monta un video.

Il flusso è:

```text
testo → dialogo validato → TTS multivoce → segmenti WAV → audio e timeline
      → piano delle scene → immagini/crop/movimenti → video finale
```

La pipeline universale crea anche uno Short verticale associato. Prima di iniziare richiede quindi due file: l'episodio principale e il corrispondente file `-short.txt`.

Tutto il lavoro pesante avviene in container Docker, in sequenza e senza rete. La configurazione è pensata per CPU Intel con AVX ma senza AVX2.

## 2. Struttura delle cartelle

```text
storycast-docker/
├── avvia-storycast.sh       interfaccia unica consigliata
├── docker-compose.yml       servizi e volumi Docker
├── Dockerfile*              immagini controller, TTS e renderer
├── config/                  personaggi, voci, TTS, video e catalogo
├── input/                   dialoghi principali e relativi Short
├── assets/
│   ├── characters/          pose individuali dei personaggi
│   ├── groups/              immagini con più personaggi
│   └── story_images/        immagini narrative opzionali
├── models/                  modello TTS locale (non incluso nel repository)
├── storycast/               motore Python
├── scripts/                 prove accessorie, per esempio animazione facciale
├── tests/                   test automatici
├── work/                    cache, stati, segmenti e scene ricostruibili
├── output/                  audio, video, timeline e rapporti finali
├── logs/                    log delle esecuzioni
└── docs/                    documentazione tecnica e storica
```

`config/`, `input/` e `assets/` sono sorgenti modificabili con attenzione. Le immagini originali sotto `assets/` non devono essere sovrascritte, ricompresse o cancellate. `work/` contiene dati indispensabili alla ripresa e cache costose: non cancellarlo manualmente. `output/` contiene i risultati da conservare. `logs/latest.log` è un collegamento al log più recente. Compose monta `${STORYCAST_MODELS_HOST:-./models}` come `/models` nel container; impostare la variabile se il modello risiede altrove.

Le directory `assets/backgrounds`, `reference`, `poses` e `approved` non esistono nell'albero attuale. Il catalogo dinamico legge direttamente le immagini ricorsive sotto `assets/characters` e `assets/groups`, escludendo directory chiamate `approved` o `archive` qualora venissero create. Non creare copie master `approved/*_master_v1.png`: non sono richieste dal codice corrente.

## 3. Avvio di Storycast

Aprire un terminale ed entrare sempre nella directory del progetto:

```bash
cd storycast-docker
./avvia-storycast.sh help
```

Lo script non installa nulla sull'host. Richiede Docker già avviato e immagini locali già costruite. Le immagini richieste sono:

```bash
docker compose build storycast-controller storycast-tts storycast-renderer
```

La build TTS parte da `qwen3-tts-cpu:local`; il renderer parte da `voiceover-to-video:local`. Queste immagini base devono quindi già esistere localmente. Non viene eseguito alcun download automatico.

## 4. Comandi disponibili

La sintassi generale è:

```bash
./avvia-storycast.sh COMANDO [OPZIONI]
```

### Pipeline universale consigliata

| Comando | Sintassi essenziale | Funzione ed effetti |
| --- | --- | --- |
| `genera` | `genera INPUT [--nome SLUG] [--story-images ask\|yes\|no] [--review-audio] [--velocita N] [--no-speed-version] [--mock] [--sostituisci]` | Genera episodio principale e Short. Scrive in `work/episodes/SLUG` e `output/SLUG`; usa le cache valide. `--dry-run` mostra il piano senza inferenza. |
| `precheck` | `precheck INPUT [--nome SLUG]` | Valida preventivamente principale e Short gemello, personaggi e formato; non genera media. |
| `piano` | `piano INPUT [--nome SLUG] [--mock] [--dry-run]` | Mostra fasi, voci, cache, asset e output previsti. `--dry-run` con `genera` produce lo stesso tipo di piano. |
| `stato` | `stato --nome SLUG` | Stampa `work/episodes/SLUG/state.json`. |
| `segmenti` | `segmenti --nome SLUG` | Elenca indice, speaker, voce, testo, WAV, durata, hash, cache, QC e revisione. |
| `ascolta` | `ascolta --nome SLUG --indice N` | Riproduce il WAV con `ffplay` dell'host; se `ffplay` non esiste, stampa il percorso. Non cambia cache. |
| `approva` / `rifiuta` | `approva --nome SLUG --indice N` | Imposta la revisione del WAV corrente. L'approvazione è necessaria solo se la generazione è stata avviata con `--review-audio`. |
| `rigenera` | `rigenera --nome SLUG --indice N [--alternate-seed] [--prudent] [--dry-run] [--yes]` | Rigenera un solo segmento, dopo backup. Senza `--yes` chiede di digitare `SI`. Invalida merge/timeline/video successivi. |
| `riprendi` | `riprendi --nome SLUG [--velocita N] [--no-speed-version]` | Riparte dallo stato e dalle cache disponibili. |
| `verifica` | `verifica --nome SLUG` | Esegue QC senza riscriverlo e controlla la validità degli output finali. |
| `pulisci` | `pulisci --nome SLUG --dry-run` poi `... --yes` | Elenca tutto, ma elimina soltanto cache visiva, scene temporanee e frame di verifica. Il dry-run è obbligatorio come procedura operativa. |

`--velocita` (alias reale di `--final-speed`) accetta un fattore maggiore di `1.0` e non superiore a `2.0`; il default è `1.15`. `--no-speed-version` evita la variante accelerata. `--story-images ask` richiede una scelta interattiva se trova immagini narrative; `yes` le usa e `no` le ignora. `--sostituisci` permette di riutilizzare uno slug con input differente, creando prima un backup. `--refresh-voice-instructions` vale per una storia nuova; il codice rifiuta il cambio profilo su una storia esistente.

### Diagnostica e riparazione TTS universale

```bash
./avvia-storycast.sh cpu-cooldown-status
./avvia-storycast.sh cpu-cooldown-check [--nome SLUG]
./avvia-storycast.sh cpu-cooldown-plan --nome SLUG
./avvia-storycast.sh diagnostica-segmento --nome SLUG --indice N
./avvia-storycast.sh verifica-segmenti --nome SLUG
./avvia-storycast.sh segmenti-sospetti --nome SLUG
./avvia-storycast.sh ripara-segmento --nome SLUG --indice N --dry-run
./avvia-storycast.sh ripara-segmento --nome SLUG --indice N --yes
./avvia-storycast.sh ripara-sospetti --nome SLUG --dry-run
```

`ripara-segmento` tenta prima di recuperare l'ultimo WAV scartato che superi i controlli; altrimenti rigenera con seed alternativo e suddivisione prudente. `ripara-sospetti` è volutamente solo un'anteprima: senza dry-run il codice rifiuta la riparazione multipla e invita a selezionare un segmento per volta.

### Short

```bash
./avvia-storycast.sh short-audio INPUT --nome SLUG [--mock]
./avvia-storycast.sh short-video INPUT --nome SLUG [--mock]
./avvia-storycast.sh short-status INPUT --nome SLUG [--mock]
./avvia-storycast.sh short-list-segments INPUT --nome SLUG [--mock]
./avvia-storycast.sh short-ascolta --nome SLUG
./avvia-storycast.sh short-video-play --nome SLUG
```

I primi quattro comandi ricevono il file principale, dal quale ricavano automaticamente `NOME-short.txt`. Gli ultimi due riproducono l'audio o il video finale con `ffplay`, oppure ne stampano il percorso.

### Stato, log, spazio e pulizia generale

```bash
./avvia-storycast.sh logs
./avvia-storycast.sh log-last
./avvia-storycast.sh work-status [--details]
./avvia-storycast.sh clean-work --dry-run
./avvia-storycast.sh clean-work --yes
./avvia-storycast.sh spazio-lavori
./avvia-storycast.sh elimina-storia --nome SLUG --dry-run
./avvia-storycast.sh elimina-storia --nome SLUG --yes
./avvia-storycast.sh azzera-lavori --dry-run
./avvia-storycast.sh azzera-lavori --yes
```

Le operazioni distruttive devono essere precedute dal corrispondente dry-run. `elimina-storia` e `azzera-lavori` rifiutano di partire se risultano container Storycast attivi. `clean-work` gestisce cache e temporanei mediante regole proprie, mentre `pulisci` opera su una singola storia.

### Controller, TTS e strumenti storici/specialistici

Sono realmente instradati anche questi comandi:

```text
status validate parse timeline check test
tts-status tts-check tts-plan tts-generate tts-regenerate tts-verify
audio-merge audio-status
tts-real-test tts-real-test-status tts-real-test-cache-check
tts-real-test-merge tts-real-test-clean
tts-instruction-status tts-instruction-profile
tts-instruction-ab-test tts-instruction-ab-test-status
visual-status visual-check visual-plan visual-assets visual-verify
render-test render-status render-check visual-clean
visual-library-status visual-library-check visual-library-plan
visual-library-build visual-library-clean episode-01-render-library
episode-01-plan episode-01-tts episode-01-audio episode-01-status
episode-01-clean episode-01-audio-review episode-01-list-segments
episode-01-play-segment episode-01-segment-status
episode-01-regenerate-segment episode-01-rebuild-after-segment
episode-01-audio-qc episode-01-qc-status episode-01-migrate-metadata
episode-01-approve-segment episode-01-reject-segment
episode-01-review-status episode-01-visual episode-01-render
episode-01-check episode-01-build test-face-animation
```

Questi comandi appartengono a pipeline di test o alla vecchia storia fissa `storycast_episode_01`; non sono il percorso consigliato per episodi nuovi. Le opzioni comuni reali del TTS specialistico sono `INDEX`, `--input`, `--dry-run`, `--from`, `--to`, `--mock`, `--speaker`, `--yes`, `--rebuild`, `--strict`, `--alternate-seed` e `--prudent`, ma ogni sottocomando ne usa soltanto una parte. I comandi visivi accettano soltanto `--dry-run` e `--yes`. `test-face-animation` accetta `--image`, `--audio`, `--output`, `--dry-run` e parametri di animazione esposti dallo script.

Attenzione: `list-segments`, `play-segment` e `regenerate-segment` da soli **non sono comandi supportati**. Per una storia universale usare rispettivamente `segmenti`, `ascolta` e `rigenera`. Le forme inglesi esistono solo incorporate nei nomi storici `episode-01-*`.

## 5. Come creare un episodio

1. Creare il file principale, per esempio `input/mio-episodio.txt`.
2. Creare obbligatoriamente `input/mio-episodio-short.txt` con una versione breve.
3. Inserire almeno una battuta di `personaggio_1` e una di `personaggio_2` in entrambi.
4. Controllare senza generare: `./avvia-storycast.sh precheck input/mio-episodio.txt`.
5. Vedere piano e cache: `./avvia-storycast.sh piano input/mio-episodio.txt --nome mio-episodio`.
6. Generare: `./avvia-storycast.sh genera input/mio-episodio.txt --nome mio-episodio --story-images no`.
7. Attendere: TTS, pause CPU, audio, scene e rendering sono deliberatamente sequenziali.
8. Trovare i risultati in `output/mio-episodio/`.

Esempio valido per entrambi i file:

```text
[personaggio_1 | curiosa]
Benvenuti a Storycast. Oggi parliamo di un sistema davvero interessante.

[personaggio_2 | riflessivo | pausa=0,8]
Vediamo con calma come trasforma il dialogo in un video completo.
```

Per il file Short usare testo più breve, ma la stessa sintassi e almeno i due personaggi obbligatori.

## 6. Formato del file di input

Il file deve essere UTF-8. Ogni battuta inizia con un'intestazione su una riga separata:

```text
[speaker | emozione | scena=indicazione | pausa=secondi]
Testo della battuta, anche su più righe.
```

Solo lo speaker è obbligatorio. `stage` e `scena` sono sinonimi; `pause` e `pausa` sono sinonimi. La pausa accetta punto o virgola decimale, deve essere zero o positiva e viene inserita **dopo** la battuta. L'emozione è un campo libero, ma può comparire una sola volta. Le righe che iniziano con `#` prima della prima battuta sono commenti; all'interno di una battuta vengono incluse nel testo.

Errori bloccanti: testo prima di un'intestazione, parentesi quadre malformate, speaker non configurato, battuta vuota, opzione sconosciuta, campi duplicati o vuoti, pausa negativa/non numerica, file vuoto o non UTF-8. Lo speaker deve iniziare con una lettera e contenere solo lettere, numeri, `_` o `-`.

Il parser è dinamico e può accettare un terzo personaggio se esistono sia `config/characters/ID.yaml`, sia una voce abilitata in `config/voices.yaml`, sia asset individuali catalogabili. Tuttavia la pipeline corrente impone, tramite `config/pipeline.json`, che **entrambi** `personaggio_1` e `personaggio_2` compaiano nel principale e nello Short. Non esiste oggi `personaggio_3` configurato.

## 7. Personaggi

I personaggi sono scoperti dai file `config/characters/*.yaml`, non codificati come limite fisso nel parser.

| ID | Ruolo | Voce corrente | Immagine principale editoriale |
| --- | --- | --- | --- |
| `personaggio_1` | conduttrice | `Vivian` | `...talking_open_hand...png` |
| `personaggio_2` | co-conduttore | `Ryan` | `...talking_open_hand...png` |

`immagine_principale` è un riferimento editoriale opzionale. Se manca, il caricamento del personaggio non fallisce: il video viene invece validato contro il catalogo dinamico delle pose. Per aggiungere davvero un personaggio servono configurazione personaggio, configurazione voce, voce disponibile nel modello, immagini riconoscibili dal catalogo e, per lo Short, una voce di crop in `config/pipeline.json`.

## 8. Voci TTS

Il backend normale è `real` e usa localmente `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`, caricato con `Qwen3TTSModel.from_pretrained(..., device_map="cpu", dtype=float32)`. La chiamata effettiva è CustomVoice: testo italiano, `language="Italian"`, speaker Vivian/Ryan e istruzione di resa vocale.

L'audio prodotto è PCM mono 16 bit a 24.000 Hz. `personaggio_1 → Vivian` e `personaggio_2 → Ryan`; il modello dichiara disponibili anche Aiden, Dylan, Eric, Ono_Anna, Serena, Sohee e Uncle_Fu. Il profilo istruzioni predefinito è `english_default`: istruzione in inglese, testo e lingua parlata in italiano. Le storie già iniziate conservano il profilo registrato nello stato.

Entrambe le voci hanno seed base `9001`. Prima di ogni sotto-battuta il motore reimposta i generatori Python, NumPy e Torch. `--alternate-seed` aggiunge `100003`. Parametri, seed, modello, voce, istruzione e testo entrano negli hash di generazione e quindi nella cache.

Le battute vengono divise preferibilmente a punteggiatura entro 50 parole, con limite rigido 70; tra parti viene aggiunta una pausa di 0,18 s. `--prudent` usa 18/24 parole. Tra battute la pausa predefinita è 0,55 s, salvo `pausa=` esplicita.

`--mock` non carica Qwen: crea toni sinusoidali sintetici deterministici. Serve a testare orchestration, cache e rendering; non consente di giudicare pronuncia o timbro e non va confuso con un episodio vocale reale.

Dopo ogni inferenza reale il profilo conservativo attende 30 secondi; dopo un errore aggiunge 60 secondi prima del retry. È consentita una sola inferenza parallela e un retry. Non interrompere queste pause pensando che il processo sia bloccato.

## 9. Asset grafici

La versione attuale usa:

- `assets/characters/<personaggio>/`: pose individuali;
- `assets/groups/`: intro, outro, conversazione e gruppo neutro;
- `assets/story_images/`: inserti narrativi opzionali;
- `assets/backgrounds/`: non presente e non letto dal catalogo corrente.

Il catalogo accetta `.png`, `.jpg` e `.jpeg`; gli inserti narrativi accettano anche `.webp`. Le directory `approved` e `archive` sono escluse dal catalogo. Non esistono sottocartelle `reference` o `poses`: le immagini sono direttamente nelle cartelle personaggio.

Il nome file determina la classificazione. I token riconosciuti includono `talking`, `listening`, `thinking`, `relaxed`, `on_table`, `intro`, `outro`, `conversation` e `neutral`. Le pose `talking` diventano funzioni di parlato primario/alternativo; listening/thinking/relaxed servono anche nelle pause o reazioni; intro/outro/conversation sono immagini di gruppo. Le immagini derivate, adattate al video, vengono salvate in `work/visual/library_v1/derived`, mai sopra gli originali.

## 10. Come aggiungere una nuova immagine

1. Copiare una nuova immagine PNG/JPG/JPEG in `assets/characters/ID/` oppure `assets/groups/`. Per un inserto narrativo usare `assets/story_images/`.
2. Usare un nome univoco in minuscolo, per esempio `personaggio_1_talking_sorridente_brightroom_v2.png`.
3. Includere nel nome l'ID del personaggio e un token di posa riconosciuto. Per i gruppi includere `intro`, `outro`, `conversation` o `neutral`.
4. Non inserirla in `approved` o `archive`, perché verrebbe esclusa.
5. Verificare senza modificare gli originali:

```bash
./avvia-storycast.sh visual-library-check
./avvia-storycast.sh visual-library-status
./avvia-storycast.sh visual-library-build --dry-run
```

Il controllo segnala file esclusi, nomi non classificabili, risoluzioni non valide o personaggi senza pool. Il build reale crea solo copie derivate in `work/`.

## 11. Selezione delle immagini

La selezione standard è deterministica con seed `71007`. Crea un pool per ogni speaker e un pool di gruppo; dà priorità alle pose coerenti, bilancia il numero di utilizzi e conserva una cronologia per evitare ripetizioni consecutive quando esiste un'alternativa. L'inizio usa un'immagine `intro`, la fine una `outro`; pause più lunghe usano un ascoltatore o un gruppo.

Una battuta lunga viene divisa visivamente circa ogni 9 secondi, fino a due cambi posa (tre scene) per battuta. La prima parte preferisce `talking`; le successive possono usare `talking`, `thinking` o `relaxed`. La pianificazione annota i fallback se manca la posa ideale. Le durate sono quantizzate ai frame e l'ultima scena termina esattamente con l'audio.

Lo Short usa seed `93017`, preferisce asset individuali di parlato, evita lo stesso asset consecutivo, limita ogni scena a circa 7 secondi e applica crop verticale specifico per speaker.

Gli inserti da `assets/story_images` sono ordinati deterministicamente e distribuiti lontano dall'apertura (20 s) e dalla chiusura (30 s), verso intervalli di circa 60 s, con durata 6 s e dissolvenze di 0,4 s.

## 12. Crop e formato video

Il video principale è MP4, 1280×720, 30 fps, H.264 `libx264`, `yuv420p`, preset `veryfast`, CRF 20. L'audio nel video è AAC mono 24 kHz a 192 kbit/s, con `faststart`.

Le immagini della libreria vengono scalate con conservazione delle proporzioni e crop centrale fino a 16:9; le immagini narrative usano esplicitamente `scale ... force_original_aspect_ratio=increase` seguito da `crop=1280:720`. Sono applicati movimenti leggeri statici, zoom o pan.

Le scene mute vengono concatenate; poi FFmpeg associa il WAV completo. Il renderer calcola i frame dalla durata audio, duplica l'ultimo fotogramma se necessario e verifica che la differenza audio/video sia entro circa un frame (1/30 s).

Lo Short è MP4 1080×1920, 30 fps, H.264/yuv420p, AAC 128 kbit/s. Esegue un crop 9:16 centrato secondo lo speaker, accelera a 1,30×, aggiunge 2 secondi finali e sottotitoli. La variante accelerata del video principale usa normalmente 1,15×, H.264/AAC 96 kbit/s e suffisso `_speed115.mp4`.

## 13. Segmenti audio

Un segmento è il WAV di una singola battuta del parser, anche se internamente la battuta è stata suddivisa in più chunk TTS. I file sono:

```text
work/episodes/SLUG/audio_segments/0001_personaggio_1.wav
work/episodes/SLUG/metadata/audio_segments/0001_personaggio_1.json
```

Per elencare e identificare testo, speaker, voce e durata:

```bash
./avvia-storycast.sh segmenti --nome mio-episodio
```

Per ascoltare il numero 7:

```bash
./avvia-storycast.sh ascolta --nome mio-episodio --indice 7
```

Il JSON dei metadati conserva testo originale, emozione, istruzione, voce, seed, modello, chunk, durata, hash, statistiche audio e QC. Per gli Short usare `short-list-segments INPUT --nome SLUG`; non esiste un comando per riprodurre un singolo segmento Short, ma il percorso WAV è mostrato nell'elenco.

## 14. Rigenerare un segmento problematico

Quando una voce è incomprensibile, allucina parole/voce, termina bruscamente o ha durata anomala:

```bash
# 1. Elenco completo
./avvia-storycast.sh segmenti --nome mio-episodio

# 2. Ascolto del sospetto
./avvia-storycast.sh ascolta --nome mio-episodio --indice 7

# 3. Diagnosi tecnica
./avvia-storycast.sh diagnostica-segmento --nome mio-episodio --indice 7

# 4. Anteprima senza inferenza
./avvia-storycast.sh rigenera --nome mio-episodio --indice 7 --alternate-seed --prudent --dry-run

# 5. Rigenerazione reale, senza domanda interattiva
./avvia-storycast.sh rigenera --nome mio-episodio --indice 7 --alternate-seed --prudent --yes

# 6. Riascolto e approvazione
./avvia-storycast.sh ascolta --nome mio-episodio --indice 7
./avvia-storycast.sh approva --nome mio-episodio --indice 7

# 7. Ricostruzione delle fasi successive
./avvia-storycast.sh riprendi --nome mio-episodio
```

La rigenerazione salva WAV e metadata precedenti in `work/episodes/SLUG/backups/segment_NNNN_DATA/`, modifica soltanto il segmento selezionato, riporta la revisione a `pending_review` e segna la storia in attesa. `--alternate-seed` cambia il campionamento; `--prudent` spezza maggiormente il testo. Provare prima il dry-run non modifica file e non esegue inferenza.

Alternativa assistita:

```bash
./avvia-storycast.sh ripara-segmento --nome mio-episodio --indice 7 --dry-run
./avvia-storycast.sh ripara-segmento --nome mio-episodio --indice 7 --yes
```

Non usare gli esempi `list-segments`, `play-segment NUMERO` o `regenerate-segment`: non sono alias reali della pipeline universale.

## 15. Cache

La cache evita di ripetere inferenze TTS molto lente e rendering identici. Per un WAV servono sia il file sia il JSON dei metadati. Vengono controllati integrità WAV, hash del testo, modello, backend, voce, lingua, istruzione/profilo, parametri, seed effettivo, stato della generazione, schema QC e hash del WAV.

- Cambiare testo invalida il segmento corrispondente.
- Cambiare voce, istruzione, parametri o modello invalida la cache.
- Cambiare seed cambia l'hash di generazione; un seed alternativo registrato resta però una cache valida per quel WAV.
- Un WAV parziale, sospetto, senza metadati o con hash incoerente viene rigenerato.
- `rigenera --indice N` forza sempre quel segmento anche se valido.

Le scene video hanno un sidecar JSON con chiave derivata da scena, comando FFmpeg e hash dell'immagine derivata. Se coincide, la scena viene riutilizzata. Stato e hash in `state.json` permettono a `riprendi` di saltare fasi complete. Non cancellare manualmente metadata o stato pensando che siano soltanto temporanei.

## 16. Output

Per lo slug `mio-episodio`:

| File | Percorso |
| --- | --- |
| Segmenti principali | `work/episodes/mio-episodio/audio_segments/*.wav` |
| Metadata segmenti | `work/episodes/mio-episodio/metadata/audio_segments/*.json` |
| Stato e piano TTS | `work/episodes/mio-episodio/state.json`, `metadata/tts_plan.json` |
| Scene e cache video | `work/episodes/mio-episodio/scenes/` |
| Audio completo | `output/mio-episodio/mio-episodio_audio.wav` |
| Manifest audio | `output/mio-episodio/mio-episodio_audio_manifest.json` |
| Timeline | `output/mio-episodio/mio-episodio_timeline.json` |
| Piano visivo | `output/mio-episodio/mio-episodio_visual_plan.json` |
| Video normale | `output/mio-episodio/mio-episodio_video.mp4` |
| Video accelerato default | `output/mio-episodio/mio-episodio_video_speed115.mp4` |
| Rapporto | `output/mio-episodio/mio-episodio_report.json` |
| Audio Short | `output/mio-episodio/mio-episodio_short_audio.wav` |
| Timeline/manifest Short | `..._short_timeline.json`, `..._short_audio_manifest.json` |
| Video e sottotitoli Short | `..._short_video.mp4`, `..._short_subtitles.srt` |

Il manifest audio registra segmenti e pause con inizio, fine e durata. La timeline aggiunge testo, speaker, voce, file e timing. Il piano visivo associa ciascun intervallo a asset, posa e movimento.

## 17. Log

Ogni `genera` crea `logs/SLUG_DATA.log`; `logs/latest.log` punta al più recente. Contiene fase, messaggi TTS, conteggi cache, avanzamento scena per scena, durate, warning, output ed errore conclusivo.

```bash
./avvia-storycast.sh logs
./avvia-storycast.sh log-last
tail -f logs/latest.log
```

`logs` mostra gli ultimi 20 log, escluso il link `latest.log`; `log-last` stampa le prime 240 righe dell'ultimo. Durante un errore cercare `ERRORE`, la fase indicata e l'ultimo segmento/scena completato. Anche lo stato persistente in `work/episodes/SLUG/state.json` registra fase ed elenco errori.

## 18. Docker

I servizi sono:

- `storycast-controller`: parser, controlli e test; 1 CPU, 256 MiB.
- `storycast-tts`: orchestratore e Qwen TTS CPU; 2 CPU, 8 GiB.
- `storycast-renderer`: catalogo e FFmpeg; 2 CPU, 2 GiB.
- `storycast-cleanup`: operazioni di inventario/pulizia con immagine controller.

Codice, test, input, config e asset sono montati in sola lettura. `work`, `output` e `logs` sono scrivibili. Tutti i servizi usano `network_mode: none`; TTS imposta anche Hugging Face/Transformers offline, CUDA vuoto, ISA massima AVX e due thread OMP/MKL. Il modello host viene montato in sola lettura sotto `/models`.

Lo script usa sempre `docker compose run --rm`, perciò il container del comando viene rimosso alla fine. Per controllare:

```bash
docker compose ps
docker ps --filter name=storycast
```

Al termine non dovrebbero esserci container Storycast attivi. Non usare `docker compose down` durante una generazione.

## 19. Risoluzione problemi

| Problema | Possibile causa | Come risolvere |
| --- | --- | --- |
| Immagine master mancante | `immagine_principale` punta a un file assente | Non creare master artificiali: verificare che il catalogo dinamico abbia pose valide con `visual-library-check`; il riferimento editoriale è opzionale. |
| Catalogo visuale vuoto | Nessuna immagine classificabile in characters/groups | Controllare estensioni, directory e nomi; usare `visual-library-check`. |
| TTS incomprensibile/allucina | Campionamento sfavorevole o battuta complessa | Ascoltare, poi `rigenera ... --alternate-seed --prudent --dry-run` e infine `--yes`. |
| Segmento troppo lungo | Testo lungo o durata fuori soglia | Usare diagnosi e rigenerazione `--prudent`; non tagliare il WAV manualmente. |
| File input non valido | UTF-8 errato, intestazione o pausa malformata | Eseguire `precheck`; correggere la riga indicata. |
| Speaker sconosciuto | ID assente da `config/characters` o senza voce | Usare un ID configurato o completare tutte le configurazioni richieste prima di generare. |
| Immagine non riconosciuta | Nome senza ID/token, formato escluso o cartella `approved/archive` | Rinominare la **nuova copia** con convenzione valida e rieseguire il check; non alterare asset sorgenti già approvati. |
| Docker non avviato | Demone non disponibile | Avviare Docker con gli strumenti normali dell'host e verificare con `docker info`; Storycast non può avviarlo. |
| Immagine Docker assente | `storycast-tts:local` o renderer/controller non costruita | Eseguire la build locale indicata in sezione 3, dopo aver verificato le immagini base. |
| Modello TTS non trovato | `models/.../config.json` assente o mount diverso | Controllare `STORYCAST_MODELS_HOST` e il modello locale; non viene scaricato automaticamente. |
| Cache incoerente | WAV/JSON/hash o config non coincidono | Usare `verifica-segmenti` e rigenerare solo gli indici segnalati. |
| Video non generato | Audio incompleto, catalogo invalido o FFmpeg fallito | Leggere `log-last`, verificare segmento e piano, poi `riprendi --nome SLUG`. |
| Audio/video non sincronizzati | Render interrotto o output/scene non coerenti | Eseguire `verifica`; lasciare che `riprendi` rigeneri le fasi invalide, senza montaggi manuali. |
| Short associato mancante | Non esiste `NOME-short.txt` | Crearlo accanto al principale con entrambi i personaggi e rilanciare `precheck`. |
| Spazio disco insufficiente | Meno di 1 GiB libero o scene/cache molto grandi | Usare `spazio-lavori`, `work-status --details`, poi i dry-run di pulizia e confermare soltanto ciò che è ricostruibile. |
| Processo apparentemente fermo | Pausa CPU prudenziale di 30/60 s | Controllare log e `cpu-cooldown-status`; attendere senza terminare il container. |
| Slug già usato con testo diverso | Protezione contro sovrascrittura | Preferire un nuovo `--nome`; usare `--sostituisci` solo consapevolmente, sfruttando il backup automatico. |

## 20. Procedura rapida — Generare un episodio

```bash
cd storycast-docker

nano input/mio-episodio.txt
nano input/mio-episodio-short.txt

./avvia-storycast.sh precheck input/mio-episodio.txt --nome mio-episodio
./avvia-storycast.sh genera input/mio-episodio.txt --nome mio-episodio --story-images no
```

Risultati principali:

```text
output/mio-episodio/mio-episodio_video.mp4
output/mio-episodio/mio-episodio_audio.wav
output/mio-episodio/mio-episodio_short_video.mp4
output/mio-episodio/mio-episodio_short_subtitles.srt
```

## 21. Procedura rapida — Correggere una voce sbagliata

```bash
# 1. Elenca
./avvia-storycast.sh segmenti --nome mio-episodio

# 2. Ascolta
./avvia-storycast.sh ascolta --nome mio-episodio --indice 7

# 3. Controlla e rigenera soltanto quel segmento
./avvia-storycast.sh rigenera --nome mio-episodio --indice 7 --alternate-seed --prudent --dry-run
./avvia-storycast.sh rigenera --nome mio-episodio --indice 7 --alternate-seed --prudent --yes

# 4. Riascolta, approva e ricrea le fasi successive
./avvia-storycast.sh ascolta --nome mio-episodio --indice 7
./avvia-storycast.sh approva --nome mio-episodio --indice 7
./avvia-storycast.sh riprendi --nome mio-episodio
```

# Note tecniche e possibili anomalie rilevate

| File coinvolto | Comportamento osservato | Possibile conseguenza |
| --- | --- | --- |
| `avvia-storycast.sh` | L'help cita genericamente `*-clean --dry-run`, ma i parser richiedono talvolta anche `--yes`; `visual-clean` non è elencato nella lista iniziale pur essendo instradato. | Affidarsi alla tabella di questo manuale e fare sempre dry-run prima di una pulizia. |
| `storycast/short_pipeline.py` | `short_status()` riporta internamente `video_status: not_implemented`, benché `short_video.py` e il comando `short-video` implementino il video. | Il campo di stato può risultare obsoleto rispetto ai file reali. |
| `storycast/orchestrator.py` | Nel ramo cached viene ricostruita la libreria e viene comunque completato il pacchetto Short; `genera` non è quindi un comando esclusivamente per il video principale. | Un rilancio può richiedere tempo anche quando il principale è già valido. |
| `storycast/orchestrator.py` e `storycast/visual_library.py` | Il conteggio inserti viene letto prima e dopo l'applicazione delle story images; il piano scritto è quello aggiornato. | I messaggi intermedi possono essere meno intuitivi, mentre output e piano finale restano la fonte corretta. |
| `config/characters/*.yaml` | `immagine_principale` non è un master obbligatorio: se manca viene silenziosamente impostata a `None`. | Documentazione storica sui master può far credere che il progetto debba fermarsi, ma il catalogo dinamico decide gli asset video. |
| `config/visual_library.yaml` | Esclude `approved` e `archive`; nell'albero corrente non esistono `reference`, `poses`, `approved` né `assets/backgrounds`. | Immagini collocate seguendo una struttura storica `approved/` non entrerebbero nel catalogo. |
| `storycast/core.py` | Il campo `audio_file` iniziale usa il percorso legacy `work/audio_segments/...`; la pipeline universale lo sostituisce con `work/episodes/SLUG/audio_segments/...` quando crea la timeline temporizzata. | Il JSON di dialogo grezzo può mostrare un percorso diverso da timeline e manifest finali. |
| `storycast/tts.py` | Ogni chunk della stessa battuta reimposta lo stesso seed. | È deterministico, ma chunk simili possono partire con caratteristiche molto simili; è il comportamento corrente, non un errore da correggere qui. |
| Pipeline `episode-01-*` | Usa percorsi fissi e vincolo di 8–14 battute/30–60 secondi, diversi dalla pipeline universale. | Non usare questi comandi come modello per episodi arbitrari. |
| `work/goal2_short_mock_cache_result.json`, `work/goal2_short_mock_result.json`, `work/goal3_real_short_video_result.json`, `work/goal31_real_render_result.json` | Sono acquisizioni storiche di output: prima dell'oggetto JSON contengono righe di avanzamento `[storycast]`. Non sono quindi JSON puri nonostante l'estensione. | Un parser JSON diretto li rifiuta; non sono configurazioni usate dalla pipeline corrente e non sono stati corretti in questo goal. |

Il progetto contiene numerosi rapporti `RISULTATO_*.md`, output e cache di goal precedenti. Sono utili come cronologia, ma il comportamento operativo descritto sopra segue gli entry point e le configurazioni correnti.
