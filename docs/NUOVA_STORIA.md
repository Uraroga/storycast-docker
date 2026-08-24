# Generare una nuova storia

## Profilo delle istruzioni vocali

Alla creazione `state.json` registra il profilo attivo. Un cambio globale successivo non cambia una storia già iniziata. Le storie anteriori allo schema continuano come `italian_legacy`. Vedere `docs/ISTRUZIONI_VOCALI.md`.

## Fine del lavoro

Dopo aver salvato il risultato fuori dal progetto, usare `elimina-storia --nome SLUG --dry-run` e poi `--yes`. Per eliminare tutte le storie, prove, cache e output runtime usare `azzera-lavori`; nessuno dei due comandi crea archivi o backup. Il bundle neutro `input/MODELLO_DIALOGO.txt` e `input/MODELLO_DIALOGO-short.txt` resta disponibile.

## Pipeline

`genera` orchestra in sequenza validazione, parsing, mapping delle voci, piano e inferenza TTS, QC tecnico, merge, manifest, timeline reale, libreria visiva, piano scene, rendering e verifica finale. Riusa i moduli Storycast esistenti e carica il modello una sola volta per il gruppo di segmenti mancanti.

Prima dell'avvio devono esistere entrambi `input/nuova_storia.txt` e `input/nuova_storia-short.txt`. Lo Short associato è obbligatorio, usa lo stesso nome con suffisso `-short` e deve contenere entrambi i personaggi configurati. I file `MODELLO_DIALOGO*` inclusi nel repository sono un esempio neutro eseguibile.

```bash
./avvia-storycast.sh genera input/nuova_storia.txt
./avvia-storycast.sh genera --input input/nuova_storia.txt --nome nuova_storia
```

Gli input assoluti e quelli fuori da `input/` sono rifiutati.

## Struttura

Ogni storia usa:

```text
work/episodes/<slug>/
├── source/
├── dialogue/
├── audio_segments/
├── metadata/
├── timeline/
├── visual/
├── scenes/
├── backups/
├── logs/
└── state.json
```

Gli output sono `<slug>_audio.wav`, `<slug>_video.mp4`, manifest audio, timeline, piano visivo e report JSON sotto `output/<slug>/`.

## Comandi di gestione

```bash
./avvia-storycast.sh stato --nome SLUG
./avvia-storycast.sh segmenti --nome SLUG
./avvia-storycast.sh ascolta --nome SLUG --indice INDEX
./avvia-storycast.sh approva --nome SLUG --indice INDEX
./avvia-storycast.sh rifiuta --nome SLUG --indice INDEX
./avvia-storycast.sh rigenera --nome SLUG --indice INDEX --alternate-seed
./avvia-storycast.sh riprendi --nome SLUG
./avvia-storycast.sh verifica --nome SLUG
./avvia-storycast.sh pulisci --nome SLUG --dry-run
```

La rigenerazione reale richiede conferma, salva WAV e metadata precedenti e invalida la revisione del solo segmento. Non esegue automaticamente molti tentativi.

La pulizia classifica file essenziali, WAV, metadata, output, cache, scene, fotogrammi e backup. Senza `--yes` non cancella nulla e resta confinata allo slug.
