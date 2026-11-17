# Rendering

`storycast-renderer` deriva da `voiceover-to-video:local`, ricostruita da `docker/base-renderer/Dockerfile` tramite `./build-storycast.sh`. La preparazione può usare la rete per pacchetti pubblici; il container di esecuzione non usa GPU, privilegi o rete e termina dopo il comando.

Il comando universale `genera` esegue anche il montaggio nel container `storycast-tts`: `storycast.orchestrator` richiama i moduli visivi Python e FFmpeg senza avviare il servizio renderer. `storycast-renderer` resta il servizio dei comandi visivi dedicati e diagnostici.

Ogni scena del video principale è renderizzata con `zoompan` a 1280×720, 30 fps, H.264/yuv420p. Il concat intermedio è rimappato su timestamp CFR e completato all'ultimo fotogramma; il WAV sorgente resta immutato ed è codificato AAC mono 24 kHz. Il contenitore MP4 usa faststart. I comandi storici di rendering salvano il precedente output in `output/backups/`; il renderer della libreria usa invece una directory di backup dedicata. Nella pipeline universale questa è `work/episodes/<slug>/backups/`. Lo Short usa 1080×1920 a 30 fps, sottotitoli, velocità finale 1,30× e hold di 2 secondi; il principale conserva anche la variante configurabile a 1,15×.

Comandi: `visual-status`, `visual-check`, `visual-plan`, `visual-assets`, `visual-verify`, `render-test`, `render-status`, `render-check` e `visual-clean --dry-run`. La pulizia reale richiede `--yes` e considera esclusivamente `work/visual`.

Per l'episodio 01 il renderer usa `work/visual/scenes_episode_01/`, preserva gli output precedenti con backup e produce cinque frame di controllo. `episode-01-render --dry-run` stampa il piano FFmpeg senza creare video.
