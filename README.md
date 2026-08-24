# Storycast

Storycast trasforma un dialogo strutturato in un episodio audio e video. Il controller valida l'input e orchestra, in sequenza, sintesi vocale CPU-only con Qwen3-TTS, controlli audio, pianificazione visiva e rendering FFmpeg. Tutte le dipendenze e le elaborazioni restano in container Docker; il motore scopre personaggi, voci e asset dalla configurazione invece di fissarne il numero nel codice.

Il TTS reale usa il profilo CPU prudente (30 s dopo ogni inferenza, più 60 s prima dell’unico retry) e QC audio v4 con WAV atomici. Vedere `docs/PAUSE_CPU.md` e `docs/SEGMENTI_TTS_TRONCATI.md`.

## Creare una storia in quattro passi

1. Scrivi il dialogo principale e il relativo Short nel formato Storycast.
2. Salvali come `input/nome_storia.txt` e `input/nome_storia-short.txt`.
3. Esegui:

   ```bash
   ./avvia-storycast.sh genera input/nome_storia.txt
   ```

4. Trova il risultato in:

   ```text
   output/nome_storia/nome_storia_audio.wav
   output/nome_storia/nome_storia_video.mp4
   output/nome_storia/nome_storia_short_audio.wav
   output/nome_storia/nome_storia_short_video.mp4
   ```

Entrambi i file sono obbligatori prima della pipeline universale e devono contenere i personaggi configurati. Il comando valida il bundle, associa le voci, genera sequenzialmente i WAV con Qwen3-TTS, esegue il controllo tecnico, unisce l'audio, crea timeline e regia visiva, renderizza e verifica video principale e Short. Tutto avviene offline in Docker; sull'host non viene installato nulla.

Il codice Python locale è montato read-only in tutti i servizi Docker: dopo una modifica al motore non serve ricostruire le immagini. Il rebuild è necessario soltanto se cambiano Dockerfile o dipendenze di base.

La libreria visiva reale viene scoperta ricorsivamente in `assets/characters/<id>/` e `assets/groups/`. I riferimenti `immagine_principale` nelle schede personaggio sono opzionali e non alimentano il montaggio; le sottocartelle `approved` e `archive` sono escluse dal catalogo. Per verificare esattamente directory, permessi e conteggi visti dal container usare `./avvia-storycast.sh visual-library-check`.

Gli esempi minimi realmente pubblicati sono `input/MODELLO_DIALOGO.txt` e `input/MODELLO_DIALOGO-short.txt`.

## Installazione da zero

Servono Docker Engine con Compose v2, spazio per immagini e modello, e almeno 8 GB di RAM disponibili al TTS. Il repository contiene codice, Dockerfile e asset; non contiene immagini Docker binarie, modello AI, input reali o output.

```bash
git clone https://github.com/Uraroga/storycast-docker
cd storycast-docker
./download-model.sh
./build-storycast.sh
./avvia-storycast.sh precheck input/MODELLO_DIALOGO.txt --nome esempio
./avvia-storycast.sh piano --input input/MODELLO_DIALOGO.txt --nome esempio --dry-run
```

`download-model.sh` è l'azione esplicita che scarica il modello ufficiale (circa 4,2 GiB); `build-storycast.sh` non lo scarica. In alternativa:

```bash
export STORYCAST_MODELS_HOST=/percorso/alla/directory-che-contiene-il-modello
./build-storycast.sh
```

La directory indicata deve contenere `Qwen3-TTS-12Hz-1.7B-CustomVoice/`. Struttura e verifica sono in [MODELLI.md](docs/MODELLI.md). La build è idempotente; `STORYCAST_FORCE_BASE_BUILD=1 ./build-storycast.sh` forza la ricostruzione delle basi.

La preparazione può richiedere Internet per immagini pubbliche, pacchetti e modello. L'esecuzione è offline: i servizi usano `network_mode: none`, `HF_HUB_OFFLINE=1` e `TRANSFORMERS_OFFLINE=1`.

Formato minimo del dialogo:

```text
[personaggio_1|curiosa]
Oggi vorrei raccontare una storia particolare.

[personaggio_2|riflessivo]
Allora cominciamo dall'inizio.
```

## Varianti utili

```bash
./avvia-storycast.sh genera
./avvia-storycast.sh genera input/nuova_storia.txt --nome nuova_storia
./avvia-storycast.sh piano --input input/nuova_storia.txt --nome nuova_storia --dry-run
./avvia-storycast.sh genera input/nuova_storia.txt --review-audio
./avvia-storycast.sh stato --nome nuova_storia
./avvia-storycast.sh segmenti --nome nuova_storia
./avvia-storycast.sh ascolta --nome nuova_storia --indice 1
./avvia-storycast.sh approva --nome nuova_storia --indice 1
./avvia-storycast.sh riprendi --nome nuova_storia
./avvia-storycast.sh verifica --nome nuova_storia
./avvia-storycast.sh pulisci --nome nuova_storia --dry-run
./avvia-storycast.sh elimina-storia --nome nuova_storia --dry-run
./avvia-storycast.sh spazio-lavori
./avvia-storycast.sh work-status
./avvia-storycast.sh work-status --details
./avvia-storycast.sh clean-work --dry-run
./avvia-storycast.sh clean-work --yes
```

