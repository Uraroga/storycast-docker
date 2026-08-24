# TTS multivoce

Il backend è sequenziale (`max_parallel_inferences: 1`). Ogni inferenza reale produce un `.partial`, supera il QC v4 e solo allora viene promossa; seguono 30 secondi di cooldown. Cache hit e dry-run non attendono. In errore seguono altri 60 secondi prima del solo seed alternativo ammesso.

## Metadata episodio 01 e revisione

I metadata audio schema 3 distinguono tre identità deterministiche:

- `voice_config_hash`: voce, lingua, istruzione effettiva e parametri, senza seed;
- `generation_config_hash`: aggiunge modello, backend e provenienza del seed effettivo;
- `effective_generation_hash`: aggiunge l'hash del testo esatto.

`seed_origin` registra `default_seed`, `effective_seed` e modalità `default` o
`alternate`. Un seed alternativo non modifica il seed predefinito della voce.

```bash
./avvia-storycast.sh episode-01-migrate-metadata --dry-run
./avvia-storycast.sh episode-01-migrate-metadata
./avvia-storycast.sh episode-01-audio-qc
./avvia-storycast.sh episode-01-qc-status
./avvia-storycast.sh episode-01-review-status
```

`qc_state` (`passed`, `warning`, `failed`) è tecnico. `review_state`
(`pending_review`, `approved`, `rejected`) è umano: l'approvazione vale soltanto
per il relativo `reviewed_wav_hash` e una scansione non la rimuove se il WAV è
invariato.

## Flusso operativo

`storycast-tts` legge il dialogo, il catalogo personaggi, `config/voices.yaml` e `config/tts.json`. Per ogni battuta crea un piano con voce, istruzione effettiva, seed, sotto-segmenti e hash. La generazione è sequenziale e il modello reale viene caricato una sola volta per comando, non per battuta.

```bash
./avvia-storycast.sh tts-status
./avvia-storycast.sh tts-check
./avvia-storycast.sh tts-plan
./avvia-storycast.sh tts-generate --dry-run
./avvia-storycast.sh tts-generate --from 10 --to 20
./avvia-storycast.sh tts-regenerate 12
./avvia-storycast.sh tts-verify
./avvia-storycast.sh audio-merge
./avvia-storycast.sh audio-status
```

Per la modalità sintetica, aggiungere `--mock` a ogni comando che valuta la cache: `tts-check`, `tts-plan`, `tts-generate`, `tts-regenerate`, `tts-verify`, `audio-merge` e `audio-status`. Il backend fa parte dell'identità del modello, quindi un WAV mock non viene mai riusato come WAV reale.

## Testo e segmentazione

Il testo resta UTF-8 e non viene tradotto. Apostrofi, accenti, virgolette, numeri, sigle e punteggiatura sono conservati. Gli spazi vengono normalizzati. Il chunker privilegia frasi concluse da `. ! ? ;`, poi virgole e infine un taglio per parole che evita alcune parole funzionali sul confine. Tutti i sotto-segmenti restano elencati nei metadata della battuta originale.

## Seed e controlli

Ogni personaggio ha seed stabile. Il backend reale ripristina seed Python, NumPy e Torch prima di ogni sotto-segmento e usa parametri espliciti. La verifica rifiuta WAV corrotti, non PCM16 mono, vuoti, con hash differente o con durata/silenzio fuori soglia. Questi controlli individuano anomalie tecniche e outlier; non sono ASR e non possono dimostrare da soli l'assenza di parole inventate. L'ascolto/ASR controllato resta necessario prima della produzione.

Ogni esecuzione di `genera` usa un log append-only dedicato in `logs/<episodio>_AAAAMMGG_HHMMSS.log`; il riepilogo TTS riporta segmenti generati, riutilizzati dalla cache e falliti senza copiare testo o output tecnico. `logs/latest.log` indica l'esecuzione più recente. In caso d'interruzione, rieseguire lo stesso comando: la cache salta solo segmenti completamente coerenti.

## Test reale minimo

Il test reale usa `input/test_reale_tts.txt`, `work/real_test/`, `output/test_reale_storycast_audio.wav`, `work/metadata/test_reale_audio_manifest.json` e `work/timeline/test_reale_timeline.json`. Impone `--speaker` per impedire che le due inferenze partano nello stesso comando. Tempi di caricamento/inferenza, rapporto real-time, RMS, picco, silenzio, clipping e warning Python sono salvati nei metadata.

La prova del 1 agosto 2026 è riuscita con Vivian e Ryan su i3-3240 senza AVX2/FMA. Sono comparsi warning NNPACK `Unsupported hardware`, ma nessun `SIGILL`; Qwen ha usato il percorso manuale PyTorch perché `flash-attn` non è installato. Vedere [TEST_TTS_REALE.md](TEST_TTS_REALE.md).

## Lingua parlata e istruzioni

Il backend invoca `generate_custom_voice(text=..., language=..., speaker=..., instruct=...)`. Il testo non contiene mai l'istruzione. Per le nuove storie `language`/`spoken_language` resta `Italian`; `instruct` deriva dal profilo configurato. Profilo, lingue, emozione originale e valore configurato sono salvati nei metadata e partecipano agli hash di cache.
