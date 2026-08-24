from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .core import StorycastError, write_json
from .story_images import apply_story_inserts, load_story_image_config
from .visual import image_size, load_json_config, probe, sha256

CATALOG = Path("config/visual_library.yaml")
TIMELINE = Path("work/timeline/storycast_episode_01_timeline.json")
AUDIO = Path("output/storycast_episode_01_audio.wav")
DERIVED = Path("work/visual/library_v1/derived")
MANIFEST = Path("work/visual/library_v1/visual_library_manifest.json")
SHOT_PLAN = Path("work/visual/storycast_episode_01_library_v1_shot_plan.json")
SCENES = Path("work/visual/library_v1/scenes")
FINAL_VIDEO = Path("output/storycast_episode_01_video_library_v1.mp4")
FRAME_DIR = Path("work/visual/verification_frames_library_v1")
VERIFY_MANIFEST = Path("work/visual/library_v1/video_verification.json")
SCENE_MANIFEST = Path("work/visual/library_v1/scene_asset_manifest.json")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _tokens(path: Path) -> set[str]:
    return set(filter(None, re.split(r"[^a-z0-9]+", path.stem.lower())))

def _classify(path: Path, character: str | None) -> tuple[str,str,int,list[str]]:
    tokens=_tokens(path); low=path.stem.lower()
    tags=sorted(tokens & {"talking","listening","thinking","relaxed","neutral","intro","outro","conversation","duo"})
    tags += sorted(x for x in {"leaning_forward","open_hand","on_table","knee_up","brightroom"} if x in low)
    if character is None:
        pose="intro" if "intro" in tokens else "outro" if "outro" in tokens else "conversation" if "conversation" in tokens else "neutral" if tokens & {"neutral","duo"} else "generic"
        return "group",pose,80 if pose!="generic" else 40,tags
    if "talking" in tokens: return "speaking_primary","talking",90,tags
    if "listening" in tokens: return "listening","listening",75,tags
    if "thinking" in tokens: return "thinking","thinking",65,tags
    if "relaxed" in tokens: return "relaxed","relaxed",55,tags
    if "on_table" in low or "table" in tokens: return "occasional","on_table",35,tags
    return "generic","generic",45,tags

def _discover(root: Path,catalog: dict[str,Any]) -> tuple[list[dict[str,Any]],list[str],list[str],dict[str,Any]]:
    extensions={str(x).lower() for x in catalog.get("extensions",[".png",".jpg",".jpeg"])}
    char_root=root/catalog.get("character_root","assets/characters"); group_root=root/catalog.get("group_root","assets/groups")
    scanned = [char_root, group_root]
    directory_rows = [{"path": p.as_posix(), "exists": p.is_dir(), "readable": os.access(p, os.R_OK)} for p in scanned]
    if not char_root.is_dir() or not group_root.is_dir():
        raise StorycastError("Directory della libreria visiva mancanti. " + _diagnostic_text(root, directory_rows, 0, 0, {"directory_mancante": sum(not x["exists"] for x in directory_rows)}))
    candidates=[]
    for folder in sorted(x for x in char_root.iterdir() if x.is_dir()):
        candidates.extend((p,folder.name,char_root) for p in sorted(folder.rglob("*")) if p.is_file())
    candidates.extend((p,None,group_root) for p in sorted(group_root.rglob("*")) if p.is_file())
    assets=[]; warnings=[]; excluded=[]; hashes={}; reasons={}; excluded_parts={str(x).rstrip("/") for x in catalog.get("archive_exclusions",[])}
    for path,character,base in candidates:
        rel=path.relative_to(root).as_posix()
        if excluded_parts.intersection(path.relative_to(base).parts[:-1]): excluded.append(rel); reasons["directory_esclusa"]=reasons.get("directory_esclusa",0)+1; continue
        if path.suffix.lower() not in extensions: warnings.append(f"Formato non supportato ignorato: {rel}"); reasons["formato_non_supportato"]=reasons.get("formato_non_supportato",0)+1; continue
        try: size=image_size(path); digest=sha256(path)
        except (StorycastError,OSError) as exc: warnings.append(f"Immagine non leggibile ignorata: {rel}: {exc}"); reasons["immagine_non_leggibile"]=reasons.get("immagine_non_leggibile",0)+1; continue
        if digest in hashes: warnings.append(f"File duplicato ignorato: {rel} (uguale a {hashes[digest]})"); reasons["duplicato"]=reasons.get("duplicato",0)+1; continue
        hashes[digest]=rel; function,pose,priority,tags=_classify(path,character)
        assets.append({"id":re.sub(r"[^a-z0-9_]+","_",path.stem.lower()),"source":rel,"character":character,"function":function,"compatible_speakers":[character] if character else [],"pose_type":pose,"tags":tags,"priority":priority,"resolution":list(size),"sha256":digest,"crop":[0.0,0.0,1.0,1.0]})
    for character in sorted(x.name for x in char_root.iterdir() if x.is_dir()):
        if not any(x["character"]==character for x in assets): raise StorycastError(f"Nessuna immagine valida per il personaggio: {character}. " + _diagnostic_text(root,directory_rows,len(candidates),len(assets),reasons))
        if not any(x["character"]==character and x["function"]=="speaking_primary" for x in assets): warnings.append(f"Nessuna posa talking per {character}: fallback generico")
    if not any(x["function"]=="group" for x in assets): raise StorycastError("Nessuna immagine di gruppo valida. " + _diagnostic_text(root,directory_rows,len(candidates),len(assets),reasons))
    diagnostics={"container_project_root":root.as_posix(),"directories":directory_rows,"files_found":len(candidates),"files_accepted":len(assets),"files_discarded":len(candidates)-len(assets),"discard_reasons":reasons}
    return assets,warnings,excluded,diagnostics