## Versione finale accelerata

Al termine del montaggio Storycast conserva il video normale e produce, per impostazione predefinita, anche la variante principale approvata a `1.15x`. Audio e video vengono accelerati insieme con `atempo` e `setpts`, mantenendo sostanzialmente invariata la tonalità. Da `storia_video.mp4` deriva quindi `storia_video_speed115.mp4`. Lo Short conserva indipendentemente velocità `1.30x` e hold finale di 2 secondi.

```bash
./avvia-storycast.sh genera input/storia.txt --nome storia
./avvia-storycast.sh genera input/storia.txt --nome storia --velocita 1.20
./avvia-storycast.sh genera input/storia.txt --nome storia --no-speed-version
```

Le opzioni CLI hanno precedenza sulla sezione `final_speed_version` di `config/render.yaml`, che permette di disabilitare la variante e configurare fattore, suffisso, preset, CRF e bitrate AAC. `--sostituisci` autorizza anche la sostituzione controllata di una variante accelerata preesistente; altrimenti una variante già valida viene riutilizzata. Un errore nel post-processing non elimina né sovrascrive mai il video normale.

Ogni storia usa `work/episodes/<slug>/` e `output/<slug>/`. Un rilancio con lo stesso input recupera WAV, crop, scene e output validi. Se lo stesso slug viene associato a contenuto diverso il comando si ferma; usare un nuovo `--nome` oppure l'opzione intenzionale `--sostituisci`, che crea prima un backup degli output esistenti.

## Immagini opzionali della storia

Le immagini `.png`, `.jpg`, `.jpeg` e `.webp` collocate direttamente in
`assets/story_images/` possono essere usate come inserti nel solo video
principale. `genera` chiede conferma in un terminale interattivo; automazioni e
test usano `--story-images yes` oppure `--story-images no`. In modalità `ask`
non interattiva Storycast continua senza immagini e non resta bloccato.

Gli inserti durano 6 secondi, hanno fade di 0,4 secondi e zoom lento. Sono
distribuiti deterministicamente tra 20 secondi dall'inizio e 30 secondi dalla
fine, con densità massima indicativa di uno ogni 60 secondi. La variante 1,15x
viene prodotta dopo il montaggio naturale. Contenuto e configurazione delle
immagini partecipano alla firma cache del principale; lo Short non le legge.

## Cartella work e pulizia

`work/` è lo spazio di lavorazione riprendibile: durante una generazione viene nuovamente popolato con stato, copie sorgente, WAV e metadata dei segmenti, timeline, cache visiva e scene intermedie. I WAV e i metadata sono necessari anche a `segmenti`, `ascolta`, rigenerazione selettiva e ripresa dopo un'interruzione; le scene e le derivate evitano rendering già validi.

`work-status` mostra un riepilogo senza modificare file; `--details` aggiunge una sola riga per episodio e una ripartizione per tipo. `clean-work` elimina intenzionalmente tutte le lavorazioni presenti in `work/`, incluse cache TTS locali, dati di ripresa e dati necessari ai comandi segmento. Non elimina la cache/modelli esterni montati in `/models`, né `assets/`, `config/`, `output/`, `logs/` o eventuale `cache/` esterna a `work/`.

Per sicurezza la pulizia richiede sempre prima un dry-run e poi conferma esplicita:

```bash
./avvia-storycast.sh clean-work --dry-run
./avvia-storycast.sh clean-work --yes
```

La normale generazione popolerà nuovamente `work/`. Usare `clean-work --yes` soltanto quando si vuole davvero rinunciare alla ripresa delle lavorazioni e alla rigenerazione basata sui segmenti presenti.

## Log sintetici

Ogni esecuzione non-dry-run di `genera` crea un log persistente in `logs/<episodio>_AAAAMMGG_HHMMSS.log`; collisioni nello stesso secondo ricevono un suffisso numerico e non sovrascrivono mai un file precedente. `logs/latest.log` è un collegamento relativo all'esecuzione più recente e resta valido sia dall'host sia dal container.

Il log contiene soltanto avvio, input, conteggi di dialogo/personaggi/immagini, riepilogo TTS e cache, audio, timeline visiva, una riga di avanzamento per scena, montaggio, durate, warning significativi ed esito o errore di fase. Non contiene testo delle battute, progressi per frame o comandi FFmpeg completi.

```bash
./avvia-storycast.sh logs
./avvia-storycast.sh log-last
tail -f logs/latest.log
```

