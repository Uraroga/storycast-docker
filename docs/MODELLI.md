# Modelli TTS

## Scelta reale prevista

- Modello: `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Fonte ufficiale: `https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Licenza dichiarata nel README locale: Apache-2.0.
- Dimensione locale completa osservata: 4.520.222.349 byte (circa 4,21 GiB, incluse le sottodirectory del tokenizer/codec); il solo `model.safetensors` è 3.833.402.552 byte.
- Percorso host consigliato: una directory locale contenente `Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Percorso container: `/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Volume: `${STORYCAST_MODELS_HOST:-./models}:/models:ro`.

Il modello non è incluso in Git né copiato nelle immagini. Il download è un passo esplicito:

```bash
./download-model.sh
```

Lo script usa un container Docker e la fonte ufficiale, senza installare librerie sull'host e senza token per i file pubblici. In alternativa impostare `STORYCAST_MODELS_HOST` a una directory esterna che contenga la cartella del modello:

```bash
export STORYCAST_MODELS_HOST=/dati/modelli
test -s "$STORYCAST_MODELS_HOST/Qwen3-TTS-12Hz-1.7B-CustomVoice/config.json"
test -s "$STORYCAST_MODELS_HOST/Qwen3-TTS-12Hz-1.7B-CustomVoice/model.safetensors"
test -s "$STORYCAST_MODELS_HOST/Qwen3-TTS-12Hz-1.7B-CustomVoice/speech_tokenizer/config.json"
test -s "$STORYCAST_MODELS_HOST/Qwen3-TTS-12Hz-1.7B-CustomVoice/speech_tokenizer/model.safetensors"
```

`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` e `network_mode: none` impediscono download impliciti durante l'utilizzo. Se il modello manca, il precheck TTS e la generazione reale terminano con istruzioni chiare; la modalità mock continua a funzionare.

## Requisiti e limiti

Il backend usa `qwen3-tts-cpu:local`, ora ricostruibile con `docker/base-tts/Dockerfile`: Python 3.11, Torch 2.12.1+cpu, torchaudio 2.11.0+cpu, qwen-tts 0.1.1, transformers 4.57.3 e NumPy 2.4.6. La diversa versione minore di torchaudio è intenzionale: l'indice CPU ufficiale non pubblica 2.12.1, mentre 2.11.0 importa ed espone l'API usata da qwen-tts nell'ambiente collaudato. Il container forza `CUDA_VISIBLE_DEVICES` vuoto, `ATEN_CPU_CAPABILITY=default`, `ONEDNN_MAX_CPU_ISA=AVX` e `DNNL_MAX_CPU_ISA=AVX`. Usa al massimo 2 CPU/8 GB e carica un solo modello per run.

Sull'i3-3240 senza AVX2/FMA l'inferenza 1.7B è lenta. Il test reale minimo del 1 agosto 2026 ha caricato e generato due segmenti senza `SIGILL`, con circa 13 secondi di caricamento e 68–81 secondi di inferenza per 3,84–4,64 secondi di audio. Sono rimasti warning NNPACK non bloccanti. Questo prova la compatibilità del percorso testato, non garantisce che ogni testo o futura versione delle librerie sia immune da errori ISA.

Per UID non privilegiato l'immagine Storycast crea l'utente `storycast` e imposta `USER`, `LOGNAME`, `XDG_CACHE_HOME`, `HF_HOME` e `TORCHINDUCTOR_CACHE_DIR` sotto `/tmp`. Senza questa correzione Torch 2.12 falliva durante l'import con `getpwuid(): uid not found`.

## Alternativa consigliata più leggera

`Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` è l'alternativa naturale, stessa famiglia e licenza indicata, con memoria e calcolo inferiori. Non è installata e non verrà scaricata automaticamente. Prima di adottarla vanno documentati dimensione effettiva, compatibilità delle voci e qualità italiana.

Il 1.7B locale è il modello realmente configurato; il 0.6B è quello consigliato da valutare per questo hardware. Nessuna inferenza reale è necessaria per test parser, cache, merge o timeline.
