# Test video reale

Sorgenti: timeline `work/timeline/test_reale_timeline.json`, WAV immutato `output/test_reale_storycast_audio.wav` e master di gruppo approvato. Procedura:

```bash
docker build --network none -f Dockerfile.renderer -t storycast-renderer:local .
./avvia-storycast.sh visual-assets
./avvia-storycast.sh visual-plan
./avvia-storycast.sh render-test
./avvia-storycast.sh render-check
```

Il risultato verificato è `output/test_reale_storycast_video.mp4`: MP4, H.264, yuv420p, 1280×720, 30 fps, AAC mono 24 kHz. Audio 9,030 s; video 9,033333 s; differenza 0,003333 s. I frame `inizio.png`, `meta.png` e `fine.png` sono estratti sotto `work/visual/verification_frames/` e controllati per decodifica, dimensione e luminanza non nera. La gradevolezza resta una valutazione percettiva dell'utente.

Il test più lungo dell'episodio 01 è documentato separatamente in `docs/EPISODIO_01.md` e usa cinque istanti di verifica.
