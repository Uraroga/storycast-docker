# Segmenti TTS troncati

Il QC audio v4 misura contenitore completo, durata totale e plausibile rispetto a parole/caratteri/punteggiatura, audio attivo, RMS, picco, silenzio iniziale/finale e coda energetica. Un finale energico è sospetto solo assieme a durata chiaramente breve e assenza di coda naturale. I dati validi della stessa voce consentono calibrazioni separate.

Gli stati sono `valid`, `suspicious_too_short`, `suspicious_mostly_silent`, `suspicious_hard_cut`, `suspicious_incomplete_write`, `partial`, `rejected` e `awaiting_human_review`. `qc_state` e `review_state` sono separati. Senza ASR il QC non certifica tutte le parole: serve ascolto umano.

Ogni inferenza scrive `*.partial`, sincronizza, verifica, calcola l’hash e completa i metadata. Solo dopo il QC WAV e metadata sono promossi. Partial, sospetti, hash incoerenti o vecchie versioni QC non entrano in cache o merge.

```bash
./avvia-storycast.sh diagnostica-segmento --nome SLUG --indice 11
./avvia-storycast.sh verifica-segmenti --nome SLUG
./avvia-storycast.sh segmenti-sospetti --nome SLUG
./avvia-storycast.sh ripara-sospetti --nome SLUG --dry-run
./avvia-storycast.sh ripara-segmento --nome SLUG --indice 11 --alternate-seed --yes
./avvia-storycast.sh ascolta --nome SLUG --indice 11
./avvia-storycast.sh approva --nome SLUG --indice 11
```

Il tentativo rifiutato resta in diagnostica/backup. Il retry conserva testo, voce, italiano e istruzione, usa un seed alternativo deterministico e non viene ripetuto.
