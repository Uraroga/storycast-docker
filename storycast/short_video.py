from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from .core import StorycastError, write_json_atomic
from .episode_bundle import load_pipeline_config, precheck_episode_bundle
from .short_pipeline import run_short_audio, short_list_segments, short_paths, short_status
from .tts import file_hash, wav_info
from .visual_library import inspect_library


def load_short_video_config(root: Path) -> dict[str, Any]:
    cfg = load_pipeline_config(root).get("short", {})
    required = ("resolution", "fps", "video_codec", "audio_codec", "final_speed",
                "final_hold_seconds", "subtitle_style", "vertical_crop", "visual")
    missing = [key for key in required if key not in cfg]
    if missing:
        raise StorycastError(f"Configurazione video Short incompleta: {', '.join(missing)}")
    width, height = cfg["resolution"]
    if (width, height) != (1080, 1920) or width * 16 != height * 9:
        raise StorycastError("La risoluzione Short deve essere 1080x1920 in rapporto 9:16")
    if int(cfg["fps"]) != 30 or float(cfg["final_speed"]) <= 1 or float(cfg["final_hold_seconds"]) < 0:
        raise StorycastError("fps, final_speed o final_hold_seconds Short non validi")
    return cfg


def vertical_crop(width: int, height: int, center_x: float, center_y: float) -> dict[str, int]:
    """Crop massimo 9:16, limitato ai bordi e con coordinate pari per H.264."""
    crop_h = height
    crop_w = min(width, math.floor(height * 9 / 16))
    if crop_w == width:
        crop_h = math.floor(width * 16 / 9)
    crop_w -= crop_w % 2; crop_h -= crop_h % 2
    x = round(center_x * width - crop_w / 2); y = round(center_y * height - crop_h / 2)
    x = max(0, min(x, width - crop_w)); y = max(0, min(y, height - crop_h))
    x -= x % 2; y -= y % 2
    return {"x": x, "y": y, "width": crop_w, "height": crop_h}


def build_vertical_plan(root: Path, timeline_path: Path, output_path: Path) -> dict[str, Any]:
    cfg = load_short_video_config(root)
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    entries = timeline.get("entries", [])
    if not entries:
        raise StorycastError("Timeline Short vuota")
    catalog = inspect_library(root)["assets"]
    speakers = sorted({entry["speaker"] for entry in entries})
    pools = {speaker: [asset for asset in catalog if asset.get("character") == speaker] for speaker in speakers}
    missing = [speaker for speaker, assets in pools.items() if not assets]
    if missing:
        raise StorycastError(f"Asset individuali Short mancanti per: {', '.join(missing)}")
    usage = {asset["id"]: 0 for asset in catalog}; previous = None; scenes = []
    max_seconds = float(cfg["visual"]["max_scene_seconds"])
    motions = ("slow_zoom_in", "static", "slow_zoom_out", "pan_horizontal")
    for entry_position, entry in enumerate(entries):
        speaker = entry["speaker"]
        preferred = [asset for asset in pools[speaker]
                     if asset["function"] in {"speaking_primary", "speaking_alternative"}]
        candidates = preferred or pools[speaker]
        visual_end = (float(entries[entry_position + 1]["start"])
                      if entry_position + 1 < len(entries) else float(entries[-1]["end"]))
        duration = visual_end - float(entry["start"])
        chunks = max(1, math.ceil(duration / max_seconds))
        bounds = [float(entry["start"]) + duration * i / chunks for i in range(chunks + 1)]
        for part in range(chunks):
            available = [asset for asset in candidates if asset["id"] != previous] or candidates
            best = min(usage[asset["id"]] for asset in available)
            available = sorted((asset for asset in available if usage[asset["id"]] == best), key=lambda x: x["id"])
            asset = available[(int(cfg["visual"]["seed"]) + len(scenes)) % len(available)]
            previous = asset["id"]; usage[previous] += 1
            crop_cfg = cfg["vertical_crop"].get(speaker)
            if not crop_cfg:
                raise StorycastError(f"Crop verticale non configurato per {speaker}")
            crop = vertical_crop(*asset["resolution"], crop_cfg["center_x"], crop_cfg["center_y"])
            scenes.append({
                "index": len(scenes) + 1, "speaker": speaker, "utterance_index": entry["index"],
                "start": round(bounds[part], 6), "end": round(bounds[part + 1], 6),
                "duration": round(bounds[part + 1] - bounds[part], 6),
                "asset_id": asset["id"], "source": asset["source"], "pose_type": asset["pose_type"],
                "crop": crop, "movement": motions[len(scenes) % len(motions)],
            })
    total = float(entries[-1]["end"])
    if scenes[-1]["end"] < total:
        scenes[-1]["end"] = total; scenes[-1]["duration"] = total - scenes[-1]["start"]
    plan = {"schema_version": 1, "namespace": "short", "resolution": cfg["resolution"],
            "aspect_ratio": "9:16", "fps": cfg["fps"], "natural_duration": total,
            "timeline": timeline_path.relative_to(root).as_posix(), "scenes": scenes}
    write_json_atomic(output_path, plan)
    return plan


