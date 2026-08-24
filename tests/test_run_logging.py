from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from storycast.run_logging import RunLogger, format_duration


class RunLoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def logger(self, seconds=0, **kwargs):
        instant=datetime(2026,8,3,20,15,0)+timedelta(seconds=seconds)
        return RunLogger(self.root,"episodio-test","input/racconto.txt",clock=lambda:instant,console=False,**kwargs)

    def test_creates_directory_unique_named_logs_and_latest(self):
        first=self.logger(); second=self.logger()
        self.assertTrue((self.root/"logs").is_dir())
        self.assertRegex(first.path.name,r"^episodio-test_20260803_201500\.log$")
        self.assertRegex(second.path.name,r"^episodio-test_20260803_201500_1\.log$")
        latest=self.root/"logs/latest.log"
        self.assertTrue(latest.is_symlink()); self.assertEqual(latest.resolve(),second.path)
        self.assertEqual(first.path.stat().st_mode & 0o777,0o644)

    def test_info_warning_error_and_no_verbose_content(self):
        log=self.logger(); log.info("Dialogo validato: 2 battute"); log.warning("Fallback visivo"); log.error("Fase TTS: errore sintetico")
        text=log.path.read_text(encoding="utf-8")
        self.assertIn("INFO    Dialogo validato",text); self.assertIn("WARNING Fallback",text); self.assertIn("ERROR   Fase TTS",text)
        self.assertNotIn("testo integrale segreto",text); self.assertNotIn("frame=",text)
        self.assertLess(len(text.splitlines()),20)

    def test_retention_only_removes_owned_logs(self):
        unknown=self.root/"logs/manuale.log"; unknown.parent.mkdir(); unknown.write_text("conservare")
        created=[]
        for second in range(5): created.append(self.logger(second,keep=3).path)
        remaining=sorted(p.name for p in (self.root/"logs").iterdir() if p.is_file() and not p.is_symlink())
        self.assertIn("manuale.log",remaining); self.assertEqual(len([x for x in remaining if x.startswith("episodio-test_")]),3)
        self.assertFalse(created[0].exists()); self.assertTrue(created[-1].exists())

    def test_level_and_duration(self):
        log=self.logger(level="WARNING"); log.info("non scritto"); log.debug("non scritto"); log.warning("scritto")
        text=log.path.read_text(); self.assertNotIn("non scritto",text); self.assertIn("scritto",text)
        self.assertEqual(format_duration(512),"00:08:32")


if __name__=="__main__": unittest.main()
