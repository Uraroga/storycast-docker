# Build riproducibile

`./build-storycast.sh` costruisce nell'ordine le basi `qwen3-tts-cpu:local` e `voiceover-to-video:local`, quindi `storycast-controller:local`, `storycast-tts:local` e `storycast-renderer:local`. Non usa `sudo`, non elimina immagini o dati e non scarica il modello. Le immagini base Python sono fissate per digest; i pacchetti Python critici sono fissati nei rispettivi `requirements.txt`.

## Origine ricostruita

La base TTS riproduce l'ambiente collaudato del progetto sorgente locale `qwen3-tts-docker`: Python 3.11.16, `ca-certificates`, FFmpeg, `libsndfile1`, SoX, Torch CPU 2.12.1, torchaudio CPU 2.11.0, qwen-tts 0.1.1 e transformers 4.57.3. Le altre versioni dirette sono in `docker/base-tts/requirements.txt`. Il disallineamento Torch/torchaudio è documentato in `MODELLI.md`.

La base renderer riproduce il Dockerfile e i requirements del progetto sorgente locale `voiceover-to-video`: Python 3.12.14, FFmpeg, font DejaVu, Torch CPU 2.7.1, Pillow 11.3.0, NumPy 2.5.2, accelerate 1.10.1, diffusers 0.35.1, huggingface-hub 0.34.4, safetensors 0.6.2 e transformers 4.56.2. Storycast usa direttamente Pillow e FFmpeg; le altre dipendenze sono conservate per equivalenza con la base già collaudata.

APT viene risolto dai repository Debian associati al digest della base e non è congelato a uno snapshot: i Dockerfile fissano quindi sistema base e dipendenze Python critiche, ma futuri aggiornamenti Debian possono cambiare la revisione di FFmpeg. Nella ricostruzione del 25 agosto 2026 entrambe le basi hanno installato FFmpeg 7.1.5 (`7:7.1.5-0+deb13u1`).

## Rete

La build può accedere alla rete per registry Docker, repository Debian e indici Python. Dopo la preparazione, tutti i servizi Storycast hanno `network_mode: none`; il TTS imposta inoltre `HF_HUB_OFFLINE=1` e `TRANSFORMERS_OFFLINE=1`. Il modello viene solo montato read-only dall'host.
