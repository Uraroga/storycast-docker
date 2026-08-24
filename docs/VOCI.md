# Configurazione delle voci

`config/voices.yaml` usa sintassi JSON, che è un sottoinsieme valido di YAML e può essere letto senza dipendenze sull'host. La mappa `voices` è dinamica: ogni chiave deve coincidere con `character_id` e con un ID presente in `config/characters/*.yaml`.

I profili sono definiti da `instruction_profiles`; ogni voce espone le formulazioni sotto `instructions`. I campi comuni restano `character_id`, `voice`, `tone`, `pace`, `seed`, `enabled` e `parameters`. `spoken_language` è distinta da `instruction_language` e il testo non viene tradotto.

La configurazione iniziale assegna Vivian a `personaggio_1` e Ryan a `personaggio_2`, con istruzioni e ritmi distinti. Entrambe usano il seed conservativo `9001`, già verificato nel progetto legacy; la voce assegnata mantiene distinti i timbri. Il modello CustomVoice documenta entrambi gli speaker; possono parlare italiano, pur avendo lingue native diverse. La distinguibilità percettiva deve essere valutata dall'utente.

Per aggiungere un personaggio servono soltanto il suo YAML, il master e una nuova voce abilitata. I test verificano questa procedura con un terzo personaggio temporaneo.

Il profilo attivo si consulta con `./avvia-storycast.sh tts-instruction-profile`; sono ammessi soltanto nomi presenti nella configurazione.