Sono conservati automaticamente gli ultimi 20 file riconosciuti come log Storycast; file manuali o con altri nomi non vengono rimossi. Il valore può essere cambiato con `STORYCAST_LOG_KEEP`. Il livello normale è `INFO`; `STORYCAST_LOG_LEVEL=WARNING`, `ERROR` o `DEBUG` modifica la soglia. `DEBUG` è previsto per diagnosi future ma la pipeline normale non emette dettagli verbosi. Il fuso predefinito è `Europe/Rome`, configurabile con `STORYCAST_TIMEZONE`.

La revisione umana non viene simulata: il QC automatico verifica soltanto caratteristiche tecniche, non la fedeltà semantica del parlato.

Dopo aver copiato il risultato fuori dal progetto, gli artefatti generati di una storia possono essere rimossi senza backup con `elimina-storia --nome SLUG --dry-run`, seguito da `--yes`. Gli input principali e Short restano sempre preservati. `azzera-lavori --dry-run` inventaria tutti i dati nelle sole aree runtime autorizzate `output/`, `work/` e `logs/`; `azzera-lavori --yes` li elimina integralmente. Vedere [Cancellazione storia](docs/CANCELLAZIONE_STORIA.md).

Per le nuove storie il profilo predefinito è `english_default`: testo e lingua dichiarata restano italiani, mentre tono e ritmo sono formulati in inglese. Il profilo `italian_legacy` resta disponibile. Vedere [Istruzioni vocali](docs/ISTRUZIONI_VOCALI.md).

## Requisiti e build manuale

- Docker con Compose;
- almeno 8 GB di RAM disponibili al servizio TTS;
- modello Qwen3-TTS locale montato read-only come descritto in [MODELLI.md](docs/MODELLI.md);
- nessuna rete richiesta durante l'utilizzo, dopo la preparazione.

Il percorso raccomandato è `./build-storycast.sh`. Per una build manuale completa, l'ordine è:

```bash
docker build -f docker/base-tts/Dockerfile -t qwen3-tts-cpu:local .
docker build -f docker/base-renderer/Dockerfile -t voiceover-to-video:local .
docker compose build storycast-controller storycast-tts storycast-renderer
```

Se le immagini base e tutte le dipendenze sono già nella cache Docker, la build può essere forzata offline:

```bash
docker build --network none -t storycast-controller:local .
docker build --network none -f Dockerfile.tts -t storycast-tts:local .
docker build --network none -f Dockerfile.renderer -t storycast-renderer:local .
```

Per verificare configurazione, input e fasi previste senza avviare inferenza o rendering:

```bash
./avvia-storycast.sh piano --input input/MODELLO_DIALOGO.txt --nome esempio --dry-run
```

## Test

I test che non richiedono FFmpeg possono essere eseguiti nel controller, senza installare librerie sull'host:

```bash
docker compose build storycast-controller
docker compose run --rm --entrypoint sh storycast-controller -c \
  'for test in test_audio_qc.py test_cleanup.py test_episode_bundle.py test_instruction_profiles.py test_run_logging.py test_short_pipeline.py test_storycast.py test_tts_safety.py test_work_manager.py; do python -m unittest discover -s tests -p "$test" || exit 1; done'
```

La suite completa, inclusi i test che richiedono FFmpeg, va eseguita nell'immagine renderer:

```bash
docker compose build storycast-renderer
docker compose run --rm --entrypoint python storycast-renderer -m unittest discover -s tests -v
```

Il modello Qwen non è distribuito con Storycast e i test di inferenza reale sono separati perché l'esecuzione CPU può essere molto lenta. Il progetto è calibrato per CPU AVX senza AVX2; qualità e tempi dipendono da modello, testo e hardware.

## Licenza

Il codice è distribuito secondo la [licenza MIT](LICENSE). Il modello Qwen3-TTS non è incluso e mantiene la propria licenza; consultare [MODELLI.md](docs/MODELLI.md).

## Documentazione

- [Uso semplice](docs/USO_SEMPLICE.md)
- [Nuova storia e comandi](docs/NUOVA_STORIA.md)
- [Ripresa dopo interruzione](docs/RIPRESA_DOPO_INTERRUZIONE.md)
- [Modalità revisione](docs/MODALITA_REVISIONE.md)
- [Cancellazione e reset](docs/CANCELLAZIONE_STORIA.md)
- [Formato dialogo](docs/FORMATO_DIALOGO.md)
- [Architettura](docs/ARCHITETTURA.md)
- [Build riproducibile](docs/BUILD_RIPRODUCIBILE.md) e [modello](docs/MODELLI.md)
- [TTS](docs/TTS.md) e [libreria visiva](docs/LIBRERIA_VISIVA.md)

I comandi storici dell'episodio 01, del TTS e del renderer restano disponibili per compatibilità e diagnosi; `./avvia-storycast.sh help` ne mostra l'elenco.
