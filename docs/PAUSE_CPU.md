# Pause CPU prudenziali

Il profilo `conservative`, predefinito per l’Intel i3-3240 CPU-only, attende 30 secondi dopo ogni inferenza TTS reale. Prima dell’attesa Storycast chiude e sincronizza il temporaneo, esegue il QC e aggiorna atomicamente `state.json`. L’attesa usa `time.sleep`: è passiva, non usa busy waiting e non mantiene inferenze attive.

Se inferenza, scrittura o QC falliscono, ai 30 secondi ordinari seguono altri 60 secondi prima dell’unico retry con seed alternativo. `max_parallel_inferences` è 1 e `retry_limit` è 1. Vale anche fra sotto-segmenti reali; non vale per cache hit verificate, scansioni, parsing, dry-run, mock o rendering.

Usare `cpu-cooldown-status`, `cpu-cooldown-check` e `cpu-cooldown-plan --nome SLUG`. Durante l’attesa lo stato espone motivo, inizio, durata, azione successiva, indice e tentativo. Ctrl+C registra `cooldown_interrupted`; alla ripresa nessun `.partial` può essere cache hit.

Il costo minimo è 30 secondi per nuova inferenza e altri 60 per un errore.
