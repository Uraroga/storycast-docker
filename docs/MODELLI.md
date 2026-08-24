# Modelli TTS

## Scelta reale prevista

- Modello: `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Fonte documentata dal materiale locale: repository Hugging Face/ModelScope di Qwen; nessun download viene effettuato da Storycast.
- Licenza dichiarata nel README locale: Apache-2.0.
- Dimensione locale completa osservata: 4.520.222.349 byte (circa 4,21 GiB, incluse le sottodirectory del tokenizer/codec); il solo `model.safetensors` è 3.833.402.552 byte. L'immagine runtime Qwen esistente è circa 3,29 GB secondo Docker.
- Percorso host consigliato: una directory locale contenente `Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Percorso container: `/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Volume: `${STORYCAST_MODELS_HOST:-./models}:/models:ro`.

Il modello è già presente localmente ed è quindi riusato, non copiato dentro Storycast né nell'immagine. `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` e `network_mode: none` impediscono download impliciti. Se il modello manca, `tts-check` e la generazione reale terminano con istruzioni chiare; la modalità mock continua a funzionare.

## Requisiti e limiti

Il backend richiede l'immagine locale `qwen3-tts-cpu:local`, Torch CPU, qwen-tts, NumPy e il modello. Il container forza `CUDA_VISIBLE_DEVICES` vuoto, `ATEN_CPU_CAPABILITY=default`, `ONEDNN_MAX_CPU_ISA=AVX` e `DNNL_MAX_CPU_ISA=AVX`. Usa al massimo 2 CPU/8 GB e carica un solo modello per run.

Sull'i3-3240 senza AVX2/FMA l'inferenza 1.7B è lenta. Il test reale minimo del 1 agosto 2026 ha caricato e generato due segmenti senza `SIGILL`, con circa 13 secondi di caricamento e 68–81 secondi di inferenza per 3,84–4,64 secondi di audio. Sono rimasti warning NNPACK non bloccanti. Questo prova la compatibilità del percorso testato, non garantisce che ogni testo o futura versione delle librerie sia immune da errori ISA.

Per UID non privilegiato l'immagine Storycast crea l'utente `storycast` e imposta `USER`, `LOGNAME`, `XDG_CACHE_HOME`, `HF_HOME` e `TORCHINDUCTOR_CACHE_DIR` sotto `/tmp`. Senza questa correzione Torch 2.12 falliva durante l'import con `getpwuid(): uid not found`.

## Alternativa consigliata più leggera

`Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` è l'alternativa naturale, stessa famiglia e licenza indicata, con memoria e calcolo inferiori. Non è installata e non verrà scaricata automaticamente. Prima di adottarla vanno documentati dimensione effettiva, compatibilità delle voci e qualità italiana.

Il 1.7B locale è il modello realmente configurato; il 0.6B è quello consigliato da valutare per questo hardware. Nessuna inferenza reale è necessaria per test parser, cache, merge o timeline.
