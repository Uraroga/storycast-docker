from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from storycast.episode_bundle import pipeline_plan, precheck_episode_bundle
from storycast.short_pipeline import (run_short_audio, short_list_segments,
                                      short_paths, short_status)
from storycast.tts import file_hash, wav_info


class ShortPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for folder in ("input", "config/characters", "work", "output"):
            (self.root / folder).mkdir(parents=True)
        for character in ("uno", "due"):
            (self.root / f"config/characters/{character}.yaml").write_text(f"id: {character}\n")
        voices = {"schema_version": 1, "voices": {}}
        for character, voice, seed in (("uno", "Vivian", 11), ("due", "Ryan", 22)):
            voices["voices"][character] = {
                "character_id": character, "voice": voice, "language": "Italian",
                "instruction": "Parla chiaramente.", "tone": "test", "pace": "medium",
                "seed": seed, "enabled": True, "parameters": {"do_sample": False},
            }
        (self.root / "config/voices.yaml").write_text(json.dumps(voices))
        tts = {
            "schema_version": 1, "backend": "real",
            "model": {"id": "test", "local_dir": "/missing", "revision": "test"},
            "audio": {"sample_rate": 8000, "channels": 1, "sample_width": 2,
                      "subsegment_pause_seconds": .02, "utterance_pause_seconds": .1,
                      "mock_seconds_per_word": .06},
            "text": {"max_words": 50, "hard_limit_words": 70},
            "verification": {"min_duration_seconds": .1, "min_seconds_per_word": .01,
                             "max_seconds_per_word": 2, "max_silence_percent": 99,
                             "available_voices": ["Vivian", "Ryan"]},
        }
        (self.root / "config/tts.json").write_text(json.dumps(tts))
        (self.root / "config/pipeline.json").write_text(json.dumps({
            "schema_version": 1, "execution": "sequential",
            "precheck": {"required_characters": ["uno", "due"]},
            "short": {"enabled": True, "status": "video_not_implemented"},
        }))
        self.main = self.root / "input/storia.txt"
        self.short = self.root / "input/storia-short.txt"
        self.main.write_text("[uno|calmo]\nEpisodio lungo.\n\n[due|sereno]\nRisposta lunga.\n")
        self.short.write_text("[uno|curioso]\nPrima breve.\n\n[due|sereno]\nSeconda breve.\n\n[uno|deciso]\nTerza breve.\n")
        self.slug = "storia"

    def tearDown(self):
        self.temp.cleanup()

    def _run(self):
        return run_short_audio(self.root, self.main, self.slug, mock=True)

    def test_parsing_short_e_ordine(self):
        bundle = precheck_episode_bundle(self.root, self.main)
        self.assertEqual([x["text"] for x in bundle.short_entries],
                         ["Prima breve.", "Seconda breve.", "Terza breve."])

    def test_associazione_voci(self):
        result = self._run()
        self.assertEqual(result["voices"], {"due": "Ryan", "uno": "Vivian"})

    def test_segmentazione_e_namespace_separato(self):
        self._run(); rows = short_list_segments(self.root, self.main, self.slug, mock=True)
        self.assertEqual([x["index"] for x in rows], [1, 2, 3])
        self.assertTrue(all("/short/audio_segments/" in x["wav"] for x in rows))
        self.assertTrue(all("/short/" not in x["wav"].replace("/short/audio_segments/", "") for x in rows))

    def test_timeline_short_completa_e_senza_sovrapposizioni(self):
        self._run(); rows = short_list_segments(self.root, self.main, self.slug, mock=True)
        self.assertTrue(all(x["duration"] > 0 and x["start"] < x["end"] for x in rows))
        self.assertTrue(all(left["end"] <= right["start"] for left, right in zip(rows, rows[1:])))

    def test_output_wav_e_sample_rate(self):
        result = self._run(); path = self.root / result["audio"]
        self.assertEqual(path, short_paths(self.root, self.slug)["audio"])
        self.assertEqual(wav_info(path)["sample_rate"], 8000)

    def test_timeline_contiene_dati_richiesti(self):
        self._run(); rows = short_list_segments(self.root, self.main, self.slug, mock=True)
        for row in rows:
            self.assertTrue({"index", "speaker", "emotion", "text", "voice", "duration",
                             "start", "end", "wav", "cache"}.issubset(row))

    def test_cache_hit_seconda_esecuzione(self):
        first = self._run(); before = file_hash(self.root / first["audio"])
        second = self._run()
        self.assertEqual(second["tts"]["generated"], 0)
        self.assertEqual(second["tts"]["cached"], 3)
        self.assertEqual(file_hash(self.root / second["audio"]), before)

    def test_invalidazione_selettiva(self):
        self._run(); paths = short_paths(self.root, self.slug)
        before = {index: file_hash(paths["segment_metadata"] / f"{index:04d}_{speaker}.json")
                  for index, speaker in ((1, "uno"), (2, "due"), (3, "uno"))}
        self.short.write_text("[uno|curioso]\nPrima modificata.\n\n[due|sereno]\nSeconda breve.\n\n[uno|deciso]\nTerza breve.\n")
        result = self._run()
        after = {index: file_hash(paths["segment_metadata"] / f"{index:04d}_{speaker}.json")
                 for index, speaker in ((1, "uno"), (2, "due"), (3, "uno"))}
        self.assertEqual(result["tts"]["generated"], 1)
        self.assertNotEqual(before[1], after[1])
        self.assertEqual({key: before[key] for key in (2, 3)}, {key: after[key] for key in (2, 3)})

    def test_timeline_principale_non_modificata(self):
        main_timeline = self.root / "output/storia/storia_timeline.json"
        main_timeline.parent.mkdir(parents=True); main_timeline.write_text('{"sentinel":true}\n')
        before = file_hash(main_timeline); self._run()
        self.assertEqual(file_hash(main_timeline), before)

    def test_stato_audio_ready_e_video_non_implementato(self):
        self._run(); status = short_status(self.root, self.main, self.slug, mock=True)
        self.assertEqual(status["status"], "audio_ready")
        self.assertEqual(status["video_status"], "not_implemented")

    def test_esecuzione_sequenziale_dichiarata(self):
        plan = pipeline_plan(self.root, self.slug, precheck_episode_bundle(self.root, self.main))
        self.assertEqual(plan["execution"], "sequential")
        self.assertEqual(plan["phases"], ["precheck", "main_episode", "main_checks", "short_audio", "short_video", "final_checks"])

    def test_backend_reale_non_avviato_in_mock(self):
        with mock.patch("storycast.tts._real_generator") as real:
            self._run()
        real.assert_not_called()

    def test_renderer_non_avviato(self):
        with mock.patch("storycast.orchestrator.render_library") as render:
            self._run()
        render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
