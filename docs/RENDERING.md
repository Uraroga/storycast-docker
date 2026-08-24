# Rendering

`storycast-renderer` deriva dall'immagine locale `voiceover-to-video:local`; la build usa `docker build --network none`. Non usa GPU, privilegi o rete e termina dopo il comando.

Ogni scena è renderizzata con `zoompan` a 1280×720, 30 fps, H.264/yuv420p. Il concat intermedio è rimappato su timestamp CFR e completato all'ultimo fotogramma; il WAV sorgente resta immutato ed è codificato AAC mono 24 kHz. Il contenitore MP4 usa faststart. Un output precedente viene copiato automaticamente in `output/backups/`.

Comandi: `visual-status`, `visual-check`, `visual-plan`, `visual-assets`, `visual-verify`, `render-test`, `render-status`, `render-check` e `visual-clean --dry-run`. La pulizia reale richiede `--yes` e considera esclusivamente `work/visual`.

Per l'episodio 01 il renderer usa `work/visual/scenes_episode_01/`, preserva gli output precedenti con backup e produce cinque frame di controllo. `episode-01-render --dry-run` stampa il piano FFmpeg senza creare video.
