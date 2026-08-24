import tempfile
import unittest
import json
import copy
from pathlib import Path

from storycast.core import StorycastError, load_characters, make_timeline, parse_dialogue
from storycast.tts import (
    build_plan, cache_status, canonical_hash, generate, load_tts_config, load_voices,
    merge_audio, split_text, verify_plan, wav_info,
)


class StorycastTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config/characters").mkdir(parents=True)
        (self.root / "assets/characters/alfa/approved").mkdir(parents=True)
        (self.root / "assets/characters/beta/approved").mkdir(parents=True)
        (self.root / "assets/characters/alfa/approved/master.png").write_bytes(b"png")
        (self.root / "assets/characters/beta/approved/master.png").write_bytes(b"png")
        (self.root / "config/characters/alfa.yaml").write_text(
            'id: alfa\nimmagine_principale: "assets/characters/alfa/approved/master.png"\n', encoding="utf-8"
        )
        (self.root / "config/characters/beta.yaml").write_text(
            'id: beta\nimmagine_principale: "assets/characters/beta/approved/master.png"\n', encoding="utf-8"
        )
        self.characters = load_characters(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def dialogue(self, content: str, binary: bool = False) -> Path:
        path = self.root / "dialogo.txt"
        path.write_bytes(content if binary else content.encode("utf-8"))
        return path

    def test_parser_supporta_metadati_unicode_e_due_personaggi(self):
        path = self.dialogue("[alfa|felice|scena=saluta|pausa=1,5]\nCaffè!\n\n[beta]\nCiao.\n")
        entries = parse_dialogue(path, self.characters)
        self.assertEqual([e["speaker"] for e in entries], ["alfa", "beta"])
        self.assertEqual(entries[0]["pause"], 1.5)
        self.assertEqual(entries[0]["stage_direction"], "saluta")
        self.assertEqual(entries[1]["audio_file"], "work/audio_segments/0002_beta.wav")
        self.assertEqual(entries[1]["status"], "pending")

    def test_personaggio_inesistente(self):
        with self.assertRaisesRegex(StorycastError, "Personaggio inesistente"):
            parse_dialogue(self.dialogue("[gamma]\nTesto\n"), self.characters)

    def test_battuta_vuota(self):
        with self.assertRaisesRegex(StorycastError, "Battuta vuota"):
            parse_dialogue(self.dialogue("[alfa]\n\n[beta]\nTesto\n"), self.characters)

    def test_sintassi_errata(self):
        with self.assertRaisesRegex(StorycastError, "Sintassi intestazione errata"):
            parse_dialogue(self.dialogue("[alfa\nTesto\n"), self.characters)

    def test_codifica_non_utf8(self):
        path = self.root / "dialogo.txt"
        path.write_bytes(b"[alfa]\n\xff\n")
        with self.assertRaisesRegex(StorycastError, "Codifica non UTF-8"):
            parse_dialogue(path, self.characters)

    def test_configurazione_mancante(self):
        for path in (self.root / "config/characters").glob("*.yaml"):
            path.unlink()
        with self.assertRaisesRegex(StorycastError, "Nessuna configurazione YAML"):
            load_characters(self.root)

    def test_immagine_master_mancante_e_riferimento_opzionale(self):
        (self.root / "assets/characters/alfa/approved/master.png").unlink()
        characters = load_characters(self.root)
        self.assertIsNone(characters["alfa"].master_image)
        entries = parse_dialogue(self.dialogue("[alfa]\nTesto.\n"), characters)
        self.assertIsNone(make_timeline(entries, characters, self.root)[0]["visual_asset"])

    def test_identificatore_duplicato(self):
        (self.root / "config/characters/duplicato.yaml").write_text(
            'id: alfa\nimmagine_principale: "assets/characters/alfa/approved/master.png"\n', encoding="utf-8"
        )
        with self.assertRaisesRegex(StorycastError, "Identificatore duplicato"):
            load_characters(self.root)

    def test_timeline_preliminare(self):
        entries = parse_dialogue(self.dialogue("[alfa|calma]\nTesto.\n"), self.characters)
        timeline = make_timeline(entries, self.characters, self.root)
        item = timeline[0]
        self.assertIsNone(item["start"])
        self.assertIsNone(item["end"])
        self.assertIsNone(item["duration"])
        self.assertEqual(item["shot_type"], "medium_closeup")
        self.assertEqual(item["visual_asset"], "assets/characters/alfa/approved/master.png")


class TTSTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config/characters").mkdir(parents=True)
        (self.root / "assets/alfa").mkdir(parents=True)
        (self.root / "assets/beta").mkdir(parents=True)
        for char in ("alfa", "beta"):
            (self.root / f"assets/{char}/master.png").write_bytes(b"png")
            (self.root / f"config/characters/{char}.yaml").write_text(
                f'id: {char}\nimmagine_principale: "assets/{char}/master.png"\n', encoding="utf-8")
        self.voice_data = {"schema_version": 1, "voices": {
            "alfa": {"character_id": "alfa", "voice": "Vivian", "language": "Italian", "instruction": "Chiara.", "tone": "vivo", "pace": "medium", "seed": 1, "enabled": True, "parameters": {"temperature": .6}},
            "beta": {"character_id": "beta", "voice": "Ryan", "language": "English", "instruction": "Calm.", "tone": "calmo", "pace": "slow", "seed": 2, "enabled": True, "parameters": {"temperature": .6}},
        }}
        (self.root / "config/voices.yaml").write_text(json.dumps(self.voice_data), encoding="utf-8")
        self.config = {"schema_version": 1, "backend": "mock", "model": {"id": "mock-v1", "local_dir": "/missing", "revision": "1", "license": "test", "expected_size_bytes": 0}, "audio": {"sample_rate": 8000, "channels": 1, "sample_width": 2, "subsegment_pause_seconds": .1, "utterance_pause_seconds": .25, "mock_seconds_per_word": .05}, "text": {"max_words": 8, "hard_limit_words": 12}, "verification": {"min_duration_seconds": .1, "min_seconds_per_word": .01, "max_seconds_per_word": 2.0, "max_silence_percent": 99.0}}
        (self.root / "config/tts.json").write_text(json.dumps(self.config), encoding="utf-8")
        (self.root / "input").mkdir()
        (self.root / "input/dialogo.txt").write_text("[alfa|felice|pausa=0.4]\nCaffè, sigle CPU e numeri 2026.\n\n[beta]\nHello, world!\n", encoding="utf-8")
        for path in ("work/audio_segments", "work/metadata/audio_segments", "work/timeline", "output"):
            (self.root / path).mkdir(parents=True, exist_ok=True)
        self.characters = load_characters(self.root)
        self.voices = load_voices(self.root, self.characters)
        self.entries = parse_dialogue(self.root / "input/dialogo.txt", self.characters)
        self.plan = build_plan(self.root, self.entries, self.voices, self.config)

    def tearDown(self): self.temp.cleanup()

    def test_caricamento_voci_e_associazione_dinamica(self):
        self.assertEqual(self.voices["alfa"]["voice"], "Vivian")
        self.assertEqual(self.plan[1]["voice"], "Ryan")
        self.assertEqual(load_tts_config(self.root)["audio"]["sample_rate"], 8000)

    def test_hash_deterministico_e_invalidation_cache(self):
        self.assertEqual(canonical_hash({"b": 2, "a": 1}), canonical_hash({"a": 1, "b": 2}))
        generate(self.root, self.plan, self.config, "mock")
        fresh = build_plan(self.root, self.entries, self.voices, self.config)
        self.assertTrue(all(x["cache_status"] == "valid" for x in fresh))
        changed = copy.deepcopy(self.voice_data); changed["voices"]["alfa"]["seed"] = 99
        (self.root / "config/voices.yaml").write_text(json.dumps(changed), encoding="utf-8")
        new_voices = load_voices(self.root, self.characters)
        invalidated = build_plan(self.root, self.entries, new_voices, self.config)
        self.assertEqual(invalidated[0]["cache_status"], "regenerate")
        self.assertEqual(invalidated[1]["cache_status"], "valid")

    def test_suddivisione_lunga_senza_perdere_parole(self):
        text = "Prima frase completa con alcune parole utili. " + " ".join(f"parola{i}" for i in range(30))
        chunks = split_text(text, 8, 12)
        self.assertGreater(len(chunks), 2)
        self.assertEqual(" ".join(chunks).split(), text.split())
        self.assertTrue(all(len(chunk.split()) <= 12 for chunk in chunks))

    def test_pianificazione_pause_e_metadata(self):
        self.assertEqual(self.plan[0]["pause_after"], .4)
        self.assertEqual(self.plan[0]["cache_status"], "missing")
        generate(self.root, self.plan, self.config, "mock")
        metadata = json.loads(self.plan[0]["metadata_path"].read_text(encoding="utf-8"))
        for key in ("index", "speaker", "original_text", "emotion", "tts_instruction", "voice", "seed", "model", "parameters", "duration", "sample_rate", "text_hash", "voice_config_hash", "wav_hash", "generated_at", "status", "error"):
            self.assertIn(key, metadata)

    def test_wav_corrotto_invalida_cache(self):
        generate(self.root, self.plan, self.config, "mock")
        self.plan[0]["wav_path"].write_bytes(b"not-a-wave")
        self.assertEqual(cache_status(self.plan[0]), "regenerate")
        checks = verify_plan(self.plan, self.config)
        self.assertEqual(checks[0]["status"], "regenerate")

    def test_merge_wav_manifest_pause_e_timeline(self):
        generate(self.root, self.plan, self.config, "mock")
        manifest = merge_audio(self.root, build_plan(self.root, self.entries, self.voices, self.config), self.config)
        self.assertEqual([x["type"] for x in manifest["elements"]], ["segment", "pause", "segment"])
        self.assertEqual(manifest["elements"][1]["source"], "explicit")
        info = wav_info(self.root / "output/storycast_audio.wav")
        self.assertAlmostEqual(info["duration"], manifest["total_duration"], places=3)
        timeline = json.loads((self.root / "work/timeline/timeline.json").read_text(encoding="utf-8"))["entries"]
        self.assertLessEqual(timeline[0]["end"], timeline[1]["start"])
        self.assertEqual(timeline[0]["audio_status"], "valid")

    def test_modello_assente_errore_chiaro(self):
        real = copy.deepcopy(self.config); real["backend"] = "real"; real["model"]["id"] = "qwen"; real["model"]["revision"] = "x"
        plan = build_plan(self.root, self.entries, self.voices, real)
        with self.assertRaisesRegex(StorycastError, "Modello assente"):
            generate(self.root, plan, real, "real")

    def test_terzo_personaggio_senza_modifiche_codice(self):
        (self.root / "assets/gamma").mkdir(); (self.root / "assets/gamma/master.png").write_bytes(b"png")
        (self.root / "config/characters/gamma.yaml").write_text('id: gamma\nimmagine_principale: "assets/gamma/master.png"\n', encoding="utf-8")
        data = copy.deepcopy(self.voice_data); data["voices"]["gamma"] = {"character_id": "gamma", "voice": "Aiden", "language": "Italian", "instruction": "Neutra.", "tone": "neutro", "pace": "medium", "seed": 3, "enabled": True, "parameters": {}}
        (self.root / "config/voices.yaml").write_text(json.dumps(data), encoding="utf-8")
        (self.root / "input/dialogo.txt").write_text("[gamma]\nTerza voce.\n", encoding="utf-8")
        characters = load_characters(self.root); voices = load_voices(self.root, characters)
        entries = parse_dialogue(self.root / "input/dialogo.txt", characters)
        plan = build_plan(self.root, entries, voices, self.config)
        self.assertEqual(plan[0]["voice"], "Aiden")


if __name__ == "__main__":
    unittest.main()
