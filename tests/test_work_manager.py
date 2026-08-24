from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from storycast.core import StorycastError
from storycast.work_manager import clean_work, work_status


class WorkManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("assets", "output", "logs", "config", "models", "cache", "work"):
            (self.root / name).mkdir()
            (self.root / name / "sentinel").write_text(name, encoding="utf-8")
        # Il sentinel di work è runtime e deve invece sparire.
        self.protected = {name: (self.root / name / "sentinel").read_bytes() for name in ("assets", "output", "logs", "config", "models", "cache")}

    def tearDown(self):
        self.tmp.cleanup()

    def populate(self):
        episode = self.root / "work/episodes/prova"
        (episode / "audio_segments").mkdir(parents=True)
        (episode / "metadata/audio_segments").mkdir(parents=True)
        (episode / "scenes").mkdir(parents=True)
        (episode / "audio_segments/0001_a.wav").write_bytes(b"wav")
        (episode / "metadata/audio_segments/0001_a.json").write_text("{}")
        (episode / "scenes/scene_0001.mp4").write_bytes(b"video")
        (episode / "state.json").write_text(json.dumps({"phase": "rendering", "final_status": "running"}))

    def assert_protected(self):
        for name, content in self.protected.items():
            self.assertEqual(content, (self.root / name / "sentinel").read_bytes())

    def test_clean_requires_confirmation_and_dry_run_preserves(self):
        self.populate()
        with self.assertRaises(StorycastError):
            clean_work(self.root, dry_run=False, yes=False)
        report = clean_work(self.root, dry_run=True, yes=False)
        self.assertGreater(report["candidate_files"], 3)
        self.assertTrue((self.root / "work/episodes/prova/state.json").exists())

    def test_clean_removes_every_runtime_file_and_is_idempotent(self):
        self.populate()
        first = clean_work(self.root, dry_run=False, yes=True)
        second = clean_work(self.root, dry_run=False, yes=True)
        self.assertGreater(first["deleted_files"], 3)
        self.assertEqual(0, first["remaining"])
        self.assertEqual(0, second["deleted_files"])
        self.assertEqual([], list((self.root / "work").iterdir()))
        self.assert_protected()

    def test_gitkeep_is_the_only_preserved_element(self):
        folder = self.root / "work/kept"
        folder.mkdir()
        (folder / ".gitkeep").touch()
        (folder / "temporary.bin").write_bytes(b"x")
        report = clean_work(self.root, dry_run=False, yes=True)
        self.assertEqual(["work/kept/.gitkeep"], report["preserved"])
        self.assertTrue((folder / ".gitkeep").is_file())

    def test_status_empty_and_populated_details(self):
        # Rimuove soltanto il sentinel runtime creato dal setUp.
        clean_work(self.root, dry_run=False, yes=True)
        empty = work_status(self.root)
        self.assertEqual((0, 0, []), (empty["files"], empty["directories"], empty["slugs"]))
        self.populate()
        full = work_status(self.root, details=True)
        self.assertEqual(["prova"], full["slugs"])
        self.assertEqual(["prova"], full["incomplete"])
        self.assertEqual(["prova"], full["regeneration_episodes"])
        self.assertEqual(1, full["episodes"][0]["wav"])


if __name__ == "__main__":
    unittest.main()