def _diagnostic_text(root: Path, directories: list[dict], found: int, accepted: int, reasons: dict[str,int]) -> str:
    shown=", ".join(f"{x['path']} (esiste={x['exists']}, leggibile={x['readable']})" for x in directories)
    main=max(reasons,key=reasons.get) if reasons else "nessuno"
    return f"root visto dal container={root}; directory scandite=[{shown}]; file trovati={found}; accettati={accepted}; scartati={found-accepted}; motivo principale={main}; motivi={reasons}"

def _catalog(root: Path) -> tuple[dict[str,Any],dict[str,Any]]:
    catalog=load_json_config(root/CATALOG); render=load_json_config(root/"config/render.yaml")
    assets,warnings,excluded,diagnostics=_discover(root,catalog)
    return {**catalog,"assets":assets,"warnings":warnings,"excluded":excluded,"diagnostics":diagnostics},render


def _crop_pixels(crop: list[float], size: tuple[int, int], target: tuple[int, int]) -> list[int]:
    """Massimo rettangolo target-aspect, centrato nell'area configurata, con dimensioni pari."""
    sw, sh = size
    x, y, w, h = crop
    left, top = round(x * sw), round(y * sh)
    aw, ah = max(2, round(w * sw)), max(2, round(h * sh))
    ratio = target[0] / target[1]
    if aw / ah > ratio:
        cw, ch = int(ah * ratio), ah
    else:
        cw, ch = aw, int(aw / ratio)
    cw -= cw % 2
    ch -= ch % 2
    left += max(0, (aw - cw) // 2)
    top += max(0, (ah - ch) // 2)
    left = min(max(0, left), sw - cw)
    top = min(max(0, top), sh - ch)
    return [left, top, cw, ch]


def _cache_key(asset: dict, crop_px: list[int], target: list[int], transform: dict) -> str:
    payload = {"source_sha256": asset["sha256"], "crop": crop_px, "target": target, "transform": transform}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def inspect_library(root: Path) -> dict[str, Any]:
    catalog, render = _catalog(root)
    return {
        "library_id": catalog["library_id"],
        "asset_count": len(catalog["assets"]),
        "assets": [{"id": x["id"], "source": x["source"], "character": x["character"], "function": x["function"], "pose_type": x["pose_type"], "speakers": x["compatible_speakers"], "resolution": x["resolution"], "sha256": x["sha256"]} for x in catalog["assets"]],
        "excluded": catalog["excluded"], "warnings": catalog["warnings"],
        "diagnostics": catalog["diagnostics"],
        "output": str(FINAL_VIDEO),
        "format": render["video"],
    }


def build_library(root: Path, dry_run: bool = False, runner=subprocess.run) -> dict[str, Any]:
    catalog, render = _catalog(root)
    target = render["library_planner"]["transform"]["target_resolution"]
    transform = render["library_planner"]["transform"]
    old = json.loads((root / MANIFEST).read_text(encoding="utf-8")) if (root / MANIFEST).is_file() else {"assets": []}
    old_by_id = {x["id"]: x for x in old.get("assets", [])}
    records = []
    for asset in catalog["assets"]:
        src = root / asset["source"]
        source_size = image_size(src)
        crop_px = _crop_pixels(asset["crop"], source_size, tuple(target))
        key = _cache_key(asset, crop_px, target, transform)
        rel = Path(catalog["derived_root"]) / f"{asset['id']}_{key[:16]}.png"
        out = root / rel
        prior = old_by_id.get(asset["id"], {})
        hit = out.is_file() and prior.get("cache_key") == key and prior.get("derived_sha256") == sha256(out)
        action = "cache_hit" if hit else "create"
        print(f"{asset['id']}: crop={crop_px} -> {rel} [{action}]")
        if not dry_run and not hit:
            out.parent.mkdir(parents=True, exist_ok=True)
            x, y, w, h = crop_px
            runner(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", f"crop={w}:{h}:{x}:{y},scale={target[0]}:{target[1]}:flags={transform['scale_filter']}", "-frames:v", "1", str(out)], check=True)
        derived_hash = sha256(out) if out.is_file() else None
        records.append({
            **asset, "source_resolution": list(source_size), "pixel_crop": crop_px,
            "derived": str(rel), "target_resolution": target, "cache_key": key,
            "cache_status": action, "derived_sha256": derived_hash,
            "created_at": prior.get("created_at") if hit else (None if dry_run else _now()),
            "validation_status": "valid" if derived_hash else "planned",
        })
    result = {"schema_version": 2, "library_id": catalog["library_id"], "generated_at": _now(), "assets": records, "warnings": catalog["warnings"], "excluded": catalog["excluded"]}
    if not dry_run:
        write_json(root / MANIFEST, result)
    return result


def _add(scenes: list[dict], start: float, end: float, speaker: str | None, asset: dict, movement: str, transition: str, reason: str) -> None:
    if end - start < 1e-7:
        return
    scenes.append({"index": len(scenes) + 1, "start": start, "end": end, "duration": end-start,
                   "speaker": speaker, "source_asset": asset["source"], "derived_asset": asset["derived"],
                   "asset_id": asset["id"], "pose_type": asset["pose_type"], "crop": asset["pixel_crop"],
                   "movement": movement, "transition": transition, "reason": reason})


def plan_library(root: Path, dry_run: bool = False, *, timeline_path: Path | None = None, manifest: dict | None = None, output_path: Path | None = None) -> dict[str, Any]:
    _, render = _catalog(root)
    manifest = manifest or build_library(root, dry_run=dry_run)
    timeline_path = timeline_path or root / TIMELINE
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    entries = timeline.get("entries", [])
    if not entries:
        raise StorycastError("Timeline episodio vuota")
    cfg = render["library_planner"]
    total = max(float(x["end"]) + float(x.get("pause_after") or 0) for x in entries)
    assets=manifest["assets"]; speakers=sorted({e["speaker"] for e in entries}); rng=random.Random(int(cfg["seed"]))
    per_character={s:[x for x in assets if x.get("character")==s] for s in speakers}
    missing=[s for s,v in per_character.items() if not v]
    if missing: raise StorycastError(f"Speaker senza asset visivo: {', '.join(missing)}")
    groups=[x for x in assets if x["function"]=="group"]
    history=[]; usage={x["id"]:0 for x in assets}; fallback=[]; table_count=0
    def choose(pool:list[dict],preferred:set[str],reason:str,avoid_table:bool=False)->dict:
        nonlocal table_count
        preferred_pool=[x for x in pool if x["pose_type"] in preferred]
        candidates=preferred_pool or pool
        if not preferred_pool: fallback.append(reason)
        if avoid_table and len(candidates)>1:
            no_table=[x for x in candidates if x["pose_type"]!="on_table"]
            if no_table and table_count*4>=max(1,len(history)): candidates=no_table
        if history and len(candidates)>1:
            alternatives=[x for x in candidates if x["id"]!=history[-1]]
            if alternatives: candidates=alternatives
        best=min(usage[x["id"]] for x in candidates); candidates=[x for x in candidates if usage[x["id"]]==best]
        candidates=sorted(candidates,key=lambda x:(-x["priority"],x["id"])); choice=candidates[rng.randrange(len(candidates))]
        usage[choice["id"]]+=1; history.append(choice["id"]); table_count += choice["pose_type"]=="on_table"
        return choice
    def group(kind:str)->dict: return choose(groups,{kind,"neutral","conversation"},f"fallback gruppo {kind}")
    scenes: list[dict] = []
    motions = ["slow_zoom_in", "static", "pan_horizontal", "slow_zoom_out"]
    first_duration=max(0.0,float(entries[0]["end"])-float(entries[0]["start"]))
    last_duration=max(0.0,float(entries[-1]["end"])-float(entries[-1]["start"]))
    opening=min(cfg["opening_seconds"],total/5,first_duration/3)
    closing=min(cfg["closing_seconds"],total/5,last_duration/3)
    _add(scenes,0.0,min(opening,total),None,group("intro"),"slow_zoom_in","cut","apertura con immagine di gruppo")
    cursor = min(opening, total)
    for pos, entry in enumerate(entries):
        speaker = entry["speaker"]
        speech_start, speech_end = max(cursor, float(entry["start"])), min(float(entry["end"]), total-closing if pos == len(entries)-1 else float(entry["end"]))
        duration = max(0, speech_end-speech_start)
        chunks=max(1,min(int(cfg.get("max_pose_changes_per_speech",2))+1,math.ceil(duration/float(cfg.get("long_pose_seconds",9.0)))))
        if duration/chunks<float(cfg["minimum_scene_seconds"]): chunks=1
        bounds=[speech_start+duration*i/chunks for i in range(chunks+1)]
        for part in range(chunks):
            preferred={"talking"} if part==0 else {"talking","thinking","relaxed"}
            asset=choose(per_character[speaker],preferred,f"nessuna posa parlante adeguata per {speaker}",True)
            _add(scenes,bounds[part],bounds[part+1],speaker,asset,motions[(pos+part)%len(motions)],"cut","posa coerente con il parlante")
        cursor = speech_end
        pause_end = min(float(entries[pos+1]["start"]) if pos+1 < len(entries) else total-closing, total-closing)
        if 0 < pause_end-cursor < cfg["minimum_scene_seconds"] and scenes:
            # Una pausa breve resta sull'ultima inquadratura: evita tagli meccanici
            # più rapidi della soglia senza lasciare buchi nella timeline.
            scenes[-1]["end"] = pause_end
            scenes[-1]["duration"] = pause_end-scenes[-1]["start"]
            scenes[-1]["reason"] += "; mantenuta durante la pausa breve"
        else:
            listeners=[x for s in speakers if s!=speaker for x in per_character[s] if x["pose_type"] in {"listening","thinking","relaxed"}]
            pause_asset=choose(listeners,{"listening","thinking","relaxed"},"fallback pausa") if listeners else group("conversation")
            _add(scenes,cursor,pause_end,None,pause_asset,"static","cut","pausa con ascolto o gruppo")
        cursor = max(cursor, pause_end)
    _add(scenes,cursor,total,None,group("outro"),"slow_zoom_out","cut","conclusione con immagine di gruppo")
    # Limiti esatti ai frame, senza buchi o sovrapposizioni; l'ultimo segue l'audio reale.
    fps = render["video"]["fps"]
    boundaries = [0.0] + [round(x["end"]*fps)/fps for x in scenes[:-1]] + [total]
    for i, scene in enumerate(scenes):
        scene.update(index=i+1, start=round(boundaries[i], 6), end=round(boundaries[i+1], 6), duration=round(boundaries[i+1]-boundaries[i], 6))
        if scene["duration"] <= 0:
            raise StorycastError("Scena nulla dopo quantizzazione")
    plan = {"schema_version":2,"seed":cfg["seed"],"timeline_source":str(timeline_path.relative_to(root)),"total_duration":round(total,6),"fps":fps,"fallbacks":sorted(set(fallback)),"scenes":scenes}
    for s in scenes:
        print(f"{s['index']:02d} {s['start']:.3f}-{s['end']:.3f}s {s['asset_id']} {s['movement']} — {s['reason']}")
    if not dry_run:
        write_json(root / (output_path or SHOT_PLAN), plan)
    return plan


def _scene_command(root: Path, scene: dict, render: dict, out: Path) -> list[str]:
    fps = render["video"]["fps"]
    frames = max(1, round(scene["duration"]*fps))
    zoom = render["camera"]["max_zoom"]
    motion = scene["movement"]
    if motion == "slow_zoom_in":
        z = f"min(1+on*({zoom}-1)/{max(frames-1, 1)},{zoom})"
    elif motion == "slow_zoom_out":
        z = f"max({zoom}-on*({zoom}-1)/{max(frames-1, 1)},1)"
    else:
        z = "1.02" if motion == "pan_horizontal" else "1"
    x = f"(iw-iw/zoom)*on/{max(frames-1, 1)}" if motion == "pan_horizontal" else "iw/2-iw/zoom/2"
    if scene.get("story_image"):
        cfg = render["story_images"]; fade = float(cfg["fade_seconds"]); zoom = float(cfg["max_zoom"])
        z = f"min(1+on*({zoom}-1)/{max(frames-1, 1)},{zoom})"
        fade_out = max(0.0, float(scene["duration"]) - fade)
        vf = (f"scale={render['video']['width']}:{render['video']['height']}:force_original_aspect_ratio=increase,"
              f"crop={render['video']['width']}:{render['video']['height']},"
              f"zoompan=z='{z}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:"
              f"s={render['video']['width']}x{render['video']['height']}:fps={fps},"
              f"fade=t=in:st=0:d={fade},fade=t=out:st={fade_out}:d={fade},format={render['video']['pixel_format']}")
    else:
        vf = f"zoompan=z='{z}':x='{x}':y='ih/2-ih/zoom/2':d={frames}:s={render['video']['width']}x{render['video']['height']}:fps={fps},format={render['video']['pixel_format']}"
    return ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(root/scene["derived_asset"]), "-vf", vf, "-frames:v", str(frames), "-an", "-c:v", render["video"]["codec"], "-preset", render["video"]["preset"], "-crf", str(render["video"]["crf"]), str(out)]


def render_library(root: Path, dry_run: bool = False, runner=subprocess.run, *,
                   timeline_path: Path | None = None, audio_path: Path | None = None,
                   final_path: Path | None = None, plan_path: Path | None = None,
                   scene_dir: Path | None = None, scene_manifest_path: Path | None = None,
                   backup_dir: Path | None = None,
                   progress_logger: Any | None = None,
                   story_images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    _, render = _catalog(root)
    manifest = build_library(root, dry_run=dry_run)
    timeline_path = timeline_path or root/TIMELINE
    plan_path = plan_path or root/SHOT_PLAN
    scene_dir = scene_dir or root/SCENES
    scene_manifest_path = scene_manifest_path or root/SCENE_MANIFEST
    plan = plan_library(root, dry_run=dry_run, manifest=manifest, timeline_path=timeline_path, output_path=plan_path.relative_to(root))
    if story_images is not None:
        plan = apply_story_inserts(plan, story_images, load_story_image_config(root))
        if not dry_run:
            write_json(plan_path, plan)
    audio, final = audio_path or root/AUDIO, final_path or root/FINAL_VIDEO
    if not audio.is_file():
        raise StorycastError(f"Audio episodio mancante: {AUDIO}")
    commands = [_scene_command(root, s, render, scene_dir/f"scene_{s['index']:04d}.mp4") for s in plan["scenes"]]
    shown = final.relative_to(root)
    print(f"Output: {shown}; durata {plan['total_duration']:.3f}s; scene {len(plan['scenes'])}; nessun TTS")
    total_scenes = len(plan["scenes"])
    width = max(2, len(str(total_scenes)))
    print(f"[VIDEO] Preparazione video: {total_scenes} scene totali", flush=True)
    if progress_logger:
        progress_logger.record(f"VIDEO scene_totali={total_scenes}")
    if dry_run:
        return {"status": "dry-run", "asset_count": len(manifest["assets"]), "excluded": inspect_library(root)["excluded"], "plan": plan, "commands": commands, "output": str(shown)}
    scene_dir.mkdir(parents=True, exist_ok=True)
    rendered_scenes = cached_scenes = 0
    for scene, command in zip(plan["scenes"], commands):
        output = Path(command[-1]); sidecar = output.with_suffix(".json")
        cache_key = hashlib.sha256(json.dumps({"scene": scene, "command": command[:-1], "derived_sha256": sha256(root/scene["derived_asset"])}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        valid = False
        if output.is_file() and sidecar.is_file():
            try:
                saved = json.loads(sidecar.read_text(encoding="utf-8")); valid = saved.get("cache_key") == cache_key and saved.get("sha256") == sha256(output)
            except (OSError, json.JSONDecodeError):
                valid = False
        if valid:
            cached_scenes += 1
            cache_label = " cache=riutilizzata"
        else:
            runner(command, check=True); rendered_scenes += 1
            write_json(sidecar, {"schema_version": 1, "cache_key": cache_key, "sha256": sha256(output), "scene_index": scene["index"]})
            cache_label = ""
        speaker = scene.get("speaker") or "gruppo"
        duration = f"{scene['duration']:.2f}".replace(".", ",")
        current = f"{scene['index']:0{width}d}/{total_scenes}"
        print(f"[VIDEO] Scena {current} completata - {speaker} - durata {duration} s{cache_label}", flush=True)
        if progress_logger:
            progress_logger.record(f"VIDEO scena={current} stato=completata speaker={speaker} durata={scene['duration']:.2f}s{cache_label}")
    print(f"[VIDEO] Scene completate: {total_scenes}/{total_scenes}", flush=True)
    print("[VIDEO] Montaggio finale in corso", flush=True)
    if progress_logger:
        progress_logger.record(f"VIDEO scene_completate={total_scenes}/{total_scenes}")
        progress_logger.record("VIDEO montaggio=inizio")
    concat = scene_dir/"concat.txt"
    concat.write_text("".join(f"file 'scene_{s['index']:04d}.mp4'\n" for s in plan["scenes"]), encoding="utf-8")
    silent = scene_dir/"silent.mp4"
    runner(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)], check=True)
    if final.exists():
        backup = (backup_dir or root/"work/backups/episode_01_before_visual_library")/f"{final.stem}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(final, backup)
    frames = math.ceil(plan["total_duration"]*render["video"]["fps"])
    runner(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-vf", f"fps={render['video']['fps']},tpad=stop_mode=clone:stop_duration=1,format={render['video']['pixel_format']}", "-frames:v", str(frames), "-c:v", render["video"]["codec"], "-preset", render["video"]["preset"], "-crf", str(render["video"]["crf"]), "-c:a", render["audio"]["codec"], "-b:a", render["audio"]["bitrate"], "-ac", "1", "-ar", str(render["audio"]["sample_rate"]), "-movflags", "+faststart", str(final)], check=True)
    write_json(scene_manifest_path, {"schema_version": 1, "video": str(shown), "story_images_signature": plan.get("story_images_signature"), "scenes": [{"index": s["index"], "start": s["start"], "end": s["end"], "asset_id": s["asset_id"], "source_asset": s["source_asset"], "derived_asset": s["derived_asset"]} for s in plan["scenes"]]})
    print(f"[VIDEO] Video completato: {shown}", flush=True)
    if progress_logger:
        progress_logger.record(f"VIDEO montaggio=completato output={shown}")
    return {"status": "rendered", "output": str(shown), "sha256": sha256(final), "scenes": len(plan["scenes"]), "rendered_scenes": rendered_scenes, "cached_scenes": cached_scenes}


def verify_library_video(root: Path, runner=subprocess.run, *, final_path: Path | None = None,
                         audio_path: Path | None = None, plan_path: Path | None = None,
                         frame_dir: Path | None = None, verification_path: Path | None = None,
                         require_all_assets: bool = True) -> dict[str, Any]:
    _, render = _catalog(root)
    final, audio = final_path or root/FINAL_VIDEO, audio_path or root/AUDIO
    if not final.is_file():
        raise StorycastError(f"Video mancante: {FINAL_VIDEO}")
    plan = json.loads((plan_path or root/SHOT_PLAN).read_text(encoding="utf-8"))
    data = probe(root, final)
    v = next((x for x in data["streams"] if x["codec_type"] == "video"), None)
    a = next((x for x in data["streams"] if x["codec_type"] == "audio"), None)
    audio_duration = float(probe(root, audio)["format"]["duration"])
    video_duration = float(v.get("duration", data["format"]["duration"]))
    num, den = map(int, v["r_frame_rate"].split("/"))
    fps = num/den
    ev, ea = render["video"], render["audio"]
    checks = {"video_codec_h264": bool(v and v["codec_name"] == "h264"), "audio_codec_aac": bool(a and a["codec_name"] == "aac"), "resolution_1280x720": bool(v and [v["width"], v["height"]] == [ev["width"], ev["height"]]), "fps_30": abs(fps-ev["fps"]) < .001, "pixel_format_yuv420p": bool(v and v["pix_fmt"] == ev["pixel_format"]), "audio_mono": bool(a and a["channels"] == 1), "sync_within_one_frame": abs(audio_duration-video_duration) <= 1/ev["fps"]+1e-3}
    # Un fotogramma per ciascun asset realmente usato dal piano.
    first_scene: dict[str, dict] = {}
    for scene in plan["scenes"]:
        first_scene.setdefault(scene["asset_id"], scene)
    frame_dir = frame_dir or root/FRAME_DIR
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for asset_id, scene in sorted(first_scene.items()):
        when = scene["start"] + min(scene["duration"]/2, .4)
        out = frame_dir/f"{len(frames)+1:02d}_{asset_id}.png"
        runner(["ffmpeg", "-v", "error", "-y", "-ss", str(when), "-i", str(final), "-frames:v", "1", str(out)], check=True)
        measured = runner(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "signalstats,metadata=print:file=-", "-frames:v", "1", "-f", "null", "-"], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw = measured.stdout + measured.stderr
        yavg = next((float(line.split("=", 1)[1]) for line in raw.splitlines() if "lavfi.signalstats.YAVG=" in line), 0.0)
        frames.append({"file": str(out.relative_to(root)), "time": round(when, 3), "asset_id": asset_id, "source_asset": scene["source_asset"], "sha256": sha256(out), "yavg": yavg, "valid": out.stat().st_size > 100 and yavg > 2})
    checks["multiple_assets_represented"] = len(first_scene) >= 2
    checks["all_planned_assets_have_valid_frames"] = len(frames) == len(first_scene) and all(x["valid"] for x in frames)
    if require_all_assets:
        checks["group_and_speakers_represented"] = any(s.get("speaker") is None for s in plan["scenes"]) and all(any(s.get("speaker")==speaker for s in plan["scenes"]) for speaker in {s.get("speaker") for s in plan["scenes"] if s.get("speaker")})
    result = {"schema_version": 1, "status": "passed" if all(checks.values()) else "failed", "checks": checks, "audio_duration": audio_duration, "video_duration": video_duration, "duration_delta": abs(audio_duration-video_duration), "video_sha256": sha256(final), "frames": frames, "ffprobe": data}
    write_json(verification_path or root/VERIFY_MANIFEST, result)
    if result["status"] != "passed":
        raise StorycastError(f"Verifica video libreria fallita: {checks}")
    return result


def clean_candidates(root: Path) -> list[Path]:
    candidates = []
    for base in (root/"work/visual/library_v1", root/FRAME_DIR):
        if base.exists():
            candidates.extend(p for p in base.rglob("*") if p.is_file())
    for path in (root/SHOT_PLAN, root/FINAL_VIDEO):
        if path.is_file():
            candidates.append(path)
    return sorted(set(candidates))
