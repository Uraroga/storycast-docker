from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from storycast.core import StorycastError, load_characters
from storycast.tts import (build_plan, cache_status, generate, load_tts_config,
                           load_voices, set_instruction_profile)
from storycast.orchestrator import _story_profile


class InstructionProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        (self.root/"config/characters").mkdir(parents=True); (self.root/"assets").mkdir()
        for char in ("a","b","c"):
            (self.root/f"assets/{char}.png").write_bytes(b"png")
            (self.root/f"config/characters/{char}.yaml").write_text(f"id: {char}\nimmagine_principale: assets/{char}.png\n")
        profiles={"english_default":{"instruction_language":"English","spoken_language":"Italian","emotion_template":" Emotion: {emotion}.","unknown_emotion_value":"neutral"},"italian_legacy":{"instruction_language":"Italian","spoken_language":"Italian","emotion_template":" Emozione: {emotion}.","unknown_emotion_value":"neutra"}}
        voices={}
        for char,name in (("a","Vivian"),("b","Ryan"),("c","Aiden")):
            voices[char]={"character_id":char,"voice":name,"tone":"x","pace":"medium","seed":9,"enabled":True,"parameters":{"do_sample":False},"instructions":{"english_default":f"Speak Italian as {name}. Do not read instructions.","italian_legacy":f"Parla in italiano come {name}."}}
        data={"schema_version":2,"instruction_profile":"english_default","instruction_profiles":profiles,"emotion_mappings":{"english_default":{"curiosa":"curious"},"italian_legacy":{"curiosa":"curiosa"}},"voices":voices}
        (self.root/"config/voices.yaml").write_text(json.dumps(data))
        tts={"backend":"mock","model":{"id":"mock","revision":"1","local_dir":"/none"},"audio":{"sample_rate":24000,"subsegment_pause_seconds":.05,"mock_seconds_per_word":.05},"text":{"max_words":30,"hard_limit_words":40},"verification":{"min_duration_seconds":.01,"min_seconds_per_word":.001,"max_seconds_per_word":2,"max_silence_percent":99}}
        (self.root/"config/tts.json").write_text(json.dumps(tts)); self.config=load_tts_config(self.root)
        self.characters=load_characters(self.root)
        for folder in ("work/audio_segments","work/metadata/audio_segments"): (self.root/folder).mkdir(parents=True)

    def tearDown(self): self.temp.cleanup()

    def plan(self,profile="english_default",emotion="curiosa",text="Questo testo italiano resta invariato."):
        voices=load_voices(self.root,self.characters,instruction_profile=profile)
        return build_plan(self.root,[{"index":1,"speaker":"a","text":text,"emotion":emotion,"pause":None}],voices,self.config)[0]

    def test_default_legacy_mapping_and_languages(self):
        english=self.plan(); legacy=self.plan("italian_legacy")
        self.assertEqual((english["voice"],english["spoken_language"]),("Vivian","Italian"))
        self.assertEqual(english["instruction_language"],"English"); self.assertIn("curious",english["instruction"])
        self.assertEqual(legacy["instruction_language"],"Italian"); self.assertIn("curiosa",legacy["instruction"])
        self.assertEqual(english["text"],"Questo testo italiano resta invariato.")

    def test_unknown_emotion_is_neutral_not_arbitrary_translation(self):
        item=self.plan(emotion="indescrivibile")
        self.assertEqual(item["emotion_original"],"indescrivibile"); self.assertEqual(item["emotion_instruction_value"],"neutral")
        self.assertNotIn("indescrivibile",item["instruction"])

    def test_instruction_is_separate_and_hash_invalidates_only_on_change(self):
        first=self.plan(); generate(self.root,[first],self.config,"mock")
        unchanged=self.plan(); self.assertEqual(cache_status(unchanged),"valid")
        legacy=self.plan("italian_legacy"); self.assertEqual(cache_status(legacy),"regenerate")
        self.assertNotIn(first["instruction"],first["text"]); self.assertNotEqual(first["voice_config_hash"],legacy["voice_config_hash"])

    def test_profile_validation_switch_and_third_character(self):
        self.assertEqual(set_instruction_profile(self.root,"italian_legacy")["instruction_profile"],"italian_legacy")
        self.assertEqual(load_voices(self.root,self.characters)["c"]["voice"],"Aiden")
        with self.assertRaisesRegex(StorycastError,"Profilo inesistente"): set_instruction_profile(self.root,"arbitrario")

    def test_metadata_records_profile_languages_and_original_text(self):
        item=self.plan(); generate(self.root,[item],self.config,"mock")
        metadata=json.loads(item["metadata_path"].read_text())
        self.assertEqual(metadata["instruction_profile"],"english_default")
        self.assertEqual(metadata["instruction_language"],"English"); self.assertEqual(metadata["spoken_language"],"Italian")
        self.assertEqual(metadata["original_text"],item["text"]); self.assertEqual(metadata["emotion_original"],"curiosa")

    def test_story_resume_keeps_recorded_profile_and_legacy_fallback(self):
        self.assertEqual(_story_profile(self.root,{"instruction_profile":"italian_legacy"}),("italian_legacy",False))
        self.assertEqual(_story_profile(self.root,{"schema_version":1}),("italian_legacy",True))
        set_instruction_profile(self.root,"italian_legacy")
        self.assertEqual(_story_profile(self.root,{"instruction_profile":"english_default"}),("english_default",False))


if __name__=="__main__": unittest.main()