def _chunks(text: str, max_words: int, max_chars_per_line: int = 24) -> list[str]:
    words = text.split()
    if not words:
        return []
    blocks: list[list[str]] = []
    current: list[str] = []
    max_chars = max_chars_per_line * 2
    for word in words:
        candidate = " ".join(current + [word])
        if current and (len(current) >= max_words or len(candidate) > max_chars):
            blocks.append(current); current = [word]
        else:
            current.append(word)
    if current: blocks.append(current)
    result = []
    for block in blocks:
        if len(" ".join(block)) <= max_chars_per_line:
            result.append(" ".join(block)); continue
        choices = [(abs(len(" ".join(block[:i]))-len(" ".join(block[i:]))), i)
                   for i in range(1, len(block))
                   if len(" ".join(block[:i])) <= max_chars_per_line
                   and len(" ".join(block[i:])) <= max_chars_per_line]
        split = min(choices)[1] if choices else max(1, len(block)//2)
        result.append(" ".join(block[:split]) + "\n" + " ".join(block[split:]))
    return result


def apply_display_aliases(text: str, cfg: dict[str, Any]) -> str:
    """Applica grafie solo visuali senza modificare timeline o piano TTS."""
    aliases = cfg.get("display_aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()):
        raise StorycastError("short.display_aliases deve contenere coppie testo:testo")
    displayed = text
    for spoken, visual in aliases.items():
        displayed = displayed.replace(spoken, visual)
    return displayed


def build_subtitles(timeline_path: Path, cfg: dict[str, Any], speed: float = 1.0) -> list[dict[str, Any]]:
    entries = json.loads(timeline_path.read_text(encoding="utf-8"))["entries"]
    style = cfg["subtitle_style"]; result = []
    for entry in entries:
        displayed_text = apply_display_aliases(entry["text"], cfg)
        chunks = _chunks(displayed_text.replace("\n", " "), int(style["max_words"]),
                         int(style.get("max_chars_per_line", 24)))
        duration = float(entry["end"]) - float(entry["start"])
        weights = [max(1, len(chunk.split())) for chunk in chunks]; total_weight = sum(weights)
        cursor = float(entry["start"])
        for position, (chunk, weight) in enumerate(zip(chunks, weights)):
            end = float(entry["end"]) if position == len(chunks) - 1 else cursor + duration * weight / total_weight
            result.append({"index": len(result) + 1, "start": cursor / speed, "end": end / speed,
                           "text": chunk, "lines": chunk.count("\n") + 1})
            cursor = end
    return result


def _srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000)); hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000); secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(path: Path, subtitles: list[dict[str, Any]]) -> None:
    body = "".join(f"{i}\n{_srt_time(item['start'])} --> {_srt_time(item['end'])}\n{item['text']}\n\n"
                   for i, item in enumerate(subtitles, 1))
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body, encoding="utf-8")


