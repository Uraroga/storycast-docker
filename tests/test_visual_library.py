from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from storycast.core import StorycastError
from storycast.visual import png_size, probe, sha256
from storycast.visual_library import (AUDIO, FINAL_VIDEO, MANIFEST, SHOT_PLAN,
                                      _crop_pixels, build_library, inspect_library,
                                      plan_library, render_library)


class VisualLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/"config").mkdir()
        for folder in ("assets/characters/a","assets/characters/b","assets/groups","assets/characters/a/archive","assets/characters/a/approved"):
            (self.root/folder).mkdir(parents=True,exist_ok=True)
        (self.root/"work/timeline").mkdir(parents=True)
        (self.root/"output").mkdir()
        assets = []
        specs = [
            ("group_intro", "group", None, [], "intro", [1448,1086]),
            ("a_main", "speaking_primary", "a", ["a"], "talking", [1122,1402]),
            ("a_alt", "speaking_alternative", "a", ["a"], "alternative", [1448,1086]),
            ("a_listen", "listening", "a", ["b"], "listening", [1448,1086]),
            ("b_main", "speaking_primary", "b", ["b"], "talking", [1122,1402]),
            ("b_alt", "speaking_alternative", "b", ["b"], "alternative", [1448,1086]),
            ("b_listen", "listening", "b", ["a"], "listening", [1448,1086]),
        ]
        colors = ["red", "green", "blue", "yellow", "magenta", "cyan", "orange"]
        for (asset_id, function, character, speakers, pose, size), color in zip(specs, colors):
            folder="assets/groups" if character is None else f"assets/characters/{character}"
            semantic={"speaking_primary":"talking","speaking_alternative":"talking_open_hand","listening":"listening"}.get(function,pose)
            path = self.root/f"{folder}/{character or 'group'}_{semantic}_{asset_id}.png"
            subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",f"color={color}:s={size[0]}x{size[1]}","-frames:v","1","-y",str(path)],check=True)
            assets.append({"id":asset_id,"source":str(path.relative_to(self.root)),"character":character,"function":function,"compatible_speakers":speakers,"pose_type":pose,"approved":True,"priority":90 if function=="speaking_primary" else 50,"resolution":size,"aspect_ratio":"test","sha256":sha256(path),"crop":[0,0,1,0.75] if size[0]>size[1] else [0,.1,1,.45],"crop_allowed":True,"zoom_allowed":True,"limitations":[]})
        (self.root/"assets/characters/a/archive/rejected.png").write_bytes(b"excluded")
        master=next((self.root/"assets/characters/a").glob("*.png"))
        (self.root/"assets/characters/a/approved/a_master.png").write_bytes(master.read_bytes())
        self.catalog={"schema_version":1,"library_id":"test","derived_root":"work/visual/library_v1/derived","character_root":"assets/characters","group_root":"assets/groups","extensions":[".png",".jpg",".jpeg"],"archive_exclusions":["approved","archive"]}
        self.render={"schema_version":1,"video":{"width":1280,"height":720,"fps":30,"codec":"libx264","pixel_format":"yuv420p","preset":"ultrafast","crf":28,"faststart":True},"audio":{"codec":"aac","bitrate":"96k","channels":1,"sample_rate":24000},"camera":{"max_zoom":1.02},"library_planner":{"minimum_scene_seconds":.2,"opening_seconds":.2,"closing_seconds":.2,"reaction_min_speech_seconds":1.2,"reaction_seconds":.25,"alternative_every_occurrences":3,"seed":7,"transform":{"scale_filter":"bilinear","target_resolution":[1280,720],"preserve_aspect_ratio":True}}}
        self._write_configs()
        entries=[]; cursor=0.0
        for i,speaker in enumerate(["a","b","a","b","a","b","a","b"],1):
            duration=1.5 if i in (1,2) else .6
            entries.append({"index":i,"speaker":speaker,"start":cursor,"end":cursor+duration,"duration":duration,"pause_after":.1 if i<8 else 0})
            cursor += duration + (.1 if i<8 else 0)
        self.timeline={"schema_version":1,"entries":entries}
        (self.root/"work/timeline/storycast_episode_01_timeline.json").write_text(json.dumps(self.timeline))

    def tearDown(self): self.tmp.cleanup()
    def _write_configs(self):
        (self.root/"config/visual_library.yaml").write_text(json.dumps(self.catalog))
        (self.root/"config/render.yaml").write_text(json.dumps(self.render))

    def test_recognizes_seven_and_excludes_archive(self):
        status=inspect_library(self.root)
        self.assertEqual(status["asset_count"],7)
        self.assertEqual(status["excluded"],["assets/characters/a/approved/a_master.png","assets/characters/a/archive/rejected.png"])
        self.assertEqual(status["diagnostics"]["files_accepted"],7)
        self.assertEqual(status["diagnostics"]["discard_reasons"],{"directory_esclusa":2})

    def test_missing_and_corrupt_png_rejected(self):
        source=next((self.root/"assets/groups").glob("*.png"))
        saved=source.read_bytes(); source.unlink()
        with self.assertRaisesRegex(StorycastError,"gruppo"): inspect_library(self.root)
        source.write_bytes(b"bad")
        with self.assertRaisesRegex(StorycastError,"gruppo"): inspect_library(self.root)
        source.write_bytes(saved)

    def test_vertical_crop_is_16_9_even_and_no_deformation(self):
        x,y,w,h=_crop_pixels([0,.1,1,.45],(1122,1402),(1280,720))
        self.assertEqual((w%2,h%2),(0,0)); self.assertLess(abs(w/h-16/9),.01)
        manifest=build_library(self.root)
        for item in manifest["assets"]: self.assertEqual(png_size(self.root/item["derived"]),(1280,720))

    def test_cache_is_content_and_transform_based(self):
        first=build_library(self.root); second=build_library(self.root)
        self.assertTrue(all(x["cache_status"]=="cache_hit" for x in second["assets"]))
        self.render["library_planner"]["transform"]["scale_filter"]="lanczos"; self._write_configs()
        third=build_library(self.root,dry_run=True)
        self.assertTrue(all(x["cache_status"]=="create" for x in third["assets"]))
        self.assertNotEqual(first["assets"][0]["cache_key"],third["assets"][0]["cache_key"])

    def test_plan_roles_variety_and_determinism(self):
        manifest=build_library(self.root)
        one=plan_library(self.root,manifest=manifest); two=plan_library(self.root,dry_run=True,manifest=manifest)
        self.assertEqual(one["scenes"],two["scenes"])
        self.assertEqual(one["scenes"][0]["pose_type"],"intro")
        self.assertEqual(one["scenes"][-1]["pose_type"],"intro")
        ids=[x["asset_id"] for x in one["scenes"]]
        self.assertTrue(any("a_talking" in x for x in ids)); self.assertTrue(any("b_talking" in x for x in ids))
        for scene in one["scenes"]:
            if scene["pose_type"]=="listening":
                compatible=next(x["compatible_speakers"] for x in manifest["assets"] if x["id"]==scene["asset_id"])
                self.assertIn(scene["speaker"],compatible)

    def test_complete_non_overlapping_coverage(self):
        plan=plan_library(self.root,manifest=build_library(self.root))
        self.assertEqual(plan["scenes"][0]["start"],0)
        self.assertEqual(plan["scenes"][-1]["end"],plan["total_duration"])
        for left,right in zip(plan["scenes"],plan["scenes"][1:]): self.assertEqual(left["end"],right["start"])

    def test_future_third_character_uses_catalog_roles(self):
        (self.root/"assets/characters/c").mkdir(); new=self.root/"assets/characters/c/c_talking_main.png"
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=white:s=1122x1402","-frames:v","1","-y",str(new)],check=True)
        self.timeline["entries"]=[{"index":1,"speaker":"c","start":0,"end":1,"duration":1,"pause_after":0}]
        self._write_configs(); (self.root/"work/timeline/storycast_episode_01_timeline.json").write_text(json.dumps(self.timeline))
        plan=plan_library(self.root,manifest=build_library(self.root))
        self.assertIn("c_talking_main",[x["asset_id"] for x in plan["scenes"]])

    def test_unknown_name_single_image_and_invalid_file(self):
        for path in list((self.root/"assets/characters/a").glob("*.png"))[1:]: path.unlink()
        only=next((self.root/"assets/characters/a").glob("*.png")); unknown=only.with_name("a_misteriosa.png"); only.rename(unknown)
        (self.root/"assets/characters/a/rotta.jpg").write_bytes(b"non-immagine")
        status=inspect_library(self.root)
        item=next(x for x in status["assets"] if x["source"].endswith("a_misteriosa.png"))
        self.assertEqual(item["function"],"generic")
        self.assertTrue(any("non leggibile" in x for x in status["warnings"]))
        plan=plan_library(self.root,manifest=build_library(self.root))
        self.assertTrue(any(x["speaker"]=="a" and x["asset_id"]==item["id"] for x in plan["scenes"]))

    def test_png_jpg_jpeg_case_insensitive(self):
        before=inspect_library(self.root)["asset_count"]
        for name,color in (("a_extra.PNG","purple"),("a_extra.JPG","pink"),("a_extra.JpEg","brown")):
            subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",f"color={color}:s=320x180","-frames:v","1","-y",str(self.root/"assets/characters/a"/name)],check=True)
        self.assertEqual(inspect_library(self.root)["asset_count"],before+3)

    def test_no_consecutive_repetition_when_alternatives_exist(self):
        plan=plan_library(self.root,manifest=build_library(self.root))
        ids=[x["asset_id"] for x in plan["scenes"]]
        self.assertTrue(all(a!=b for a,b in zip(ids,ids[1:])))

    def test_dry_runs_do_not_create_outputs(self):
        build_library(self.root,dry_run=True); plan_library(self.root,dry_run=True)
        self.assertFalse((self.root/MANIFEST).exists()); self.assertFalse((self.root/SHOT_PLAN).exists()); self.assertFalse((self.root/FINAL_VIDEO).exists())

    def test_render_codec_resolution_and_duration(self):
        total=max(x["end"]+x["pause_after"] for x in self.timeline["entries"])
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t",str(total),"-c:a","pcm_s16le","-y",str(self.root/AUDIO)],check=True)
        render_library(self.root)
        info=probe(self.root,self.root/FINAL_VIDEO); video=next(x for x in info["streams"] if x["codec_type"]=="video"); audio=next(x for x in info["streams"] if x["codec_type"]=="audio")
        self.assertEqual((video["codec_name"],video["width"],video["height"],video["pix_fmt"]),("h264",1280,720,"yuv420p"))
        self.assertEqual((audio["codec_name"],audio["channels"]),("aac",1))
        self.assertLessEqual(abs(float(video["duration"])-total),1/30+.001)


if __name__ == "__main__": unittest.main()


class RealContainerLibraryTests(unittest.TestCase):
    @unittest.skipUnless(Path("/app/assets/characters/personaggio_1").is_dir(), "solo integrazione Docker reale")
    def test_real_mounted_library_and_shared_loader(self):
        from storycast import orchestrator
        status=inspect_library(Path("/app"))
        counts={"personaggio_1":0,"personaggio_2":0,"groups":0}
        for asset in status["assets"]:
            source=Path(asset["source"])
            key=source.parts[2] if source.parts[:2]==("assets","characters") else "groups"
            counts[key]+=1
        self.assertEqual(counts,{"personaggio_1":7,"personaggio_2":7,"groups":4})
        self.assertIs(orchestrator.inspect_library,inspect_library)
        self.assertEqual(status["diagnostics"]["container_project_root"],"/app")
        self.assertTrue(all(x["exists"] and x["readable"] for x in status["diagnostics"]["directories"]))
