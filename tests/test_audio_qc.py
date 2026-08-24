import copy, json, shutil, tempfile, unittest, wave
from pathlib import Path
from unittest import mock
from storycast.audio_qc import generate_review_page, load_states, migrate_metadata, qc, recovery_scan, regenerate_segment, segment_inventory, set_state
from storycast.core import load_characters, parse_dialogue
from storycast.tts import build_plan, generate, load_voices, merge_audio, wav_info

class AudioQCTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        for d in ("config/characters","assets/a","assets/b","input","work/episode_01/audio_segments","work/episode_01/metadata/audio_segments","work/timeline","output"): (self.root/d).mkdir(parents=True,exist_ok=True)
        for c in ("a","b"):
            (self.root/f"assets/{c}/master.png").write_bytes(b"png")
            (self.root/f"config/characters/{c}.yaml").write_text(f'id: {c}\nimmagine_principale: "assets/{c}/master.png"\n')
        voices={"voices":{"a":{"character_id":"a","voice":"Vivian","language":"Italian","instruction":"Chiara.","tone":"x","pace":"medium","seed":7,"enabled":True,"parameters":{}},"b":{"character_id":"b","voice":"Ryan","language":"Italian","instruction":"Calma.","tone":"x","pace":"medium","seed":8,"enabled":True,"parameters":{}}}}
        (self.root/"config/voices.yaml").write_text(json.dumps(voices))
        (self.root/"input/episode.txt").write_text("[a|felice|pausa=0.2]\nUna battuta breve.\n\n[b]\nSeconda battuta di prova.\n")
        self.config={"backend":"mock","model":{"id":"mock","revision":"1","local_dir":"/none"},"audio":{"sample_rate":24000,"channels":1,"sample_width":2,"subsegment_pause_seconds":.1,"utterance_pause_seconds":.2,"mock_seconds_per_word":.06},"text":{"max_words":8,"hard_limit_words":12,"prudent_max_words":3,"prudent_hard_limit_words":5},"verification":{"min_duration_seconds":.05,"min_seconds_per_word":.01,"max_seconds_per_word":2,"max_silence_percent":99,"max_duration_seconds":20,"duration_outlier_factor":2,"alternate_seed_offset":100}}
        chars=load_characters(self.root); vs=load_voices(self.root,chars); entries=parse_dialogue(self.root/"input/episode.txt",chars)
        self.plan=build_plan(self.root,entries,vs,self.config)
        for x in self.plan:
            stem=f"{x['index']:04d}_{x['speaker']}"; x["wav_path"]=self.root/"work/episode_01/audio_segments"/f"{stem}.wav"; x["metadata_path"]=self.root/"work/episode_01/metadata/audio_segments"/f"{stem}.json"
        generate(self.root,self.plan,self.config,"mock")
        for x in self.plan:
            m=json.loads(x["metadata_path"].read_text()); m.update(character=x["speaker"],requested_voice=x["voice"],backend_voice=x["voice"],effective_instruction=x["instruction"],config_hash=x["voice_config_hash"],backend="real"); x["metadata_path"].write_text(json.dumps(m))
        migrate_metadata(self.root,self.plan,dry_run=False)
    def tearDown(self): self.tmp.cleanup()
    def test_pagina_html_mapping_e_comandi(self):
        result=generate_review_page(self.root,self.plan,self.config); page=(self.root/result["page"]).read_text()
        self.assertEqual(page.count("<audio controls"),2); self.assertIn("../work/episode_01/audio_segments/0001_a.wav",page)
        self.assertIn("episode-01-regenerate-segment 2",page)
    def test_elenco_ordinato_e_associazione(self):
        items=segment_inventory(self.root,self.plan,self.config)
        self.assertEqual([x["index"] for x in items],[1,2])
        self.assertEqual((items[1]["speaker"],items[1]["original_text"],items[1]["wav_path"]),("b","Seconda battuta di prova.","work/episode_01/audio_segments/0002_b.wav"))
    def test_stato_manuale_non_approva_automaticamente(self):
        self.assertEqual(load_states(self.root,self.plan)["segments"]["1"]["review_state"],"pending_review")
        set_state(self.root,self.plan,1,"approved"); self.assertEqual(load_states(self.root,self.plan)["segments"]["1"]["review_state"],"approved")
        set_state(self.root,self.plan,2,"rejected"); self.assertEqual(load_states(self.root,self.plan)["segments"]["2"]["review_state"],"rejected")
    def test_rigenerazione_selettiva_backup_hash_e_seed_alternativo(self):
        other=self.plan[1]["wav_path"].read_bytes()
        before={x["index"]:x["wav_path"].read_bytes() for x in self.plan}
        dry=regenerate_segment(self.root,self.plan,self.config,1,True,True,True,"mock"); self.assertEqual(dry["seed"],107)
        self.assertFalse(dry["model_loaded"]); self.assertEqual(dry["files_modified"],[])
        self.assertEqual(before,{x["index"]:x["wav_path"].read_bytes() for x in self.plan})
        result=regenerate_segment(self.root,self.plan,self.config,1,False,True,True,"mock")
        self.assertTrue((self.root/result["backup"]/self.plan[0]["wav_path"].name).is_file())
        self.assertEqual(other,self.plan[1]["wav_path"].read_bytes()); self.assertTrue(result["other_segments_unchanged"])
        meta=json.loads(self.plan[0]["metadata_path"].read_text()); self.assertTrue(meta["alternate_seed"]); self.assertEqual(meta["seed"],107)
    def test_indice_inesistente_rifiutato(self):
        with self.assertRaisesRegex(Exception,"Indice battuta inesistente"):
            regenerate_segment(self.root,self.plan,self.config,99,True,backend="mock")
    def test_seed_alternativo_e_hash_vocale_indipendente(self):
        item=self.plan[0]; meta=json.loads(item["metadata_path"].read_text())
        voice_hash_default=meta["voice_config_hash"]; meta["seed"]=107; meta["alternate_seed"]=True
        item["metadata_path"].write_text(json.dumps(meta)); migrate_metadata(self.root,self.plan,dry_run=False)
        migrated=json.loads(item["metadata_path"].read_text()); report=qc(self.root,self.plan,self.config)
        row=next(x for x in report["segments"] if x["index"]==1)
        self.assertEqual(self.plan[0]["seed"],7); self.assertEqual(migrated["seed_origin"]["default_seed"],7)
        self.assertEqual(migrated["seed_origin"]["effective_seed"],107); self.assertEqual(migrated["voice_config_hash"],voice_hash_default)
        self.assertFalse(any("seed" in x for x in row["metadata_errors"]))
    def test_migrazione_legacy_deterministica_e_wav_invariato(self):
        item=self.plan[0]; meta=json.loads(item["metadata_path"].read_text())
        for key in ("character","requested_voice","backend_voice","effective_instruction","config_hash","generation_config_hash","effective_generation_hash","seed_origin"): meta.pop(key,None)
        meta["schema_version"]=1; item["metadata_path"].write_text(json.dumps(meta)); before=item["wav_path"].read_bytes()
        first=migrate_metadata(self.root,self.plan,True); second=migrate_metadata(self.root,self.plan,True)
        self.assertEqual(first["segments"][0]["fields"],second["segments"][0]["fields"])
        migrate_metadata(self.root,self.plan,False); migrated=json.loads(item["metadata_path"].read_text())
        self.assertEqual(before,item["wav_path"].read_bytes()); self.assertTrue(migrated["metadata_migrated"])
        self.assertIn("metadata_migration_fields",migrated); self.assertEqual(migrated["metadata_previous_schema_version"],1)
    def test_approvazione_conservata_e_invalidata_su_hash(self):
        set_state(self.root,self.plan,1,"approved"); qc(self.root,self.plan,self.config)
        self.assertEqual(load_states(self.root,self.plan)["segments"]["1"]["review_state"],"approved")
        path=self.plan[0]["wav_path"]; data=bytearray(path.read_bytes()); data[-1]^=1; path.write_bytes(data)
        self.assertEqual(load_states(self.root,self.plan)["segments"]["1"]["review_state"],"pending_review")
    def test_qc_analizza_audio_con_warning_metadata(self):
        item=self.plan[0]; meta=json.loads(item["metadata_path"].read_text()); meta.pop("requested_voice"); item["metadata_path"].write_text(json.dumps(meta))
        row=next(x for x in qc(self.root,self.plan,self.config)["segments"] if x["index"]==1)
        self.assertIsNotNone(row["audio"]); self.assertEqual(row["qc_state"],"warning"); self.assertIn("metadata_requested_voice_mancante_migrabile",row["metadata_warnings"])
    def test_strict_distingue_warning_migrabile_da_errore(self):
        item=self.plan[0]; meta=json.loads(item["metadata_path"].read_text()); meta.pop("requested_voice"); item["metadata_path"].write_text(json.dumps(meta))
        for x in self.plan: set_state(self.root,self.plan,x["index"],"approved")
        report=qc(self.root,self.plan,self.config,strict=True)
        self.assertEqual(report["technical_failed"],[]); self.assertEqual(report["status"],"passed")
    def test_migrazione_supporta_terzo_personaggio(self):
        item=copy.deepcopy(self.plan[0]); item.update(index=3,speaker="gamma",voice="Aiden")
        item["wav_path"]=self.root/"work/episode_01/audio_segments/0003_gamma.wav"; item["metadata_path"]=self.root/"work/episode_01/metadata/audio_segments/0003_gamma.json"
        shutil.copy2(self.plan[0]["wav_path"],item["wav_path"]); meta=json.loads(self.plan[0]["metadata_path"].read_text())
        meta.update(index=3,speaker="gamma",character="gamma",voice="Aiden",requested_voice="Aiden",backend_voice="Aiden",wav_hash=wav_info(item["wav_path"])["wav_hash"])
        item["metadata_path"].write_text(json.dumps(meta)); migrate_metadata(self.root,[item],False)
        self.assertEqual(json.loads(item["metadata_path"].read_text())["character"],"gamma")
    def test_durata_anomala_e_blocco_strict(self):
        p=self.plan[1]["wav_path"]
        with wave.open(str(p),"wb") as w: w.setparams((1,2,24000,240000,"NONE","")); w.writeframes((b"\xe8\x03")*240000)
        m=json.loads(self.plan[1]["metadata_path"].read_text()); m["wav_hash"]=wav_info(p)["wav_hash"]; m["duration"]=10; self.plan[1]["metadata_path"].write_text(json.dumps(m))
        report=qc(self.root,self.plan,self.config,strict=True)
        self.assertEqual(report["status"],"blocked"); self.assertEqual(next(x for x in report["segments"] if x["index"]==2)["qc_state"],"warning")
    def test_merge_successivo_aggiorna_audio_e_timeline(self):
        manifest=merge_audio(self.root,self.plan,self.config,output_rel="output/full.wav",manifest_rel="work/manifest.json",timeline_rel="work/timeline/full.json",input_path=self.root/"input/episode.txt")
        self.assertTrue((self.root/"output/full.wav").is_file()); self.assertEqual(len(json.loads((self.root/"work/timeline/full.json").read_text())["entries"]),2)
        self.assertGreater(manifest["total_duration"],0)
    def test_recupero_file_temporanei_non_cancella(self):
        p=self.root/"work/episode_01/audio_segments/prova.wav.tmp"; p.write_bytes(b"incompleto")
        scan=recovery_scan(self.root); self.assertIn("work/episode_01/audio_segments/prova.wav.tmp",scan["incomplete_files"]); self.assertTrue(p.exists())
    def test_rebuild_delega_merge_render_verifica_dopo_backup(self):
        from storycast import episode
        with mock.patch.object(episode,"context",return_value=(self.config,[],self.plan)), mock.patch("storycast.audio_qc.qc",return_value={"status":"passed","needs_review":[]}), mock.patch("storycast.audio_qc.backup_outputs",return_value=self.root/"backup"), mock.patch.object(episode,"audio",return_value={"wav_hash":"a"}), mock.patch.object(episode,"render",return_value={"status":"rendered"}), mock.patch.object(episode,"check",return_value={"video_sha256":"v"}):
            (self.root/"backup").mkdir(); result=episode.rebuild_after_segment(self.root,1); self.assertEqual(result["video_hash"],"v")
if __name__=="__main__": unittest.main()
