# Modalità revisione audio

Un rigenerato può avere `qc_state: valid`, ma resta `review_state: pending` fino all’ascolto. Il QC tecnico non è ASR. Usare `ascolta`, poi `approva`; un sospetto non entra nel merge.

Per ascoltare il parlato prima del montaggio:

```bash
./avvia-storycast.sh genera input/nome_storia.txt --review-audio
```

La pipeline genera i WAV, esegue il QC tecnico e si ferma con stato `awaiting_review`. Non crea ancora audio completo o video.

```bash
./avvia-storycast.sh segmenti --nome nome_storia
./avvia-storycast.sh ascolta --nome nome_storia --indice 1
./avvia-storycast.sh approva --nome nome_storia --indice 1
./avvia-storycast.sh rifiuta --nome nome_storia --indice 2
```

L'approvazione è legata allo SHA-256 del WAV. Se quel file cambia torna `pending_review`. QC e revisione sono distinti: `qc_state` descrive soltanto la qualità tecnica, `review_state` la decisione dell'utente.

Dopo avere approvato tutti i segmenti rilanciare lo stesso comando o:

```bash
./avvia-storycast.sh riprendi --nome nome_storia
```

Il QC non riconosce semanticamente parole, lingua o identità della voce; l'ascolto rimane necessario quando tale garanzia è richiesta.
