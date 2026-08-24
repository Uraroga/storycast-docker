# Architettura

## Principi

Il controller è Python standard library in un container `python:3.11-slim`, senza rete, con limite di 1 CPU e 256 MB. TTS e `storycast-renderer` sono servizi batch separati, CPU-only e terminano dopo ogni comando. Il renderer ha 2 CPU/2 GB, FFmpeg locale, rete disabilitata, asset/config read-only e scrittura limitata a work/output/logs.

## Struttura

- `input/`: dialoghi sorgente UTF-8.
- `config/characters/`: catalogo dinamico dei personaggi; ogni YAML dichiara `id` e può indicare un riferimento editoriale opzionale `immagine_principale`.
- `config/groups/`: gruppi e composizioni.
- `models/`: punto previsto per modelli locali; il modello già disponibile è riusato tramite volume esterno read-only.
- `assets/`: immagini sorgente immutabili dei personaggi e dei gruppi; `approved` e `archive` sono escluse dal catalogo video.
- `storycast/`: parser, validazione, timeline e CLI.
- `scripts/`: strumenti futuri specifici.
- `tests/`: test automatici senza modelli.
- `work/`: JSON e media intermedi rigenerabili.
- `output/`: risultati finali futuri.
- `logs/`: log sintetici persistenti di ogni esecuzione reale, con `latest.log` e conservazione degli ultimi 20 file riconosciuti.
- `docs/`: specifiche e roadmap.

## Pipeline

`storycast-controller` valida dialogo e timeline. Lo script indirizza audio a `storycast-tts` e immagini/render a `storycast-renderer`, eseguiti in sequenza. Il catalogo firma ogni ritaglio con master, coordinate e risoluzione; il planner trasforma la timeline reale in scene contigue; FFmpeg produce segmenti H.264 e il mux finale AAC. File e nomi deterministici consentono ripresa e cache.

Gli episodi usano namespace dedicati (`work/episode_01`, manifest/timeline/piano nominati e output separati). L'orchestratore shell attraversa TTS, merge, planner, render e check in container distinti, così TTS e rendering non sono contemporanei.

Il comando universale usa `storycast.orchestrator` nel servizio batch TTS e rimappa i percorsi dei moduli esistenti verso `work/episodes/<slug>/` e `output/<slug>/`. Inferenza e FFmpeg restano sequenziali. Lo stato atomico e i sidecar firmati consentono ripresa a livello di WAV, audio, piano, crop e singola scena. I percorsi fissi dell'episodio 01 restano soltanto nei moduli legacy compatibili, non nell'orchestratore generale.

Il lock è per slug e combina hostname, PID e istante di avvio letto da `/proc`; questo distingue un processo attivo da un PID riciclato o da un container terminato. I lock obsoleti vengono conservati con suffisso timestamp.

Il parser non contiene un elenco fisso di personaggi: carica ogni YAML nella directory dedicata e verifica gli ID duplicati. Il riferimento master è opzionale; gli asset effettivi vengono validati dal loader dinamico. Aggiungere un terzo personaggio richiede una nuova configurazione, una voce e una directory `assets/characters/<id>/`, non modifiche al motore.

## Sicurezza dei dati

Nel Compose `assets`, `config`, `input` e modelli sono read-only. Soltanto `work`, `output` e `logs` sono scrivibili. Entrambi i servizi hanno rete disabilitata; TTS usa `no-new-privileges`, nessuna GPU e limiti ISA AVX. `clean-work` inventaria l'intera directory `work/`, preserva soltanto eventuali `.gitkeep` e richiede dry-run e conferma esplicita.

Il nuovo orchestratore accetta input esclusivamente sotto `input/`, applica una regex allo slug e costruisce ogni destinazione da componenti già validati. Un hash input diverso per uno slug esistente blocca la sovrascrittura.
## Bundle episodio e Short

Ogni episodio principale `input/<nome>.txt` è associato automaticamente a
`input/<nome>-short.txt`. Il comando `genera` esegue un precheck completo di
entrambi prima di creare cache o avviare il TTS. I personaggi obbligatori sono
dichiarati in `config/pipeline.json`, non nel motore.

