# Cache audio

Ogni WAV ha un JSON omonimo sotto `work/metadata/audio_segments/`. Un segmento è riusato soltanto se:

- il WAV è PCM16 mono leggibile e non vuoto;
- `status` è `valid`;
- hash del testo normalizzato, configurazione vocale effettiva e identità modello/backend coincidono;
- SHA-256 corrente del WAV coincide con i metadata.

Testo, seed, istruzione, lingua, voce, parametri, modello, revisione o backend diversi invalidano la cache. Un WAV corrotto o metadata mancanti producono `regenerate`. La scrittura WAV usa un file temporaneo e rename atomico; metadata validi vengono scritti soltanto dopo la verifica.

`tts-regenerate N` forza esclusivamente l'indice richiesto. `tts-generate --from N --to M` limita il lavoro. Un'interruzione lascia riusabili i segmenti già conclusi.

Il merge non sovrascrive silenziosamente un audio completo valido: prima crea una copia timestampata in `output/backups/`. `audio_manifest.json` registra ordine, pause, tempi, durata totale, hash e backup.
