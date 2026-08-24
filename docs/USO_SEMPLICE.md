# Uso semplice

Controllare senza modello con `verifica-segmenti --nome SLUG`. Prima di riparare usare `ripara-sospetti --nome SLUG --dry-run`, poi selezionare il solo indice. Le inferenze reali attendono 30 secondi; le cache valide no.

1. Crea `input/nome_storia.txt` e il relativo `input/nome_storia-short.txt`.
2. Inserisci in entrambi almeno una battuta per ciascun personaggio configurato, nel formato documentato.
3. Esegui `./avvia-storycast.sh genera input/nome_storia.txt`.
4. Apri `output/nome_storia/nome_storia_audio.wav` e `output/nome_storia/nome_storia_video.mp4`.

Il nome sicuro della storia, o slug, deriva dal nome del file. Spazi e lettere accentate vengono normalizzati quando il nome è automatico. Con `--nome` sono accettati soltanto lettere minuscole, numeri, `_` e `-`.

Prima di caricare Qwen il sistema verifica UTF-8, sintassi, speaker, voci, modello, asset, spazio libero, scrivibilità e lock. Il comando non scarica nulla e non usa la rete.

Per vedere tutto ciò che verrebbe fatto senza caricare il modello:

```bash
./avvia-storycast.sh piano --input input/nome_storia.txt --nome nome_storia --dry-run
```

Le nuove storie usano istruzioni vocali inglesi e parlato italiano. Il formato del dialogo non cambia. Verificare il profilo con `./avvia-storycast.sh tts-instruction-status`.

Quando audio e video sono stati controllati e copiati altrove:

```bash
./avvia-storycast.sh elimina-storia --nome nome_storia --dry-run
./avvia-storycast.sh elimina-storia --nome nome_storia --yes
```

La seconda operazione è definitiva e non crea backup.
