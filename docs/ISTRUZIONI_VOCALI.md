# Istruzioni vocali: inglese e parlato italiano

Storycast separa `spoken_language: Italian`, lingua pronunciata, da `instruction_language`, lingua delle indicazioni di tono, ritmo, timbro ed espressività.

Qwen3-TTS riceve il dialogo nel campo `text`, `Italian` nel campo `language` e lo stile nel campo `instruct`. L'istruzione non viene concatenata al testo. `english_default` è il profilo predefinito; `italian_legacy` conserva le formulazioni precedenti. La valutazione percettiva resta all'utente.

## Profili e cache

```bash
./avvia-storycast.sh tts-instruction-status
./avvia-storycast.sh tts-instruction-profile
./avvia-storycast.sh tts-instruction-profile english_default
./avvia-storycast.sh tts-instruction-profile italian_legacy
```

Sono accettati soltanto profili dichiarati in `config/voices.yaml`. Il cambio è scritto atomicamente in `work/settings/tts_instruction_profile.json`. Testo, voce, modello, seed, parametri, lingua parlata, profilo e istruzione effettiva partecipano alla cache. Ogni storia registra il proprio profilo e lo riusa alla ripresa; quelle anteriori allo schema restano legacy senza riscrittura. `--refresh-voice-instructions` non altera WAV esistenti e richiede un nuovo slug se il profilo differisce.

## Emozioni

`emotion_original` conserva il valore del dialogo. Solo i valori presenti nel mapping configurabile vengono tradotti per l'istruzione inglese. Un valore sconosciuto usa l'indicazione neutra configurata, senza traduzioni arbitrarie.

## Confronto A/B

```bash
./avvia-storycast.sh tts-instruction-ab-test --dry-run
./avvia-storycast.sh tts-instruction-ab-test
./avvia-storycast.sh tts-instruction-ab-test-status
```

Ascolto locale:

```bash
ffplay -nodisp -autoexit work/instruction_ab_test/vivian_instruction_it.wav
ffplay -nodisp -autoexit work/instruction_ab_test/vivian_instruction_en.wav
ffplay -nodisp -autoexit work/instruction_ab_test/ryan_instruction_it.wav
ffplay -nodisp -autoexit work/instruction_ab_test/ryan_instruction_en.wav
```

Il manifest tecnico è `work/instruction_ab_test/comparison_manifest.json`.