La pipeline opera in quattro fasi sequenziali: `precheck`, episodio principale,
Short e controlli finali. La fase Short riusa parser, voci, backend TTS, seed,
pause, verifica WAV e merge audio, ma scrive segmenti e metadati nel namespace
isolato `work/episodes/<slug>/short/`. Produce `<slug>_short_audio.wav` e
`<slug>_short_timeline.json` dentro `output/<slug>/`.

Lo stato audio è `audio_ready`; dopo `short-video` lo stato video diventa
`video_ready`. Il renderer usa pose individuali del catalogo, crop 9:16
configurati per speaker e scene naturali che includono anche le pause audio.
Sottotitoli brevi, bilanciati su massimo due righe, vengono impressi nel master
naturale e salvati come SRT con timestamp riscalati alla versione finale.
Prima della segmentazione viene applicata la mappa `short.display_aliases`:
questa modifica soltanto la grafia visuale (ASS, SRT e futuri testi display),
lasciando invariati dialogo, timeline, piano TTS, hash e audio.

Il master naturale resta sotto `work/episodes/<slug>/short/video/`. Il filtro
finale applica una sola volta 1,30x a video e audio (`setpts` e `atempo`), poi
produce due secondi effettivi di ultimo fotogramma e silenzio. Variazioni a
velocità o hold non invalidano i WAV Qwen.

Comandi dedicati:

```bash
./avvia-storycast.sh short-audio input/storia.txt --nome storia
./avvia-storycast.sh short-status input/storia.txt --nome storia
./avvia-storycast.sh short-list-segments input/storia.txt --nome storia
./avvia-storycast.sh short-video input/storia.txt --nome storia
./avvia-storycast.sh short-video-play --nome storia
```

`short-audio` esegue sempre il precheck del bundle, poi lavora esclusivamente
sulle battute Short. `--mock` seleziona il backend sintetico per test locali.

## Generazione integrata del pacchetto

Il comando `genera` è il flusso normale e completa in ordine stretto precheck,
episodio principale, verifica principale, audio Short, video Short e verifica
del pacchetto. `short-audio` e `short-video` restano strumenti diagnostici.

Lo stato principale registra `main_episode`, `short_audio`, `short_video` e
`package`. Se lo Short fallisce dopo un principale valido, il pacchetto resta
`partial`, il video principale non viene rimosso e una nuova esecuzione può
riusare WAV, scene e output firmati. Il video Short è riusato solo quando hash
di audio, timeline, configurazione, MP4 e SRT coincidono e il probe conferma
risoluzione e codec; questo impedisce anche una seconda velocizzazione.

La variante accelerata del video principale legge il fattore centralizzato da
`config/render.yaml`: il default è 1,15x e produce `_video_speed115.mp4`.
`--velocita` (alias compatibile `--final-speed`) ha precedenza sul default. La
velocità Short rimane indipendente a 1,30x con hold finale di due secondi.

La pulizia globale usa la whitelist `output/`, `work/`, `logs/`; include quindi
anche l'intero namespace `work/episodes/<slug>/short/` e gli output Short. Input,
prompt, asset, codice, script e configurazioni non sono candidati e `input/` è
montata read-only nel container di pulizia. Dry-run e `--yes` sono mutuamente
esclusivi e le directory runtime necessarie vengono mantenute o ricreate.

## Inserti immagini della storia

`storycast.story_images` legge soltanto i file supportati direttamente sotto
`assets/story_images/`, li ordina senza dipendere dall'ordine del filesystem e
firma modalità, configurazione, percorsi, dimensioni e SHA-256. La scelta
`ask|yes|no` avviene prima della generazione principale; `ask` non interattivo
equivale a `no` per evitare blocchi nelle automazioni.

Il planner distribuisce un massimo di un inserto ogni 60 secondi nella finestra
compresa tra il secondo 20 e 30 secondi prima della fine. Ogni inserto sostituisce
per 6 secondi le scene Storycast sottostanti, mantenendo invariata la timeline
audio, ed è renderizzato H.264 con crop dominante 16:9, zoom fino a 1,035 e fade
in/out di 0,4 secondi. Il post-processing principale a 1,15x avviene dopo.
Cambiare scelta, set, hash o configurazione invalida principale e variante
accelerata, ma non namespace, stato o cache Short. La pulizia non include mai
`assets/story_images/`, che resta una sorgente utente read-only nei renderer.
