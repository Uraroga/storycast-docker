from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from storycast.core import StorycastError
from storycast.final_speed import create_speed_version, ffmpeg_speed_command, probe_media, speed_output_path


class FinalSpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.normal=self.root/"episodio.mp4"
        subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=c=blue:s=160x90:r=25:d=2","-f","lavfi","-i","sine=frequency=440:sample_rate=24000:duration=2","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-b:a","64k","-shortest",str(self.normal)],check=True)
    def tearDown(self): self.temp.cleanup()
    def _hash(self): return hashlib.sha256(self.normal.read_bytes()).hexdigest()

    def test_default_speed_keeps_normal_and_verifies_streams(self):
        before=self._hash(); target=speed_output_path(self.normal,1.05)
        result=create_speed_version(self.normal,target,1.05,video_preset="ultrafast",video_crf=30)
        self.assertEqual(target.name,"episodio_speed105.mp4"); self.assertEqual(before,self._hash())
        info=probe_media(target)
        self.assertEqual((info["video"]["codec_name"],info["video"]["pix_fmt"],info["audio"]["codec_name"]),("h264","yuv420p","aac"))
        self.assertAlmostEqual(info["duration"],result["normal"]["duration"]/1.05,delta=.12)
        command=" ".join(result["command"])
        self.assertIn("setpts=PTS/1.05",command); self.assertIn("atempo=1.05",command); self.assertNotIn("asetrate",command)

    def test_custom_suffix_and_command(self):
        target=speed_output_path(self.normal,1.08); self.assertEqual(target.name,"episodio_speed108.mp4")
        self.assertIn("[0:a]atempo=1.08[a]"," ".join(ffmpeg_speed_command(self.normal,target,1.08)))

    def test_missing_audio_refused_without_output(self):
        silent=self.root/"silent.mp4"
        subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=s=160x90:d=1","-c:v","libx264","-pix_fmt","yuv420p",str(silent)],check=True)
        target=speed_output_path(silent,1.05)
        with self.assertRaisesRegex(StorycastError,"privo di flusso audio"): create_speed_version(silent,target,1.05)
        self.assertFalse(target.exists())

    def test_encoding_error_preserves_normal_and_removes_partial(self):
        before=self._hash(); target=speed_output_path(self.normal,1.05)
        def runner(command,**kwargs):
            if command[0]=="ffmpeg": raise subprocess.CalledProcessError(1,command)
            return subprocess.run(command,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError): create_speed_version(self.normal,target,1.05,runner=runner)
        self.assertEqual(before,self._hash()); self.assertFalse(target.exists()); self.assertFalse(list(self.root.glob("*.partial.mp4")))


if __name__=="__main__": unittest.main()
