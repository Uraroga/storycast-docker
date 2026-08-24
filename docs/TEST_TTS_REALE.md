# Test TTS reale minimo

## Scopo e isolamento

La prova sintetizza esclusivamente le due battute di `input/test_reale_tts.txt`. Non usa il backend mock, non legge il dialogo principale e non scrive negli output di produzione. Il modello host viene montato read-only in `/models`; il container non ha rete né GPU.

Artefatti:

- WAV: `work/real_test/audio_segments/`;
- metadata: `work/real_test/metadata/audio_segments/`;
- merge: `output/test_reale_storycast_audio.wav`;
- manifest: `work/metadata/test_reale_audio_manifest.json`;
- timeline: `work/timeline/test_reale_timeline.json`.

## Procedura esatta

Dalla directory principale del repository:

```bash
docker build --network none -f Dockerfile.tts -t storycast-tts:local .
./avvia-storycast.sh tts-real-test --dry-run
./avvia-storycast.sh tts-real-test --speaker personaggio_1
./avvia-storycast.sh tts-real-test --speaker personaggio_2
./avvia-storycast.sh tts-real-test-status
./avvia-storycast.sh tts-real-test --speaker personaggio_1
./avvia-storycast.sh tts-real-test --speaker personaggio_2
./avvia-storycast.sh tts-real-test-cache-check
./avvia-storycast.sh tts-real-test-merge
./avvia-storycast.sh tts-real-test-status
./avvia-storycast.sh tts-real-test-clean --dry-run
```

La prima esecuzione per speaker carica Qwen e genera un WAV. La seconda deve mostrare `cached: 1, generated: 0` e non cambiare hash, metadata o mtime. `cache-check` modifica soltanto una copia della configurazione, verifica `personaggio_1=regenerate` e `personaggio_2=valid`, poi ripristina la copia.

La pulizia reale richiede `tts-real-test-clean --yes`; senza `--yes` o con `--dry-run` non elimina nulla e non considera file estranei al test.

## Configurazione applicata

- modello: Qwen3-TTS 1.7B CustomVoice locale;
- Vivian per `personaggio_1`, Ryan per `personaggio_2`;
- lingua `Italian`, seed 9001;
- profilo conservative: temperature 0,65, top-p 0,90, top-k 30, repetition penalty 1,10 e analoghi parametri subtalker;
- CPU: `ATEN_CPU_CAPABILITY=default`, `ONEDNN_MAX_CPU_ISA=AVX`, `DNNL_MAX_CPU_ISA=AVX`, due thread;
- offline: `network_mode: none`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`;
- GPU: `CUDA_VISIBLE_DEVICES` vuoto e `torch.cuda.is_available() == False`.

## Interpretazione

Un risultato valido richiede PCM16 mono a 24 kHz, durata plausibile, RMS/picco non nulli, assenza di silenzio completo, hash coerenti e timeline non sovrapposta. I metadata provano speaker, instruction e seed passati all'API Qwen; non provano la qualità percepita. L'utente deve ascoltare i WAV e valutare timbro, pronuncia, naturalezza e corrispondenza delle voci.

Il warning `flash-attn is not installed` è previsto in CPU-only. Il warning NNPACK `Unsupported hardware` è stato osservato senza crash; un exit code 132 o `Illegal instruction` indica invece `SIGILL` e impone di fermarsi prima dello speaker successivo.
