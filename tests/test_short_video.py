from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from storycast.episode_bundle import precheck_episode_bundle
from storycast.short_pipeline import run_short_audio, short_paths
from storycast.short_video import (apply_display_aliases, build_subtitles, build_vertical_plan, final_filter,
                                   load_short_video_config, render_short_video,
                                   vertical_crop, write_srt)
from storycast.tts import file_hash


class ShortVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        for folder in ("input", "config/characters", "assets/characters/p1", "assets/characters/p2",
                       "assets/groups", "work", "output"):
            (self.root/folder).mkdir(parents=True)
        for character in ("p1", "p2"):
            (self.root/f"config/characters/{character}.yaml").write_text(f"id: {character}\n")
        for rel, color in (("assets/characters/p1/p1_talking_open_hand.png", "red"),
                           ("assets/characters/p1/p1_talking_leaning.png", "yellow"),
                           ("assets/characters/p2/p2_talking_open_hand.png", "blue"),
                           ("assets/characters/p2/p2_talking_leaning.png", "cyan"),
                           ("assets/groups/group_duo_neutral.png", "green")):
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=320x180",
                            "-frames:v", "1", "-y", str(self.root/rel)], check=True)
        (self.root/"config/visual_library.yaml").write_text(json.dumps({
            "schema_version": 1, "library_id": "short-test", "derived_root": "work/derived",
            "character_root": "assets/characters", "group_root": "assets/groups",
            "extensions": [".png"], "archive_exclusions": []}))
        (self.root/"config/render.yaml").write_text(json.dumps({
            "schema_version":1,"video":{"width":160,"height":90,"fps":30,"codec":"libx264",
                "pixel_format":"yuv420p","preset":"ultrafast","crf":30},
            "audio":{"codec":"aac","bitrate":"64k","channels":1,"sample_rate":8000},
            "camera":{"max_zoom":1.01},
            "library_planner":{"minimum_scene_seconds":.1,"opening_seconds":.1,"closing_seconds":.1,
                "reaction_min_speech_seconds":.3,"reaction_seconds":.1,"alternative_every_occurrences":3,
                "seed":7,"transform":{"scale_filter":"bilinear","target_resolution":[160,90],
                "preserve_aspect_ratio":True}}}))
        voices = {"schema_version": 1, "voices": {}}
        for char, voice in (("p1", "Vivian"), ("p2", "Ryan")):
            voices["voices"][char] = {"character_id": char, "voice": voice, "language": "Italian",
                "instruction": "Parla.", "tone": "test", "pace": "medium", "seed": 7,
                "enabled": True, "parameters": {"do_sample": False}}
        (self.root/"config/voices.yaml").write_text(json.dumps(voices))
        (self.root/"config/tts.json").write_text(json.dumps({"schema_version":1,"backend":"real",
            "model":{"id":"test","local_dir":"/missing","revision":"x"},
            "audio":{"sample_rate":8000,"channels":1,"sample_width":2,"subsegment_pause_seconds":.02,
                     "utterance_pause_seconds":.05,"mock_seconds_per_word":.08},
            "text":{"max_words":50,"hard_limit_words":70},
            "verification":{"min_duration_seconds":.1,"min_seconds_per_word":.01,"max_seconds_per_word":2,
                            "max_silence_percent":99,"available_voices":["Vivian","Ryan"]}}))
        self.short_cfg = {"enabled":True,"status":"video_ready","aspect_ratio":"9:16",
            "resolution":[1080,1920],"fps":30,"video_codec":"libx264","pixel_format":"yuv420p",
            "video_preset":"ultrafast","video_crf":30,"audio_codec":"aac","audio_bitrate":"64k",
            "final_speed":1.30,"final_hold_seconds":2.0,"subtitles":True,
            "display_aliases":{"Ura Roga":"Uraroga"},
            "subtitle_style":{"font":"DejaVu Sans","font_size":68,"margin_left":90,"margin_right":150,
                "margin_bottom":360,"outline":5,"shadow":2,"max_words":8,"max_chars_per_line":24,
                "max_lines":2,"minimum_seconds":.75},
            "vertical_crop":{"p1":{"center_x":.53,"center_y":.5},"p2":{"center_x":.47,"center_y":.5},
                             "group":{"center_x":.5,"center_y":.5}},
            "visual":{"seed":9,"max_scene_seconds":7.0,"minimum_scene_seconds":1.0,
                      "max_zoom":1.02,"prefer_individual":True}}
        (self.root/"config/pipeline.json").write_text(json.dumps({"schema_version":1,"execution":"sequential",
            "precheck":{"required_characters":["p1","p2"]},"short":self.short_cfg}))
        self.main=self.root/"input/storia.txt"; self.short=self.root/"input/storia-short.txt"
        self.main.write_text("[p1|calma]\nPrincipale.\n\n[p2|sereno]\nRisposta.\n")
        self.short.write_text("[p1|curiosa]\nPrima battuta breve.\n\n[p2|sereno]\nIl canale Ura Roga.\n")
        self.slug="storia"

    def tearDown(self): self.temp.cleanup()
    def _audio(self): return run_short_audio(self.root,self.main,self.slug,mock=True)
    def _plan(self):
        self._audio(); p=short_paths(self.root,self.slug)
        return build_vertical_plan(self.root,p["timeline"],p["work"]/"video/plan.json")

    def test_configurazione_1080x1920(self): self.assertEqual(load_short_video_config(self.root)["resolution"],[1080,1920])
    def test_aspect_ratio_9_16(self):
        w,h=load_short_video_config(self.root)["resolution"]; self.assertEqual(w*16,h*9)
    def test_fps_30(self): self.assertEqual(load_short_video_config(self.root)["fps"],30)
    def test_crop_personaggio_1(self): self.assertGreater(self._plan()["scenes"][0]["crop"]["x"],100)
    def test_crop_personaggio_2(self): self.assertLess(self._plan()["scenes"][-1]["crop"]["x"],110)
    def test_crop_entro_limiti(self):
        for cx in (0,.5,1):
            c=vertical_crop(320,180,cx,.5); self.assertGreaterEqual(c["x"],0); self.assertLessEqual(c["x"]+c["width"],320)
    def test_selezione_pose_valida(self): self.assertTrue(all("talking" in x["pose_type"] for x in self._plan()["scenes"]))
    def test_nessuna_ripetizione_consecutiva(self):
        ids=[x["asset_id"] for x in self._plan()["scenes"]]; self.assertTrue(all(a!=b for a,b in zip(ids,ids[1:])))
    def test_timeline_video_naturale_coerente(self):
        p=self._plan(); self.assertAlmostEqual(p["scenes"][-1]["end"],p["natural_duration"])
    def test_sottotitoli_derivati_timeline(self):
        self._audio(); p=short_paths(self.root,self.slug); subs=build_subtitles(p["timeline"],self.short_cfg)
        self.assertIn("Prima battuta",subs[0]["text"])
    def test_sottotitoli_massimo_parole(self):
        self._audio(); subs=build_subtitles(short_paths(self.root,self.slug)["timeline"],self.short_cfg)
        self.assertTrue(all(len(x["text"].split())<=8 and x["lines"]<=2 for x in subs))
    def test_safe_area_sottotitoli(self): self.assertGreaterEqual(self.short_cfg["subtitle_style"]["margin_bottom"],300)
    def test_velocizzazione_una_volta(self): self.assertEqual(final_filter(1.3,2).count("PTS/1.3"),1)
    def test_audio_video_accelerati_insieme(self):
        f=final_filter(1.3,2); self.assertIn("setpts=PTS/1.3",f); self.assertIn("atempo=1.3",f)
    def test_srt_riscalato(self):
        self._audio(); p=short_paths(self.root,self.slug); natural=build_subtitles(p["timeline"],self.short_cfg)
        final=build_subtitles(p["timeline"],self.short_cfg,1.3); self.assertAlmostEqual(final[-1]["end"],natural[-1]["end"]/1.3)
    def test_hold_due_secondi(self): self.assertIn("stop_duration=2.6",final_filter(1.3,2))
    def test_nessun_sottotitolo_nel_hold(self):
        self._audio(); p=short_paths(self.root,self.slug); subs=build_subtitles(p["timeline"],self.short_cfg,1.3)
        self.assertAlmostEqual(subs[-1]["end"],json.loads(p["timeline"].read_text())["entries"][-1]["end"]/1.3)
    def test_ultima_scena_mantenuta(self): self.assertIn("tpad=stop_mode=clone",final_filter(1.3,2))
    def test_durata_finale_formula(self): self.assertAlmostEqual(67.22/1.3+2,53.707692,places=5)
    def test_srt_utf8_e_timestamp(self):
        out=self.root/"output/test.srt"; write_srt(out,[{"start":0,"end":1.25,"text":"Città."}]); self.assertIn("00:00:01,250",out.read_text())
    def test_qwen_non_avviato(self):
        with mock.patch("storycast.tts._real_generator") as real: self._audio()
        real.assert_not_called()
    def test_asset_immutabili(self):
        before={x:file_hash(x) for x in (self.root/"assets").rglob("*.png")}; self._plan(); self.assertEqual(before,{x:file_hash(x) for x in before})
    def test_video_principale_non_modificato(self):
        main=self.root/"output/storia/storia_video.mp4"; main.parent.mkdir(parents=True); main.write_bytes(b"sentinel"); h=file_hash(main); self._plan(); self.assertEqual(file_hash(main),h)
    def test_pipeline_sequenziale_precheck(self): self.assertEqual(precheck_episode_bundle(self.root,self.main).short_path,self.short)
    def test_render_completo_codec_sync_hold_e_no_doppia_velocita(self):
        with mock.patch("storycast.tts._real_generator") as real:
            first=render_short_video(self.root,self.main,self.slug,mock=True)
            second=render_short_video(self.root,self.main,self.slug,mock=True)
        real.assert_not_called(); self.assertEqual(first["video_codec"],"h264"); self.assertEqual(first["audio_codec"],"aac")
        self.assertTrue(first["checks"]["duration"]); self.assertTrue(first["checks"]["audio_video_sync"])
        self.assertAlmostEqual(first["final_duration"],second["final_duration"],places=3)
        self.assertEqual(second["tts"]["generated"],0); self.assertEqual(second["resolution"],[1080,1920])
    def test_srt_finale_esiste_nel_render(self):
        result=render_short_video(self.root,self.main,self.slug,mock=True); self.assertTrue((self.root/result["subtitles"]).is_file())

    def test_alias_non_modifica_testo_tts_o_timeline(self):
        before=file_hash(self.short); bundle=precheck_episode_bundle(self.root,self.main)
        self.assertIn("Ura Roga",bundle.short_entries[-1]["text"])
        self._audio(); timeline=json.loads(short_paths(self.root,self.slug)["timeline"].read_text())
        self.assertIn("Ura Roga",timeline["entries"][-1]["text"]); self.assertEqual(file_hash(self.short),before)

    def test_alias_compare_in_srt(self):
        self._audio(); paths=short_paths(self.root,self.slug)
        subs=build_subtitles(paths["timeline"],self.short_cfg,1.3); write_srt(paths["subtitles"],subs)
        text=paths["subtitles"].read_text(); self.assertIn("Uraroga",text); self.assertNotIn("Ura Roga",text)

    def test_alias_compare_nei_sottotitoli_renderizzati(self):
        self.assertEqual(apply_display_aliases("Il canale Ura Roga",self.short_cfg),"Il canale Uraroga")
        result=render_short_video(self.root,self.main,self.slug,mock=True)
        ass=(self.root/result["natural_master"]).parent/"natural_subtitles.ass"
        self.assertIn("Uraroga",ass.read_text()); self.assertNotIn("Ura Roga",ass.read_text())


if __name__=="__main__": unittest.main()