def write_ass(path: Path, subtitles: list[dict[str, Any]], cfg: dict[str, Any]) -> None:
    style = cfg["subtitle_style"]; width, height = cfg["resolution"]
    header = ("[Script Info]\nScriptType: v4.00+\n" f"PlayResX: {width}\nPlayResY: {height}\n"
              "WrapStyle: 0\n\n[V4+ Styles]\n"
              "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
              f"Style: Short,{style['font']},{style['font_size']},&H00FFFFFF,&H00FFFFFF,&H00101010,&H78000000,1,0,0,0,100,100,0,0,3,{style['outline']},{style['shadow']},2,{style['margin_left']},{style['margin_right']},{style['margin_bottom']},1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    def stamp(value: float) -> str:
        centis = round(value * 100); hours, centis = divmod(centis, 360000); minutes, centis = divmod(centis, 6000); seconds, centis = divmod(centis, 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"
    event_rows = []
    for item in subtitles:
        text = item["text"].replace("{", "(").replace("}", ")").replace("\n", "\\N")
        event_rows.append(f"Dialogue: 0,{stamp(item['start'])},{stamp(item['end'])},Short,,0,0,0,,{text}\n")
    events = "".join(event_rows)
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(header + events, encoding="utf-8")


def _scene_command(root: Path, scene: dict, cfg: dict, target: Path) -> list[str]:
    width, height = cfg["resolution"]; fps = int(cfg["fps"]); frames = max(1, round(scene["duration"] * fps))
    crop = scene["crop"]; zoom = float(cfg["visual"]["max_zoom"]); movement = scene["movement"]
    if movement == "slow_zoom_in": z = f"min(1+on*({zoom}-1)/{max(frames-1,1)},{zoom})"
    elif movement == "slow_zoom_out": z = f"max({zoom}-on*({zoom}-1)/{max(frames-1,1)},1)"
    else: z = "1.01" if movement == "pan_horizontal" else "1"
    x = f"(iw-iw/zoom)*on/{max(frames-1,1)}" if movement == "pan_horizontal" else "iw/2-iw/zoom/2"
    vf = (f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']},scale={width}:{height}:flags=lanczos,"
          f"zoompan=z='{z}':x='{x}':y='ih/2-ih/zoom/2':d={frames}:s={width}x{height}:fps={fps},format={cfg['pixel_format']}")
    return ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(root / scene["source"]),
            "-vf", vf, "-frames:v", str(frames), "-an", "-c:v", cfg["video_codec"],
            "-preset", cfg["video_preset"], "-crf", str(cfg["video_crf"]), str(target)]


def _probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], check=True, text=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout)


def final_filter(final_speed: float, hold_seconds: float) -> str:
    """Filtro unico: accelera una volta il master naturale, poi aggiunge il hold."""
    # tpad opera prima del retiming: hold*speed diventa esattamente hold dopo setpts.
    padded_hold = hold_seconds * final_speed
    return (f"[0:v]tpad=stop_mode=clone:stop_duration={padded_hold},setpts=PTS/{final_speed}[v];"
            f"[0:a]atempo={final_speed},apad=pad_dur={hold_seconds}[a]")


