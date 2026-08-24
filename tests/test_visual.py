import copy
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from storycast.core import StorycastError
from storycast.visual import build_assets, load_visual, plan_shots, png_size, probe, render_video, sha256, validate_crop, verify_video

class VisualTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        for d in ("config","assets/groups/approved","work/timeline","work/visual","output"): (self.root/d).mkdir(parents=True,exist_ok=True)
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=c=blue:s=640x480","-frames:v","1",str(self.root/"assets/groups/approved/group.png")],check=True)
        self.visual={"schema_version":1,"group":{"id":"gruppo","source":"assets/groups/approved/group.png","wide_crop":[0,0.125,1,0.75]},"characters":[
          {"id":"alfa","speaker":"alfa","source":"assets/groups/approved/group.png","scene_area":[0,0,0.5,1],"medium_crop":[0,0.1,0.6,0.7],"closeup_crop":[0.1,0.1,0.4,0.5],"side":"left","shots":["medium","closeup"],"listening_assets":["gruppo_wide"],"enabled":True,"priority":1},
          {"id":"beta","speaker":"beta","source":"assets/groups/approved/group.png","scene_area":[0.5,0,0.5,1],"medium_crop":[0.4,0.1,0.6,0.7],"closeup_crop":[0.5,0.1,0.4,0.5],"side":"right","shots":["medium","closeup"],"listening_assets":[],"enabled":True,"priority":1}]}
        self.render={"schema_version":1,"video":{"width":320,"height":180,"fps":30,"codec":"libx264","pixel_format":"yuv420p","preset":"ultrafast","crf":28,"faststart":True},"audio":{"codec":"aac","bitrate":"64k","channels":1,"sample_rate":8000},"planner":{"minimum_shot_seconds":0.2,"opening_seconds":0.3,"closing_seconds":0.3,"long_speech_seconds":3,"seed":1},"camera":{"max_zoom":1.02,"intensity":0.3,"motions":["static"]}}
        self.write_configs(); self.write_timeline()

    def tearDown(self): self.tmp.cleanup()
    def write_configs(self):
        (self.root/"config/visual_assets.yaml").write_text(json.dumps(self.visual),encoding="utf-8")
        (self.root/"config/render.yaml").write_text(json.dumps(self.render),encoding="utf-8")
    def write_timeline(self):
        data={"schema_version":1,"entries":[{"index":1,"speaker":"alfa","start":0,"end":1.0,"duration":1.0,"pause_after":.4},{"index":2,"speaker":"beta","start":1.4,"end":2.4,"duration":1.0,"pause_after":0}]}
        (self.root/"work/timeline/test_reale_timeline.json").write_text(json.dumps(data),encoding="utf-8")
    def audio(self,duration=2.4):
        with wave.open(str(self.root/"output/test_reale_storycast_audio.wav"),"wb") as w:
            w.setparams((1,2,8000,round(duration*8000),"NONE","")); w.writeframes(b"\0\0"*round(duration*8000))

    def test_caricamento_configurazione(self): self.assertEqual(len(load_visual(self.root)[0]["characters"]),2)
    def test_coordinate_normalizzate(self):
        validate_crop([0,.1,.5,.5])
        with self.assertRaises(StorycastError): validate_crop([.8,0,.3,1])
    def test_associazione_speaker_asset(self): self.assertEqual(plan_shots(self.root)["scenes"][1]["visual_asset"],"alfa_medium")
    def test_asset_mancante(self):
        self.visual["group"]["source"]="assets/missing.png"; self.write_configs()
        with self.assertRaisesRegex(StorycastError,"mancante"): load_visual(self.root)
    def test_generazione_ritagli_e_risoluzione(self):
        m=build_assets(self.root); self.assertEqual(len(m["assets"]),5)
        self.assertTrue(all(png_size(self.root/x["output"])==(320,180) for x in m["assets"]))
    def test_cache_asset_derivati(self):
        build_assets(self.root); m=build_assets(self.root); self.assertTrue(all(x["cache_status"]=="cache_hit" for x in m["assets"]))
    def test_master_modificato_invalida_tutto(self):
        build_assets(self.root); subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=c=red:s=640x480","-frames:v","1",str(self.root/"assets/groups/approved/group.png")],check=True)
        self.assertTrue(all(x["cache_status"]=="create" for x in build_assets(self.root,dry_run=True)["assets"]))
    def test_invalidazione_selettiva_crop(self):
        build_assets(self.root); self.visual["characters"][0]["closeup_crop"]=[.11,.1,.4,.5]; self.write_configs(); m=build_assets(self.root,dry_run=True)
        changed=[x["id"] for x in m["assets"] if x["cache_status"]=="create"]; self.assertEqual(changed,["alfa_closeup"])
    def test_pause_e_pianificazione(self):
        p=plan_shots(self.root); self.assertTrue(any(s["speaker"] is None and s["start"]>=1 for s in p["scenes"]))
    def test_inquadrature_alternate_per_speaker(self):
        data={"schema_version":1,"entries":[{"index":i+1,"speaker":"alfa","start":i*1.2,"end":i*1.2+1,"duration":1,"pause_after":.2} for i in range(3)]}
        (self.root/"work/timeline/test_reale_timeline.json").write_text(json.dumps(data))
        shots=[s["shot_type"] for s in plan_shots(self.root)["scenes"] if s["speaker"]=="alfa"]
        self.assertEqual(shots,["medium","closeup","medium"])
    def test_copertura_senza_sovrapposizioni(self):
        s=plan_shots(self.root)["scenes"]; self.assertEqual(s[0]["start"],0); self.assertEqual(s[-1]["end"],2.4)
        self.assertTrue(all(a["end"]==b["start"] for a,b in zip(s,s[1:])))
    def test_durata_minima_apertura_configurata(self): self.assertGreaterEqual(plan_shots(self.root)["scenes"][0]["duration"],.2)
    def test_terzo_personaggio_solo_configurazione(self):
        gamma=copy.deepcopy(self.visual["characters"][0]); gamma.update(id="gamma",speaker="gamma"); self.visual["characters"].append(gamma); self.write_configs()
        data=json.loads((self.root/"work/timeline/test_reale_timeline.json").read_text()); data["entries"][1]["speaker"]="gamma"; (self.root/"work/timeline/test_reale_timeline.json").write_text(json.dumps(data))
        self.assertTrue(any(x["speaker"]=="gamma" for x in plan_shots(self.root)["scenes"]))
    def test_speaker_senza_asset(self):
        data=json.loads((self.root/"work/timeline/test_reale_timeline.json").read_text()); data["entries"][0]["speaker"]="missing"; (self.root/"work/timeline/test_reale_timeline.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(StorycastError,"senza asset"): plan_shots(self.root)
    def test_video_sintetico_codec_e_sync(self):
        self.audio(); result=render_video(self.root); self.assertEqual(result["status"],"rendered")
        checked=verify_video(self.root); self.assertEqual(checked["status"],"passed")
        streams=checked["ffprobe"]["streams"]; self.assertEqual(next(x for x in streams if x["codec_type"]=="video")["codec_name"],"h264")

if __name__=="__main__": unittest.main()
