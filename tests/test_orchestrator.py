from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from storycast.core import StorycastError
from storycast.orchestrator import (StoryLock, cleanup_inventory, config_hashes,
    PHASES, initial_state, load_state, paths, plan_summary, regenerate_story, review_set,
    run_story, safe_input, save_state, segment_list, slug_from)
from storycast.tts import file_hash
from storycast.run_logging import RunLogger


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        for folder in ("input","config/characters","assets/characters/a","assets/characters/b","assets/groups","work","output"): (self.root/folder).mkdir(parents=True,exist_ok=True)
        self._png("assets/a.png","red",320,240); self._png("assets/b.png","blue",320,240)
        (self.root/"config/characters/a.yaml").write_text("id: a\nimmagine_principale: assets/a.png\n")
        (self.root/"config/characters/b.yaml").write_text("id: b\nimmagine_principale: assets/b.png\n")
        voices={"schema_version":1,"voices":{}}
        for char,voice in (("a","Vivian"),("b","Ryan")):
            voices["voices"][char]={"character_id":char,"voice":voice,"language":"Italian","instruction":"Parla chiaramente.","tone":"test","pace":"medium","seed":9001,"enabled":True,"parameters":{"do_sample":False}}
        (self.root/"config/voices.yaml").write_text(json.dumps(voices))
        tts={"schema_version":1,"backend":"real","model":{"id":"test","local_dir":"/missing","revision":"test"},"audio":{"sample_rate":24000,"channels":1,"sample_width":2,"subsegment_pause_seconds":.05,"utterance_pause_seconds":.1,"mock_seconds_per_word":.055},"text":{"max_words":50,"hard_limit_words":70,"prudent_max_words":4,"prudent_hard_limit_words":6},"verification":{"min_duration_seconds":.1,"min_seconds_per_word":.015,"max_seconds_per_word":1.5,"max_silence_percent":95,"max_duration_seconds":45,"duration_outlier_factor":2,"alternate_seed_offset":100003,"expected_backend":"real","available_voices":["Vivian","Ryan","Aiden"]}}
        (self.root/"config/tts.json").write_text(json.dumps(tts))
        specs=[("group","group",None,[],"group"),("a_main","speaking_primary","a",["a"],"talking"),("a_alt","speaking_alternative","a",["a"],"alt"),("a_listen","listening","a",["b"],"listening"),("b_main","speaking_primary","b",["b"],"talking"),("b_alt","speaking_alternative","b",["b"],"alt"),("b_listen","listening","b",["a"],"listening")]
        colors=["green","red","yellow","magenta","blue","cyan","orange"]
        for (ident,function,char,speakers,pose),color in zip(specs,colors):
            folder="assets/groups" if char is None else f"assets/characters/{char}"
            semantic="talking_open_hand" if "alt" in ident else pose
            path=f"{folder}/{ident}_{semantic}.png"; self._png(path,color,320,240)
        library={"schema_version":1,"library_id":"test","derived_root":"work/visual/library_v1/derived","character_root":"assets/characters","group_root":"assets/groups","extensions":[".png",".jpg",".jpeg"],"archive_exclusions":[]}
        (self.root/"config/visual_library.yaml").write_text(json.dumps(library))
        render={"schema_version":1,"video":{"width":160,"height":90,"fps":30,"codec":"libx264","pixel_format":"yuv420p","preset":"ultrafast","crf":30,"faststart":True},"audio":{"codec":"aac","bitrate":"64k","channels":1,"sample_rate":24000},"final_speed_version":{"enabled":True,"factor":1.15,"suffix":"_speed{percent}","video_preset":"ultrafast","video_crf":30,"audio_bitrate":"64k"},"story_images":{"directory":"assets/story_images","extensions":[".png",".jpg",".jpeg",".webp"],"insert_seconds":6.0,"fade_seconds":.4,"opening_guard_seconds":20.0,"closing_guard_seconds":30.0,"target_interval_seconds":60.0,"max_zoom":1.035},"camera":{"max_zoom":1.01},"library_planner":{"minimum_scene_seconds":.15,"opening_seconds":.15,"closing_seconds":.15,"reaction_min_speech_seconds":.3,"reaction_seconds":.15,"alternative_every_occurrences":3,"seed":7,"transform":{"scale_filter":"bilinear","target_resolution":[160,90],"preserve_aspect_ratio":True}}}
        (self.root/"config/render.yaml").write_text(json.dumps(render))
        self.input=self.root/"input/racconto.txt"; self.input.write_text("[a|curiosa]\nUna piccola storia comincia qui.\n\n[b|calmo]\nE continua senza fretta.\n")
        short_cfg={"enabled":True,"status":"video_ready","resolution":[1080,1920],"fps":30,
                   "video_codec":"libx264","pixel_format":"yuv420p","video_preset":"ultrafast","video_crf":30,
                   "audio_codec":"aac","audio_bitrate":"64k","final_speed":1.30,"final_hold_seconds":2.0,
                   "display_aliases":{"Nome Parlato":"NomeVisuale"},
                   "subtitle_style":{"font":"DejaVu Sans","font_size":30,"margin_left":30,"margin_right":30,
                                     "margin_bottom":100,"outline":2,"shadow":1,"max_words":8,"max_chars_per_line":24},
                   "vertical_crop":{"a":{"center_x":.5,"center_y":.5},"b":{"center_x":.5,"center_y":.5}},
                   "visual":{"seed":7,"max_scene_seconds":7.0,"max_zoom":1.01}}
        (self.root/"config/pipeline.json").write_text(json.dumps({"schema_version":1,"execution":"sequential",
            "precheck":{"required_characters":["a","b"]},"short":short_cfg}))
        (self.root/"input/racconto-short.txt").write_text(self.input.read_text())

    def tearDown(self): self.tmp.cleanup()
    def _png(self,rel,color,w,h): subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",f"color={color}:s={w}x{h}","-frames:v","1","-y",str(self.root/rel)],check=True)

    def test_slug_default_explicit_and_rejections(self):
        self.assertEqual(slug_from(None,Path("input/La Stazione.txt")),"la_stazione")
        self.assertEqual(slug_from("nome-2",self.input),"nome-2")
        for bad in ("", "../x", "X", "a/b"): self.assertRaises(StorycastError,slug_from,bad,self.input)

    def test_default_and_explicit_input(self):
        default=self.root/"input/dialogo.txt"; default.write_text(self.input.read_text())
        self.assertEqual(safe_input(self.root,None),default)
        self.assertEqual(safe_input(self.root,"input/racconto.txt"),self.input)
        for bad in ("../x.txt","input/../../etc/passwd"):
            with self.assertRaises(StorycastError): safe_input(self.root,bad)

    def test_plan_dry_run_has_no_workspace_and_dynamic_mapping(self):
        result=plan_summary(self.root,self.input,"racconto",True)
        self.assertEqual(result["voices"],{"a":"Vivian","b":"Ryan"}); self.assertEqual(result["utterances"],2)
        self.assertFalse(paths(self.root,"racconto")["work"].exists())

    def test_resume_selection_from_every_phase(self):
        pp=paths(self.root,"phases")
        for index,phase in enumerate(PHASES):
            state=initial_state("phases","input/racconto.txt",file_hash(self.input),config_hashes(self.root))
            state["completed_phases"]=PHASES[:index]; state["phase"]=phase; save_state(pp["state"],state)
            self.assertEqual(plan_summary(self.root,self.input,"phases",True)["resume_from"],phase)

    def test_atomic_state_and_active_stale_lock(self):
        pp=paths(self.root,"locktest"); state=initial_state("locktest","input/racconto.txt",file_hash(self.input),config_hashes(self.root)); save_state(pp["state"],state)
        self.assertEqual(load_state(pp["state"])["slug"],"locktest"); self.assertFalse(list(pp["work"].glob("state.json.*.tmp")))
        with StoryLock(pp["lock"]):
            with self.assertRaisesRegex(StorycastError,"già in esecuzione"): StoryLock(pp["lock"]).__enter__()
        pp["lock"].write_text(json.dumps({"pid":999999,"hostname":socket.gethostname(),"process_start":"x"}))
        with StoryLock(pp["lock"]): pass
        self.assertTrue(list(pp["work"].glob("run.lock.stale_*")))

    def test_mock_end_to_end_outputs_and_second_run_cache(self):
        first=run_story(self.root,self.input,"uno",mock=True)
        pp=paths(self.root,"uno")
        self.assertEqual(first["status"],"verified"); self.assertTrue(pp["audio"].is_file() and pp["video"].is_file())
        self.assertTrue(pp["short_audio"].is_file() and pp["short_video"].is_file())
        self.assertEqual(first["short"]["status"],"completed")
        self.assertEqual(load_state(pp["state"])["package"],"completed")
        self.assertEqual(first["speed_factor"],1.15)
        self.assertEqual(first["short"]["video"]["final_speed"],1.30)
        speed=pp["video"].with_name(pp["video"].stem+"_speed115.mp4"); self.assertTrue(speed.is_file())
        self.assertEqual(first["video_speed"],speed.relative_to(self.root).as_posix())
        before=(file_hash(pp["audio"]),file_hash(pp["video"])); second=run_story(self.root,self.input,"uno",mock=True)
        self.assertEqual(second["status"],"cached"); self.assertEqual(second["tts_generated"],0); self.assertEqual(second["tts_cached"],2)
        self.assertEqual(before,(file_hash(pp["audio"]),file_hash(pp["video"])))
        self.assertEqual(second["short"]["video"]["cache_status"],"valid")

    def test_short_failure_preserves_main_and_marks_partial(self):
        with mock.patch("storycast.orchestrator.render_short_video",side_effect=StorycastError("errore short sintetico")):
            with self.assertRaisesRegex(StorycastError,"EPISODIO PRINCIPALE: OK"):
                run_story(self.root,self.input,"partial",mock=True)
        pp=paths(self.root,"partial"); state=load_state(pp["state"])
        self.assertTrue(pp["video"].is_file())
        self.assertEqual((state["main_episode"],state["short_video"],state["package"]),("completed","error","partial"))

    def test_log_sintetico_rispetta_ordine_integrato(self):
        with mock.patch("builtins.print") as output:
            run_story(self.root,self.input,"ordine",mock=True)
        rows=[str(call.args[0]) for call in output.call_args_list if call.args
              and str(call.args[0]).startswith("[storycast]")
              and not str(call.args[0]).startswith("[storycast] immagini storia")
              and not str(call.args[0]).startswith("[storycast] continuazione senza immagini")]
        expected=["[storycast] precheck input","[storycast] episodio principale",
                  "[storycast] controllo episodio principale","[storycast] short audio",
                  "[storycast] short video","[storycast] controlli finali","[storycast] completato"]
        self.assertEqual(rows,expected)

    def test_speed_cli_override_and_disable(self):
        custom=run_story(self.root,self.input,"speed108",mock=True,final_speed=1.08)
        self.assertTrue((self.root/custom["video_speed"]).name.endswith("_speed108.mp4"))
        disabled=run_story(self.root,self.input,"normal-only",mock=True,no_speed_version=True)
        pp=paths(self.root,"normal-only")
        self.assertIsNone(disabled["video_speed"]); self.assertTrue(pp["video"].is_file())
        self.assertFalse(pp["video"].with_name(pp["video"].stem+"_speed115.mp4").exists())

    def test_alias_velocita_override_ha_precedenza(self):
        from storycast.orchestrator import parser
        args=parser().parse_args(["genera","input/racconto.txt","--nome","prova","--velocita","1.20"])
        self.assertEqual(args.final_speed,1.20)

    def test_story_images_integrate_main_invalidate_main_only(self):
        folder=self.root/"assets/story_images"; folder.mkdir()
        self._png("assets/story_images/01.png","white",320,240)
        render=json.loads((self.root/"config/render.yaml").read_text())
        render["story_images"].update(insert_seconds=.2,fade_seconds=.04,opening_guard_seconds=0,
                                      closing_guard_seconds=0,target_interval_seconds=60)
        (self.root/"config/render.yaml").write_text(json.dumps(render))
        first=run_story(self.root,self.input,"immagini",mock=True,story_images="yes")
        pp=paths(self.root,"immagini"); short_hash=file_hash(pp["short_video"])
        plan=json.loads(pp["visual_plan"].read_text())
        self.assertEqual(len(plan["story_images"]),1)
        self.assertEqual(first["short"]["video"]["final_speed"],1.30)
        self._png("assets/story_images/02.png","black",320,240)
        second=run_story(self.root,self.input,"immagini",mock=True,story_images="yes")
        self.assertEqual(second["tts"]["generated"],0)
        self.assertEqual(file_hash(pp["short_video"]),short_hash)
        self.assertEqual(load_state(pp["state"])["story_images_count"],1)

    def test_mock_pipeline_writes_synthetic_run_log(self):
        log=RunLogger(self.root,"logging",self.input.relative_to(self.root).as_posix(),console=False)
        with mock.patch("builtins.print") as console:
            result=run_story(self.root,self.input,"logging",mock=True,run_log=log)
        text=log.path.read_text(encoding="utf-8")
        for expected in ("Avvio Storycast","Dialogo validato","Libreria visiva","Avvio generazione TTS","TTS completato","Timeline audio completata","Timeline visiva completata","Avvio montaggio video","Video completato","Output:","Durata audio:","Elaborazione completata con successo"):
            self.assertIn(expected,text)
        self.assertNotIn("Una piccola storia comincia qui",text)
        self.assertNotIn("frame=",text)
        self.assertIn(f"VIDEO scene_totali={result['visual']['scenes']}",text)
        self.assertEqual(result["visual"]["scenes"], text.count("VIDEO scena="))
        progress=[call for call in console.call_args_list if call.args and str(call.args[0]).startswith("[VIDEO]")]
        self.assertTrue(progress)
        self.assertTrue(all(call.kwargs.get("flush") is True for call in progress))
        self.assertEqual(result["status"],"verified")

    def test_isolation_and_slug_hash_conflict(self):
        run_story(self.root,self.input,"uno",mock=True); other=self.root/"input/altro.txt"; other.write_text("[a|calmo]\nTesto diverso.\n\n[b|calmo]\nRisposta.\n")
        (self.root/"input/altro-short.txt").write_text(other.read_text())
        run_story(self.root,other,"due",mock=True)
        self.assertTrue(paths(self.root,"uno")["video"].is_file()); self.assertTrue(paths(self.root,"due")["video"].is_file())
        with self.assertRaisesRegex(StorycastError,"input diverso"): run_story(self.root,other,"uno",mock=True)

    def test_review_mode_approval_and_resume(self):
        result=run_story(self.root,self.input,"review",mock=True,review_audio=True)
        self.assertEqual(result["status"],"awaiting_review"); self.assertFalse(paths(self.root,"review")["audio"].exists())
        review_set(self.root,"review",1,"approved"); review_set(self.root,"review",2,"approved")
        resumed=run_story(self.root,self.input,"review",mock=True,review_audio=True)
        self.assertEqual(resumed["status"],"verified")

    def test_selective_regeneration_backup_alternate_and_other_hash(self):
        run_story(self.root,self.input,"regen",mock=True); before={x["index"]:x["sha256"] for x in segment_list(self.root,"regen")}
        dry=regenerate_story(self.root,"regen",1,alternate=True,dry_run=True); self.assertTrue(dry["dry_run"])
        result=regenerate_story(self.root,"regen",1,alternate=True); after={x["index"]:x["sha256"] for x in segment_list(self.root,"regen")}
        self.assertEqual(before[2],after[2]); self.assertTrue((self.root/result["backup"]).is_dir())
        meta=json.loads(next(paths(self.root,"regen")["segment_metadata"].glob("0001_*.json")).read_text()); self.assertTrue(meta["alternate_seed"])

    def test_interruption_resume_and_scene_cache(self):
        run_story(self.root,self.input,"resume",mock=True); pp=paths(self.root,"resume"); state=load_state(pp["state"])
        state["phase"]="failed"; state["final_status"]="failed"; state["video_sha256"]="invalid"; save_state(pp["state"],state)
        # Simula output finale incompleto lasciando scene e WAV validi.
        pp["video"].write_bytes(b"partial")
        result=run_story(self.root,self.input,"resume",mock=True)
        self.assertEqual(result["status"],"verified"); self.assertEqual(result["tts"]["generated"],0)
        self.assertGreater(result["visual"]["render"]["cached_scenes"],0)

    def test_cleanup_classification_never_crosses_story(self):
        run_story(self.root,self.input,"clean",mock=True); inventory=cleanup_inventory(self.root,"clean")
        self.assertTrue({"essential","valid_wav","metadata","final_output","temporary_scenes"}.issubset({x["category"] for x in inventory["files"]}))
        self.assertTrue(all("work/episodes/clean/" in x["path"] or "output/clean/" in x["path"] for x in inventory["files"]))

    def test_temporary_third_character(self):
        self._png("assets/c.png","white",320,240); (self.root/"config/characters/c.yaml").write_text("id: c\nimmagine_principale: assets/c.png\n")
        (self.root/"assets/characters/c").mkdir(); self._png("assets/characters/c/c_talking.png","white",320,240)
        voices=json.loads((self.root/"config/voices.yaml").read_text()); voices["voices"]["c"]={"character_id":"c","voice":"Aiden","language":"Italian","instruction":"Parla.","tone":"test","pace":"medium","seed":9,"enabled":True,"parameters":{"do_sample":False}}; (self.root/"config/voices.yaml").write_text(json.dumps(voices))
        third=self.root/"input/terzo.txt"; third.write_text("[a|calmo]\nIntroduzione.\n\n[b|calmo]\nPassaggio.\n\n[c|calmo]\nIl terzo personaggio è configurato.\n")
        (self.root/"input/terzo-short.txt").write_text("[a|calmo]\nIntroduzione.\n\n[b|calmo]\nPassaggio.\n")
        result=plan_summary(self.root,third,"terzo",True); self.assertEqual(result["voices"]["c"],"Aiden")


if __name__=="__main__": unittest.main()
