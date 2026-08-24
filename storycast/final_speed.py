from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from .core import StorycastError

Runner = Callable[..., subprocess.CompletedProcess]


def _factor_text(factor: float) -> str:
    return f"{factor:.6f}".rstrip("0").rstrip(".")


def validate_factor(factor: float) -> float:
    factor = float(factor)
    if not 1.0 < factor <= 2.0:
        raise StorycastError("La velocità finale deve essere maggiore di 1.0 e non superiore a 2.0")
    return factor


def speed_suffix(factor: float, template: str = "_speed{percent}") -> str:
    factor = validate_factor(factor)
    percent = str(round(factor * 100))
    suffix = template.format(percent=percent, factor=_factor_text(factor).replace(".", ""))
    if not suffix or "/" in suffix or "\\" in suffix:
        raise StorycastError("Suffisso della versione accelerata non valido")
    return suffix


def speed_output_path(normal_path: Path, factor: float, template: str = "_speed{percent}") -> Path:
    return normal_path.with_name(normal_path.stem + speed_suffix(factor, template) + normal_path.suffix)


def probe_media(path: Path, runner: Runner = subprocess.run) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise StorycastError(f"Video normale mancante o vuoto: {path}")
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    try:
        completed = runner(command, check=True, capture_output=True, text=True)
        data = json.loads(completed.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        raise StorycastError(f"Video non valido o non leggibile con ffprobe: {path}") from exc
    streams = data.get("streams", [])
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    audio = next((x for x in streams if x.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0)
    return {"duration": duration, "video": video, "audio": audio, "raw": data}


def ffmpeg_speed_command(normal_path: Path, target_path: Path, factor: float, *,
                         video_codec: str = "libx264", video_preset: str = "veryfast",
                         video_crf: int = 20, pixel_format: str = "yuv420p",
                         audio_codec: str = "aac", audio_bitrate: str = "96k") -> list[str]:
    value = _factor_text(validate_factor(factor))
    return ["ffmpeg", "-v", "error", "-y", "-i", str(normal_path), "-filter_complex",
            f"[0:v]setpts=PTS/{value}[v];[0:a]atempo={value}[a]", "-map", "[v]", "-map", "[a]",
            "-c:v", video_codec, "-preset", video_preset, "-crf", str(video_crf),
            "-pix_fmt", pixel_format, "-c:a", audio_codec, "-b:a", audio_bitrate,
            "-movflags", "+faststart", "-shortest", str(target_path)]


def _verify_result(normal: dict[str, Any], accelerated: dict[str, Any], factor: float) -> None:
    video, audio = accelerated["video"], accelerated["audio"]
    if not video or not audio:
        raise StorycastError("Versione accelerata priva di flusso video o audio")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p" or audio.get("codec_name") != "aac":
        raise StorycastError("Versione accelerata non conforme: richiesti H.264, yuv420p e AAC")
    expected = normal["duration"] / factor
    tolerance = max(0.12, expected * 0.015)
    if accelerated["duration"] >= normal["duration"] or abs(accelerated["duration"] - expected) > tolerance:
        raise StorycastError(f"Durata accelerata incoerente: {accelerated['duration']:.3f}s, attesa circa {expected:.3f}s")
    vd = float(video.get("duration") or accelerated["duration"])
    ad = float(audio.get("duration") or accelerated["duration"])
    if abs(vd - ad) > max(0.12, 2 / float(video.get("avg_frame_rate", "25/1").split("/")[0] or 25)):
        raise StorycastError(f"Flussi audio/video non sincronizzati: video={vd:.3f}s audio={ad:.3f}s")


def create_speed_version(normal_path: Path, accelerated_path: Path, factor: float, *, overwrite: bool = False,
                         video_codec: str = "libx264", video_preset: str = "veryfast", video_crf: int = 20,
                         pixel_format: str = "yuv420p", audio_codec: str = "aac", audio_bitrate: str = "96k",
                         runner: Runner = subprocess.run) -> dict[str, Any]:
    normal = probe_media(normal_path, runner)
    if not normal["video"]:
        raise StorycastError(f"Video normale privo di flusso video: {normal_path}")
    if not normal["audio"]:
        raise StorycastError(f"Video normale privo di flusso audio: {normal_path}")
    if accelerated_path.resolve() == normal_path.resolve():
        raise StorycastError("Il percorso accelerato non può coincidere con il video normale")
    if accelerated_path.exists() and not overwrite:
        existing = probe_media(accelerated_path, runner)
        _verify_result(normal, existing, factor)
        return {"status": "cached", "normal": normal, "accelerated": existing, "path": accelerated_path}
    accelerated_path.parent.mkdir(parents=True, exist_ok=True)
    partial = accelerated_path.with_name(accelerated_path.stem + ".partial" + accelerated_path.suffix)
    command = ffmpeg_speed_command(normal_path, partial, factor, video_codec=video_codec,
        video_preset=video_preset, video_crf=video_crf, pixel_format=pixel_format,
        audio_codec=audio_codec, audio_bitrate=audio_bitrate)
    try:
        runner(command, check=True, capture_output=True, text=True)
        accelerated = probe_media(partial, runner)
        _verify_result(normal, accelerated, factor)
        os.replace(partial, accelerated_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return {"status": "created", "normal": normal, "accelerated": accelerated, "path": accelerated_path, "command": command}
