from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from storycast.core import StorycastError
from storycast.story_images import (apply_story_inserts, choose_story_images,
    discover_story_images, plan_story_inserts, story_images_signature)
from storycast.visual_library import _scene_command


class StoryImagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        (self.root/"assets/story_images").mkdir(parents=True)
        (self.root/"config").mkdir()
        self.cfg={"directory":"assets/story_images","extensions":[".png",".jpg",".jpeg",".webp"],
                  "insert_seconds":6.0,"fade_seconds":.4,"opening_guard_seconds":20.0,
                  "closing_guard_seconds":30.0,"target_interval_seconds":60.0,"max_zoom":1.035}
        render={"schema_version":1,"story_images":self.cfg,"video":{"width":1280,"height":720,"fps":30,"codec":"libx264",
                "pixel_format":"yuv420p","preset":"ultrafast","crf":30},"camera":{"max_zoom":1.045}}
        (self.root/"config/render.yaml").write_text(json.dumps(render))

    def tearDown(self): self.tmp.cleanup()

    def image(self,name,color="red"):
        path=self.root/"assets/story_images"/name
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",f"color={color}:s=640x480","-frames:v","1","-y",str(path)],check=True)
        return path

    def base_plan(self,total=180.0):
        return {"total_duration":total,"scenes":[{"index":1,"start":0.0,"end":total,"duration":total,
            "speaker":"a","source_asset":"assets/a.png","derived_asset":"work/a.png","asset_id":"a",
            "pose_type":"talking","movement":"static","transition":"cut","reason":"base"}]}

    def test_cartella_vuota_e_non_immagini_ignorate(self):
        (self.root/"assets/story_images/note.txt").write_text("no")
        self.assertEqual(discover_story_images(self.root),[])

    def test_formati_validi_e_ordine_deterministico(self):
        self.image("B.jpg","blue"); self.image("a.png"); self.image("c.webp","green")
        self.assertEqual([Path(x["path"]).name for x in discover_story_images(self.root)],["a.png","B.jpg","c.webp"])

    def test_conferma_yes_no_e_annullamento_senza_immagini(self):
        self.image("a.png")
        self.assertTrue(choose_story_images(self.root,"ask",input_fn=lambda _:"s",interactive=True)["enabled"])
        self.assertFalse(choose_story_images(self.root,"ask",input_fn=lambda _:"n",interactive=True)["enabled"])
        (self.root/"assets/story_images/a.png").unlink()
        with self.assertRaisesRegex(StorycastError,"annullata"):
            choose_story_images(self.root,"ask",input_fn=lambda _:"n",interactive=True)

    def test_modalita_non_interattiva_controllabile(self):
        self.image("a.png")
        self.assertTrue(choose_story_images(self.root,"yes",interactive=False)["enabled"])
        self.assertFalse(choose_story_images(self.root,"no",interactive=False)["enabled"])
        self.assertFalse(choose_story_images(self.root,"ask",interactive=False)["enabled"])

    def test_distribuzione_guardie_durata_e_non_ripetizione(self):
        for index,color in enumerate(("red","blue","green"),1): self.image(f"{index}.png",color)
        inserts=plan_story_inserts(240,discover_story_images(self.root),self.cfg)
        self.assertEqual(len(inserts),3)
        self.assertTrue(all(x["start"]>=20 and x["end"]<=210 for x in inserts))
        self.assertTrue(all(x["duration"]==6 for x in inserts))
        self.assertEqual(len({x["image"]["path"] for x in inserts}),3)
        self.assertGreater(min(b["start"]-a["end"] for a,b in zip(inserts,inserts[1:])),30)

    def test_episodio_corto_non_inserisce(self):
        self.image("a.png")
        self.assertEqual(plan_story_inserts(55,discover_story_images(self.root),self.cfg),[])

    def test_piano_spezza_scene_senza_cambiare_durata(self):
        self.image("a.png"); result=apply_story_inserts(self.base_plan(),discover_story_images(self.root),self.cfg)
        story=[x for x in result["scenes"] if x.get("story_image")]
        self.assertEqual(len(story),1); self.assertAlmostEqual(story[0]["duration"],6)
        self.assertAlmostEqual(sum(x["duration"] for x in result["scenes"]),180)

    def test_comando_ffmpeg_contiene_fade_e_zoom_lento(self):
        self.image("a.png"); scene=apply_story_inserts(self.base_plan(),discover_story_images(self.root),self.cfg)["story_images"][0]
        visual={"duration":scene["duration"],"movement":"story_slow_zoom","derived_asset":scene["image"]["path"],"story_image":True}
        command=_scene_command(self.root,visual,json.loads((self.root/"config/render.yaml").read_text()),self.root/"out.mp4")
        vf=command[command.index("-vf")+1]
        self.assertIn("fade=t=in:st=0:d=0.4",vf); self.assertIn("fade=t=out:st=5.6:d=0.4",vf)
        self.assertIn("zoompan",vf); self.assertIn("1.035",vf)

    def test_firma_cambia_con_set_e_modalita(self):
        self.image("a.png"); one=discover_story_images(self.root)
        first=story_images_signature(one,True)
        self.image("b.png","blue"); two=discover_story_images(self.root)
        self.assertNotEqual(first,story_images_signature(two,True))
        self.assertNotEqual(first,story_images_signature(one,False))


if __name__=="__main__": unittest.main()
