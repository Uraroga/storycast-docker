# Cancellazione di una storia e reset dei lavori

## Flusso consigliato

1. Generare la storia.
2. Controllare audio, video e segmenti.
3. Copiare il risultato desiderato fuori dal progetto.
4. Esaminare `./avvia-storycast.sh elimina-storia --nome SLUG --dry-run`.
5. Eseguire `./avvia-storycast.sh elimina-storia --nome SLUG --yes`.
6. Controllare `./avvia-storycast.sh spazio-lavori` e iniziare il lavoro successivo.

La cancellazione usa `state.json` e la struttura multi-storia per individuare output, WAV, metadata, scene, cache e file collegati. Gli input principale e Short non sono mai bersagli. Se lo stato non esiste, usa soltanto percorsi confinati e riconoscibili. Lo slug deve essere sicuro. Senza `--yes` nulla viene eliminato.

## Reset completo

```bash
./avvia-storycast.sh azzera-lavori --dry-run
./avvia-storycast.sh azzera-lavori --yes
```

Il reset elimina tutto il contenuto runtime esclusivamente sotto `output/`, `work/` e `logs/`. Include episodi legacy, demo, test TTS/video, confronto A/B, WAV, metadata, stati, revisioni, timeline, manifest, scene, fotogrammi, cache, crop derivati, lock obsoleti, log e backup runtime. Include inoltre audio, timeline, manifest, MP4, SRT, master naturale, ASS, piani verticali e scene della pipeline Short. Non crea backup, archivi o manifest permanenti della cancellazione.

Restano tutti gli input `.txt` e `-short.txt`, prompt, codice, script, configurazioni, documentazione, test, Dockerfile, asset originali/approved e modello TTS esterno. Vengono mantenute o ricreate le directory vuote `output/`, `work/`, `work/episodes/` e `logs/`.

## Protezioni

- rifiuto di path traversal e slug non validi;
- risoluzione confinata alla root del progetto;
- protezione esplicita della root, di codice, configurazioni, documenti e asset;
- rifiuto di symlink runtime diretti fuori dal progetto;
- rifiuto in presenza di container Storycast o generazioni con lock/PID attivo;
- nessun processo viene terminato automaticamente;
- eliminazione elemento per elemento dopo un inventario completo, senza `rm -rf`.

`./avvia-storycast.sh spazio-lavori` mostra numero di storie e byte occupati da output, WAV, cache, temporanei e totale eliminabile senza modificare file.
