# Roadmap

## Fase 1 — Fondamenta e parser

Controller Docker leggero, catalogo dinamico dei personaggi, dialogo UTF-8, validazione, JSON strutturato, timeline preliminare, test e comandi operativi.

Stato: completata.

## Fase 2 — TTS multivoce

Servizio CPU-only separato, una voce configurabile per personaggio, generazione sequenziale per segmento, manifest e cache basata su input/configurazione. Nessun modello sarà scaricato implicitamente.

Stato: completata nelle fondamenta, verificata end-to-end in mock e con un test reale minimo Vivian/Ryan. La produzione completa non è stata avviata.

## Fase 3 — Metadati temporali

Misura affidabile della durata WAV, calcolo cumulativo di start/end, gestione delle pause e aggiornamento atomico degli stati per consentire la ripresa.

Stato: base completata per WAV PCM; affinamenti prosodici e validazione su output reale restano futuri.

## Fase 4 — Selezione degli asset visivi

Regole deterministiche per speaker, emozione, indicazione scenica e inquadratura; fallback ai master approvati senza modificarli.

Stato: completata la base configurabile con ritagli wide/medium/closeup, catalogo verificabile, cache selettiva e planner speaker/pause.

## Fase 5 — Rendering FFmpeg

Servizio renderer isolato, composizione audio/video sequenziale, profili CPU adatti all'hardware e ripresa dagli intermedi.

Stato: completato e verificato sul test reale a 1280×720/30 fps, H.264/AAC; restano futuri transizioni e montaggi lunghi.

## Fase 5.1 — Primo episodio breve

Episodio reale multivoce di 57,05 secondi con otto battute, timeline dedicata, 17 scene automatiche, video sincronizzato e CLI ripetibile.

Stato: completata.

## Fase 6 — Inferenza immagini opzionale

Servizio separato e disattivato di default; inferenza CPU esplicita, cache, limiti di memoria e approvazione degli asset generati prima dell'uso.

## Fase 7 — Introduzione del terzo personaggio

Aggiunta di YAML, master e voce del nuovo personaggio; test della pipeline con tre speaker senza modificare parser o schema.

## Fase 8 — Orchestratore multi-storia

Comando unico `genera`, namespace per slug, stato atomico, lock, ripresa selettiva, review opzionale, output completi e report verificabile.

Stato: completata e verificata in mock e con una nuova storia reale di quattro battute.
