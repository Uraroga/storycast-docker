from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from storycast.cleanup import (_inside, execute, inventory, space_status)
from storycast.core import StorycastError


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        for folder in ("input","output","work/episodes","work/visual/library_v1/derived","logs",
                       "assets/characters","assets/groups","assets/story_images","config","storycast","scripts","tests","docs"):
            (self.root/folder).mkdir(parents=True,exist_ok=True)
        for protected in ("config/voices.yaml","storycast/app.py","scripts/tool.sh","tests/test_x.py","docs/README.md",
                          "assets/characters/master.png","assets/groups/group.png","assets/story_images/story.png"):
            (self.root/protected).write_text("protetto")
        (self.root/"input/MODELLO_DIALOGO.txt").write_text("modello")
        (self.root/"prompt_fisso_chatgpt.txt").write_text("prompt")
        (self.root/"prompt_fisso_chatgpt_short.txt").write_text("prompt short")
        (self.root/"avvia-storycast.sh").write_text("script")
        self.make_story("uno"); self.make_story("due")
        (self.root/"work/visual/library_v1/derived/crop.png").write_bytes(b"cache")
        (self.root/"logs/runtime.log").write_text("log")

    def tearDown(self): self.tmp.cleanup()

    def make_story(self,slug):
        work=self.root/f"work/episodes/{slug}"; output=self.root/f"output/{slug}"
        for folder in (work/"source",work/"audio_segments",work/"metadata/audio_segments",work/"scenes",work/"visual/cache",
                       work/"short/audio_segments",work/"short/video/scenes",output): folder.mkdir(parents=True,exist_ok=True)
        inp=self.root/f"input/{slug}.txt"; inp.write_text(f"[{slug}]\nTesto")
        (work/"source"/inp.name).write_text(inp.read_text()); (work/"audio_segments/0001.wav").write_bytes(b"wav")
        (work/"metadata/audio_segments/0001.json").write_text("{}")
        (work/"scenes/0001.mp4").write_bytes(b"scene"); (work/"visual/cache/item.bin").write_bytes(b"cache")
        (output/f"{slug}_audio.wav").write_bytes(b"audio"); (output/f"{slug}_video.mp4").write_bytes(b"video")
        for name in (f"{slug}_short_audio.wav",f"{slug}_short_timeline.json",f"{slug}_short_audio_manifest.json",
                     f"{slug}_short_video.mp4",f"{slug}_short_subtitles.srt"):
            (output/name).write_bytes(b"short")
        (work/"short/video/natural_master.mp4").write_bytes(b"master")
        (work/"short/video/vertical_plan.json").write_text("{}")
        (work/"short/video/natural_subtitles.ass").write_text("ass")
        (work/"state.json").write_text(json.dumps({"schema_version":1,"slug":slug,"input_original":f"input/{slug}.txt"}))

    def test_story_dry_run_does_not_delete_and_lists_all_categories(self):
        before=(self.root/"input/uno.txt").read_bytes(); result=execute(self.root,"story","uno",False)
        self.assertTrue(result["dry_run"]); self.assertEqual(result["deleted"]["files"],0)
        self.assertEqual((self.root/"input/uno.txt").read_bytes(),before)
        self.assertTrue({"wav","video","metadata_state","scene","cache"}.issubset(result["bytes_by_category"]))

    def test_complete_story_deletion_and_isolation(self):
        result=execute(self.root,"story","uno",True); self.assertGreater(result["deleted"]["files"],4)
        self.assertTrue((self.root/"input/uno.txt").exists()); self.assertFalse((self.root/"work/episodes/uno").exists())
        self.assertFalse((self.root/"output/uno").exists()); self.assertTrue((self.root/"work/episodes/due/state.json").exists())
        self.assertFalse((self.root/"work/episodes/uno/short/video/natural_master.mp4").exists())
        self.assertTrue((self.root/"input/due.txt").exists())

    def test_reset_preserves_code_config_assets_and_model(self):
        result=execute(self.root,"reset",None,True); self.assertGreater(result["deleted"]["files"],10)
        for path in ("config/voices.yaml","storycast/app.py","scripts/tool.sh","tests/test_x.py","docs/README.md",
                     "assets/characters/master.png","assets/groups/group.png","assets/story_images/story.png","input/MODELLO_DIALOGO.txt",
                     "input/uno.txt","input/due.txt","prompt_fisso_chatgpt.txt","prompt_fisso_chatgpt_short.txt",
                     "avvia-storycast.sh"):
            self.assertTrue((self.root/path).is_file(),path)
        self.assertFalse((self.root/"work/visual/library_v1/derived/crop.png").exists())
        for folder in ("input","output","work","work/episodes","logs"): self.assertTrue((self.root/folder).is_dir())

    def test_reset_is_idempotent_and_empty_second_time(self):
        execute(self.root,"reset",None,True); second=execute(self.root,"reset",None,True)
        self.assertEqual(second["files"],0); self.assertEqual(second["deleted"]["files"],0)

    def test_rejects_traversal_missing_story_and_project_root(self):
        for slug in ("../x","/tmp/x","A/B"):
            with self.assertRaises(StorycastError): inventory(self.root,"story",slug)
        with self.assertRaisesRegex(StorycastError,"inesistente"): inventory(self.root,"story","assente")
        with self.assertRaisesRegex(StorycastError,"root"): _inside(self.root,self.root)

    def test_rejects_external_symlink(self):
        outside=Path(self.tmp.name).parent/"storycast-cleanup-external-target"
        link=self.root/"work/episodes/uno/external"; link.symlink_to(outside)
        try:
            with self.assertRaisesRegex(StorycastError,"Symlink verso l'esterno"): inventory(self.root,"story","uno")
        finally: link.unlink(missing_ok=True)

    def test_refuses_active_generation_without_killing_it(self):
        lock=self.root/"work/episodes/uno/run.lock"; lock.write_text(json.dumps({"pid":os.getpid()}))
        with self.assertRaisesRegex(StorycastError,"Generazione attiva"): execute(self.root,"story","uno",True)
        self.assertTrue(lock.exists())

    def test_space_status_is_read_only(self):
        before=(self.root/"work/episodes/uno/state.json").read_bytes(); status=space_status(self.root)
        self.assertEqual(status["stories"],2); self.assertGreater(status["wav_bytes"],0)
        self.assertEqual((self.root/"work/episodes/uno/state.json").read_bytes(),before)


if __name__=="__main__": unittest.main()