def _video_signature(root: Path, paths: dict[str, Path], cfg: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({
        "audio_hash": file_hash(paths["audio"]),
        "timeline_hash": file_hash(paths["timeline"]),
        "config": cfg,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _cached_video_result(root: Path, paths: dict[str, Path], cfg: dict[str, Any]) -> dict[str, Any] | None:
    state_path = paths["work"] / "video" / "video_state.json"
    if not all(path.is_file() for path in (state_path, paths["video"], paths["subtitles"],
                                            paths["audio"], paths["timeline"])):
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state.get("status") != "video_ready"
                or state.get("signature") != _video_signature(root, paths, cfg)
                or state.get("video_hash") != file_hash(paths["video"])
                or state.get("subtitles_hash") != file_hash(paths["subtitles"])):
            return None
        probe = _probe(paths["video"])
        video = next(x for x in probe["streams"] if x["codec_type"] == "video")
        audio = next(x for x in probe["streams"] if x["codec_type"] == "audio")
        if ([video["width"], video["height"]] != cfg["resolution"]
                or video["codec_name"] != "h264" or audio["codec_name"] != "aac"):
            return None
    except (OSError, ValueError, KeyError, StopIteration, json.JSONDecodeError,
            subprocess.CalledProcessError):
        return None
    return {**state, "cache_status": "valid"}


def render_short_video(root: Path, main_path: Path, slug: str, *, mock: bool = False,
                       runner=subprocess.run, audio_result: dict[str, Any] | None = None) -> dict[str, Any]:
    bundle = precheck_episode_bundle(root, main_path); cfg = load_short_video_config(root)
    audio_result = audio_result or run_short_audio(root, main_path, slug, mock=mock)
    paths = short_paths(root, slug); work = paths["work"] / "video"
    work.mkdir(parents=True, exist_ok=True); scenes_dir = work / "scenes"; scenes_dir.mkdir(parents=True, exist_ok=True)
    cached = _cached_video_result(root, paths, cfg)
    if cached is not None:
        cached.update(tts=audio_result["tts"], segments=audio_result["segments"])
        return cached
    plan_path = work / "vertical_plan.json"; plan = build_vertical_plan(root, paths["timeline"], plan_path)
    natural_subtitles = build_subtitles(paths["timeline"], cfg); ass_path = work / "natural_subtitles.ass"
    write_ass(ass_path, natural_subtitles, cfg)
    scene_files = []
    for scene in plan["scenes"]:
        target = scenes_dir / f"scene_{scene['index']:04d}.mp4"; command = _scene_command(root, scene, cfg, target)
        key = hashlib.sha256(json.dumps({"scene": scene, "command": command[:-1], "source": file_hash(root/scene["source"])}, sort_keys=True).encode()).hexdigest()
        sidecar = target.with_suffix(".json"); valid = False
        if target.is_file() and sidecar.is_file():
            try: valid = json.loads(sidecar.read_text())["key"] == key and _probe(target)["streams"][0]["codec_name"] == "h264"
            except Exception: valid = False
        if not valid:
            runner(command, check=True); write_json_atomic(sidecar, {"schema_version": 1, "key": key})
        scene_files.append(target)
    concat = work / "concat.txt"; concat.write_text("".join(f"file 'scenes/{path.name}'\n" for path in scene_files), encoding="utf-8")
    silent = work / "natural_silent.mp4"
    runner(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)], check=True)
    natural = work / "natural_master.mp4"; escaped = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    runner(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(paths["audio"]),
            "-vf", f"subtitles='{escaped}'", "-map", "0:v:0", "-map", "1:a:0", "-t", str(plan["natural_duration"]),
            "-c:v", cfg["video_codec"], "-preset", cfg["video_preset"], "-crf", str(cfg["video_crf"]),
            "-pix_fmt", cfg["pixel_format"], "-c:a", cfg["audio_codec"], "-b:a", cfg["audio_bitrate"],
            "-ar", str(wav_info(paths["audio"])["sample_rate"]), "-ac", "1", str(natural)], check=True)
    speed = float(cfg["final_speed"]); hold = float(cfg["final_hold_seconds"])
    accelerated_duration = plan["natural_duration"] / speed; final_duration = accelerated_duration + hold
    runner(["ffmpeg", "-v", "error", "-y", "-i", str(natural), "-filter_complex", final_filter(speed, hold),
            "-map", "[v]", "-map", "[a]", "-t", f"{final_duration:.6f}", "-r", str(cfg["fps"]),
            "-c:v", cfg["video_codec"], "-preset", cfg["video_preset"], "-crf", str(cfg["video_crf"]),
            "-pix_fmt", cfg["pixel_format"], "-c:a", cfg["audio_codec"], "-b:a", cfg["audio_bitrate"],
            "-movflags", "+faststart", str(paths["video"])], check=True)
    final_subtitles = build_subtitles(paths["timeline"], cfg, speed=speed); write_srt(paths["subtitles"], final_subtitles)
    probe = _probe(paths["video"]); video = next(x for x in probe["streams"] if x["codec_type"] == "video"); audio = next(x for x in probe["streams"] if x["codec_type"] == "audio")
    actual = float(probe["format"]["duration"]); video_duration = float(video.get("duration", actual)); audio_duration = float(audio.get("duration", actual))
    fps_num, fps_den = map(int, video["r_frame_rate"].split("/")); actual_fps = fps_num/fps_den
    checks = {"resolution": [video["width"], video["height"]] == cfg["resolution"], "fps": abs(actual_fps-cfg["fps"]) < .01,
              "video_codec": video["codec_name"] == "h264", "audio_codec": audio["codec_name"] == "aac",
              "duration": abs(actual-final_duration) <= 2/cfg["fps"],
              "video_duration": abs(video_duration-final_duration) <= 2/cfg["fps"],
              "audio_duration": abs(audio_duration-final_duration) <= 2/cfg["fps"],
              "audio_video_sync": abs(audio_duration-video_duration) <= 2/cfg["fps"],
              "subtitles_before_hold": not final_subtitles or final_subtitles[-1]["end"] <= accelerated_duration + .01}
    if not all(checks.values()): raise StorycastError(f"Verifica Short fallita: {checks}")
    result = {"schema_version": 1, "status": "video_ready", "audio_status": "audio_ready", "slug": slug,
              "segments": audio_result["segments"], "tts": audio_result["tts"], "natural_duration": plan["natural_duration"],
              "final_speed": speed, "accelerated_duration": accelerated_duration, "final_hold_seconds": hold,
              "final_duration": actual, "video_duration": video_duration, "audio_duration": audio_duration,
              "duration_delta": abs(audio_duration-video_duration), "resolution": cfg["resolution"], "fps": actual_fps,
              "video_codec": video["codec_name"], "audio_codec": audio["codec_name"],
              "video": paths["video"].relative_to(root).as_posix(), "subtitles": paths["subtitles"].relative_to(root).as_posix(),
              "natural_master": natural.relative_to(root).as_posix(), "vertical_plan": plan_path.relative_to(root).as_posix(),
              "checks": checks, "video_hash": file_hash(paths["video"]),
              "subtitles_hash": file_hash(paths["subtitles"]),
              "signature": _video_signature(root, paths, cfg), "cache_status": "rendered"}
    write_json_atomic(work / "video_state.json", result)
    return result


def short_video_status(root: Path, main_path: Path, slug: str, *, mock: bool = False) -> dict[str, Any]:
    base = short_status(root, main_path, slug, mock=mock); cfg = load_short_video_config(root)
    state_path = short_paths(root, slug)["work"] / "video" / "video_state.json"
    try: state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except json.JSONDecodeError: state = {}
    natural = base.get("audio_info", {}).get("duration") if base.get("audio_info") else None
    speed = float(cfg["final_speed"]); hold = float(cfg["final_hold_seconds"])
    base.update({
        "audio_status": base.pop("status"), "video_status": state.get("status", "video_pending"),
        "natural_duration": natural, "final_speed": speed,
        "accelerated_duration": natural / speed if natural is not None else None,
        "final_hold_seconds": hold, "final_duration": state.get("final_duration"),
        "resolution": cfg["resolution"], "fps": cfg["fps"],
        "video_codec": "h264", "audio_codec": cfg["audio_codec"],
        "video": short_paths(root, slug)["video"].relative_to(root).as_posix(),
        "subtitles": short_paths(root, slug)["subtitles"].relative_to(root).as_posix(),
    })
    return base
