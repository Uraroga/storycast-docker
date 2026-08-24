# Ripresa dopo interruzione

Ctrl+C durante inferenza lascia solo un `.partial` non riutilizzabile; durante cooldown registra `cooldown_interrupted`. Rilanciando `genera INPUT`, WAV validi con hash e QC v4 sono cache hit senza pausa; partial e sospetti sono ignorati.

Ogni storia ha `work/episodes/<slug>/state.json`. Il file viene scritto in un temporaneo nella stessa directory, sincronizzato con `fsync` e sostituito atomicamente. Registra input e hash, configurazioni, modello, fase, fasi completate, segmenti, output, scene, errori e stato conclusivo.

In caso di spegnimento rilanciare lo stesso comando:

```bash
./avvia-storycast.sh genera input/nome_storia.txt
```

Il sistema ricalcola le identità dei segmenti e conserva WAV con metadata coerenti; verifica gli hash di audio, timeline, piano e output finali; riusa crop e scene firmati. Riparte dal primo artefatto mancante o invalido. Un output finale già verificato non viene riscritto.

Il lock `run.lock` contiene PID, hostname, istante e identità del processo. Un lock attivo blocca una seconda esecuzione dello stesso slug. Un lock proveniente da un processo o container non più esistente viene rinominato `run.lock.stale_*` per audit e non impedisce la ripresa; nessun processo viene terminato.

Se l'hash dell'input è diverso, la ripresa si ferma. Usare un nuovo nome oppure, soltanto intenzionalmente, `--sostituisci`; gli output correnti vengono prima copiati sotto `backups/`.
