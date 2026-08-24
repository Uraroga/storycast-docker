# Asset visivi

La sorgente reale è la libreria dinamica descritta in [LIBRERIA_VISIVA.md](LIBRERIA_VISIVA.md): `assets/characters/<id>/` per le immagini singole e `assets/groups/` per quelle di gruppo. La scansione è ricorsiva e accetta PNG, JPG e JPEG senza distinzione tra maiuscole e minuscole.

Tutte le immagini sorgente sono read-only nei container. Le eventuali sottocartelle `approved` e `archive` sono escluse dal catalogo: possono contenere riferimenti editoriali storici, ma non sono richieste e non alimentano pose o rendering. I campi `immagine_principale` sono anch'essi riferimenti opzionali.

Il manifest dinamico conserva sorgente, personaggio, funzione, posa, risoluzione, hash, crop, cache key e derivato. Le immagini trasformate sono 1280×720 sotto `work/`; il crop centrale conserva il rapporto 16:9 senza deformazione. La cache viene invalidata quando cambiano contenuto, crop, trasformazione o risoluzione.

Usare `./avvia-storycast.sh visual-library-check` per vedere ciò che il container scandisce e `visual-library-plan --dry-run` per ispezionare la selezione senza creare output.
