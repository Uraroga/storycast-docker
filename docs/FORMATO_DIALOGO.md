# Formato del dialogo

Ogni file sotto `input/` deve essere UTF-8. La pipeline universale richiede la coppia `input/<nome>.txt` e `input/<nome>-short.txt`; ogni battuta comincia con un'intestazione fra parentesi quadre e prosegue fino alla successiva intestazione.

```text
[personaggio_1|curiosa|scena=sorride al microfono|pausa=0.4]
Benvenuti a Storycast.

[personaggio_2|riflessivo]
Cominciamo.
```

La forma è `[speaker|emozione|opzione=valore]`. Solo `speaker` è obbligatorio. L'emozione è un valore libero senza `=`. Le opzioni supportate sono `scena` (alias `stage`) e `pausa` in secondi (alias `pause`), con punto o virgola decimale. Il testo può occupare più righe; righe vuote interne vengono conservate e gli spazi esterni eliminati. Prima della prima battuta sono ammessi righe vuote e commenti che iniziano con `#`.

Gli speaker validi provengono da `config/characters/*.yaml`; non esiste un limite a due personaggi. Sono errori bloccanti intestazioni malformate, opzioni sconosciute o duplicate, testo senza intestazione, battute vuote, pause negative, speaker non configurati e input non UTF-8.

Il JSON del parser contiene per ogni battuta: `index`, `speaker`, `text`, `emotion`, `stage_direction`, `pause`, `audio_file` e `status`. I valori opzionali assenti sono `null`.
