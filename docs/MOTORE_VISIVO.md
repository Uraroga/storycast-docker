# Motore visivo

Il motore legge `config/visual_assets.yaml`, senza identificativi codificati nel Python. Ogni personaggio dichiara speaker, sorgente, area di scena, ritagli normalizzati medium/closeup, lato, inquadrature, asset di ascolto, abilitazione e priorità. Un terzo personaggio richiede soltanto configurazione e asset.

Il planner legge `work/timeline/test_reale_timeline.json`: apre e chiude sul gruppo, assegna lo speaker corretto durante il parlato e il wide nelle pause. I confini sono quantizzati a 30 fps, contigui e senza sovrapposizioni. Apertura, chiusura, durata minima, soglia battuta lunga e seed sono in `config/render.yaml`.

I movimenti deterministici disponibili sono statico, zoom lento avanti/indietro e pan orizzontale. Lo zoom massimo è 1,045 e conserva proporzioni e riempimento. La generazione AI di nuove pose non è integrata e appartiene a un goal successivo.
