# Libreria visiva dinamica

Storycast scopre ricorsivamente le immagini in `assets/characters/<speaker>/` e in `assets/groups/`. Sono accettate estensioni PNG, JPG e JPEG (senza distinzione tra maiuscole e minuscole). Il nome della sottocartella è lo speaker: aggiungere `assets/characters/personaggio_3/` rende quindi disponibile un terzo personaggio senza modificare Python. Le cartelle `approved` e `archive`, riservate a riferimenti editoriali o asset storici, sono escluse dalla configurazione corrente.

I campi `immagine_principale` delle schede in `config/characters/` sono riferimenti editoriali opzionali: non sono master richiesti per creare una storia e non vengono usati come pose. Se il file indicato manca, parser, TTS e pipeline dinamica continuano normalmente. Non occorre creare copie manuali della posa `talking`.

`visual-library-check` e `genera` importano lo stesso loader dal sorgente montato read-only in `/app/storycast`. Il risultato contiene `diagnostics`, con root effettiva nel container, directory scandite, esistenza e leggibilità, file trovati/accettati/scartati e motivi degli scarti. Un file immagine corrotto genera un avviso ed è ignorato se rimangono alternative valide.

## Classificazione dei nomi

I nomi contenenti `talking`, `listening`, `thinking`, `relaxed` o `on_table` diventano rispettivamente pose di parlato, ascolto, riflessione, rilassamento o varianti occasionali. `leaning_forward`, `open_hand`, `knee_up`, `neutral` e `brightroom` sono conservati come tag. Per i gruppi, `intro`, `outro`, `conversation` e `duo`/`neutral` guidano apertura, chiusura e passaggi. Un nome sconosciuto resta valido come posa `generic`.

La scoperta registra risoluzione e SHA-256. File illeggibili, formati estranei e duplicati per contenuto vengono ignorati e segnalati in `warnings`; la pipeline si ferma soltanto se manca una cartella richiesta, un personaggio non ha alcuna immagine valida o non esiste alcuna immagine di gruppo.

## Regia e formato

Durante ogni battuta vengono privilegiate le pose `talking` dello speaker. La scelta bilancia l'uso delle varianti e impedisce ripetizioni consecutive quando esiste un'alternativa. Le pause non brevi mostrano una posa di ascolto/riflessione dell'altro personaggio, con fallback sicuro al gruppo. Le pose sul tavolo hanno priorità bassa e un limite implicito di frequenza. Una battuta lunga viene divisa al massimo nel numero configurato da `max_pose_changes_per_speech`, rispettando `minimum_scene_seconds` e `long_pose_seconds`.

La sequenza è deterministica con `library_planner.seed` in `config/render.yaml`. Inizio e fine scelgono semanticamente `intro` e `outro`, poi `neutral`/`conversation` come fallback. Ogni confine viene quantizzato ai 30 fps e l'ultima scena termina sulla durata audio reale: niente buchi o sovrapposizioni.

Gli originali non vengono modificati. Le derivate cache in `work/visual/library_v1/derived/` sono create a 1280x720 mediante crop centrale 16:9 e scala Lanczos, senza deformazione. Immagini già 16:9 perdono al massimo la riga eccedente necessaria a ottenere dimensioni pari.

## Aggiungere immagini e usare i comandi

Copiare un nuovo file nella cartella del personaggio o dei gruppi; non serve aggiornare manifest o hash. Un nome semanticamente descrittivo migliora la scelta, ma non è obbligatorio.

```bash
./avvia-storycast.sh visual-library-check
./avvia-storycast.sh visual-library-build --dry-run
./avvia-storycast.sh visual-library-plan --dry-run
./avvia-storycast.sh episode-01-render-library --dry-run
./avvia-storycast.sh episode-01-render-library
```

Per una nuova storia completa usare `./avvia-storycast.sh genera input/nome_storia.txt --nome nome_storia`. Tutto il lavoro avviene offline nei container; cache, manifest, piano e possibilità di ripresa restano attivi.
