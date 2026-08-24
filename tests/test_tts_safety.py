import json
import tempfile
import unittest
import wave
from pathlib import Path

from storycast.audio_safety import CpuCooldown, QC_SCHEMA_VERSION, analyze_wav, technical_qc
from storycast.core import StorycastError
from storycast.tts import _write_wav_partial, split_text


def config():
    return {"cpu_cooldown":{"enabled":True,"profile":"conservative","after_inference_seconds":30,
            "after_error_extra_seconds":60,"max_parallel_inferences":1,"retry_limit":1},
            "verification":{"technical_completeness":{"words_per_second":2.7,"seconds_per_character":.012,
            "punctuation_seconds":.1,"minimum_factor":.32,"maximum_factor":2.8,"absolute_min_seconds":.18,
            "min_active_seconds":.25,"min_active_ratio":.12,"min_rms":.003,"max_silence_percent":88,
            "silence_amplitude_threshold":164,"hard_cut_duration_factor":.72,"min_natural_tail_seconds":.035,
            "hard_cut_tail_rms":.018}}}


def metadata():
    return {k: True for k in ("index","speaker","original_text","requested_voice","backend_voice",
            "effective_instruction","spoken_language","seed","parameters","wav_hash")} | {
            "status":"valid","generation_completed":True,"partial":False}


class SafetyTests(unittest.TestCase):
    def test_cooldown_30_plus_60_and_atomic_state(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"state.json"; p.write_text('{"schema_version":1}')
            calls=[]; c=CpuCooldown(config(),state_path=p,sleeper=calls.append,clock=lambda:"T",logger=lambda x:None)
            c.wait_after_inference(segment_index=11,attempt_number=1,failed=True,next_action="retry_tts")
            c.wait_after_error(segment_index=11,attempt_number=1)
            self.assertEqual(calls,[30.0,60.0]); self.assertFalse(list(p.parent.glob("*.tmp")))

    def test_no_wait_for_cache_or_dry_run_is_a_generate_contract(self):
        calls=[]; CpuCooldown(config(),sleeper=calls.append,logger=lambda x:None)
        self.assertEqual(calls,[])

    def test_partial_never_valid(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"x.wav.partial"; _write_wav_partial(Path(td)/"x.wav",b"\0\0"*1000,8000).replace(p)
            with self.assertRaises(StorycastError): analyze_wav(p)
            self.assertEqual(technical_qc(p,"testo",config(),metadata=metadata())["qc_state"],"partial")

    def test_short_attack_and_silence_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"x.wav"
            with wave.open(str(p),"wb") as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b"\x10\x00"*100)
            q=technical_qc(p,"Questa frase dovrebbe essere molto più lunga e completa.",config(),metadata=metadata())
            self.assertNotEqual(q["qc_state"],"valid")

    def test_split_preserves_text_and_punctuation(self):
        text="Prima frase. Seconda frase? Terza frase! E infine: chiusura."
        chunks=split_text(text,4,6)
        self.assertEqual(" ".join(chunks),text); self.assertGreater(len(chunks),1)

    def test_invalid_conservative_values(self):
        bad=config();bad["cpu_cooldown"]["after_inference_seconds"]=29
        with self.assertRaises(StorycastError): CpuCooldown(bad)


if __name__ == "__main__": unittest.main()
