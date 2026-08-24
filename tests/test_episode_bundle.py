from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from storycast.core import StorycastError, load_characters, parse_dialogue
from storycast.episode_bundle import associated_short_path, pipeline_plan, precheck_episode_bundle
from storycast.orchestrator import run_story


class EpisodeBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "input").mkdir()
        (self.root / "config/characters").mkdir(parents=True)
        for character in ("uno", "due"):
            (self.root / f"config/characters/{character}.yaml").write_text(
                f"id: {character}\n", encoding="utf-8"
            )
        (self.root / "config/pipeline.json").write_text(json.dumps({
            "schema_version": 1,
            "precheck": {"required_characters": ["uno", "due"]},
            "short": {"enabled": False},
        }), encoding="utf-8")
        self.main = self.root / "input/Storia.txt"
        self.short = self.root / "input/Storia-short.txt"
        self.valid = "[uno|curioso]\nPrima battuta.\n\n[due|sereno]\nSeconda battuta.\n"
        self.main.write_text(self.valid, encoding="utf-8")
        self.short.write_text(self.valid, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_associazione_automatica_conserva_nome_e_cartella(self):
        self.assertEqual(associated_short_path(self.main), self.short)

    def test_short_presente_e_valido(self):
        bundle = precheck_episode_bundle(self.root, self.main)
        self.assertEqual(bundle.short_path, self.short)
        self.assertEqual(len(bundle.short_entries), 2)

    def test_short_mancante(self):
        self.short.unlink()
        with self.assertRaisesRegex(StorycastError, "Short associato non trovato"):
            precheck_episode_bundle(self.root, self.main)

    def test_short_vuoto(self):
        self.short.write_text(" \n", encoding="utf-8")
        with self.assertRaisesRegex(StorycastError, "File input vuoto"):
            precheck_episode_bundle(self.root, self.main)

    def test_short_formato_non_valido(self):
        self.short.write_text("[uno|curioso\nTesto.\n", encoding="utf-8")
        with self.assertRaisesRegex(StorycastError, "Sintassi intestazione errata"):
            precheck_episode_bundle(self.root, self.main)

    def test_entrambi_i_personaggi_richiesti_presenti(self):
        bundle = precheck_episode_bundle(self.root, self.main)
        self.assertEqual({x["speaker"] for x in bundle.short_entries}, {"uno", "due"})

    def test_short_con_solo_primo_personaggio(self):
        self.short.write_text("[uno|sereno]\nSolo uno.\n", encoding="utf-8")
        with self.assertRaisesRegex(StorycastError, "due"):
            precheck_episode_bundle(self.root, self.main)

    def test_short_con_solo_secondo_personaggio(self):
        self.short.write_text("[due|sereno]\nSolo due.\n", encoding="utf-8")
        with self.assertRaisesRegex(StorycastError, "uno"):
            precheck_episode_bundle(self.root, self.main)

    def test_parser_esistente_non_cambia(self):
        entries = parse_dialogue(self.main, load_characters(self.root))
        self.assertEqual([x["speaker"] for x in entries], ["uno", "due"])

    def test_piano_sequenziale_e_output_futuri(self):
        plan = pipeline_plan(self.root, "storia", precheck_episode_bundle(self.root, self.main))
        self.assertEqual(plan["execution"], "sequential")
        self.assertEqual(plan["phases"], ["precheck", "main_episode", "main_checks", "short_audio", "short_video", "final_checks"])
        self.assertEqual(plan["short"]["status"], "ready")
        self.assertTrue(plan["short"]["outputs"][2].endswith("storia_short_subtitles.srt"))

    def test_precheck_blocca_tts_e_rendering(self):
        self.short.unlink()
        with mock.patch("storycast.orchestrator.generate") as tts, \
             mock.patch("storycast.orchestrator.render_library") as render:
            with self.assertRaisesRegex(StorycastError, "Short associato non trovato"):
                run_story(self.root, self.main, "storia", mock=True)
        tts.assert_not_called()
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
