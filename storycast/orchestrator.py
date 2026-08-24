from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

from .core import StorycastError, load_characters, parse_dialogue, write_json, write_json_atomic
from .tts import (build_plan, cache_status, canonical_hash, file_hash, generate,
                  generation_hashes, load_tts_config, load_voices, merge_audio,
                  split_text, verify_plan, wav_info, active_instruction_profile,
                  load_instruction_configuration)
from .visual_library import (build_library, inspect_library, plan_library,
                             render_library, verify_library_video)
from .segment_safety import cooldown_status, inspect_segment, scan_segments, recover_latest_rejected
from .run_logging import RunLogger, format_duration
from .final_speed import create_speed_version, speed_output_path, validate_factor
from .visual import load_json_config
from .episode_bundle import pipeline_plan, precheck_episode_bundle
from .short_pipeline import run_short_audio, short_list_segments, short_status
from .short_video import render_short_video, short_video_status
from .story_images import choose_story_images

ROOT = Path(os.environ.get("STORYCAST_ROOT", Path(__file__).resolve().parents[1])).resolve()
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
PHASES = ["initialized", "validated", "parsed", "tts_planned", "tts_running", "tts_complete",
          "audio_qc_complete", "awaiting_review", "audio_merged", "timeline_complete",
          "visual_plan_complete", "rendering", "video_complete", "verified"]


def now() -> str: return dt.datetime.now(dt.timezone.utc).isoformat()
def stamp() -> str: return dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def slug_from(value: str | None, input_path: Path) -> str:
    if value is not None:
        slug = value
    else:
        raw = unicodedata.normalize("NFKD", input_path.stem).encode("ascii", "ignore").decode().lower()
        slug = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_-")
    if not slug or not SLUG_RE.fullmatch(slug):
        raise StorycastError("Slug non valido: usare solo lettere minuscole, numeri, trattini e underscore")
    return slug


def safe_input(root: Path, value: str | Path | None) -> Path:
    raw = Path(value or "input/dialogo.txt")
    if raw.is_absolute():
        raise StorycastError("Percorsi input assoluti non autorizzati")
    resolved = (root/raw).resolve()
    try: resolved.relative_to((root/"input").resolve())
    except ValueError as exc: raise StorycastError("Input fuori dalla cartella input/ o path traversal rilevato") from exc
    if not resolved.is_file(): raise StorycastError(f"File input mancante: {raw}")
    return resolved


def paths(root: Path, slug: str) -> dict[str, Path]:
    work = root/"work/episodes"/slug; output = root/"output"/slug
    return {"work":work,"source":work/"source","dialogue":work/"dialogue/dialogue.json",
            "tts_plan":work/"metadata/tts_plan.json","segments":work/"audio_segments",
            "segment_metadata":work/"metadata/audio_segments","review":work/"metadata/review_status.json",
            "qc":work/"metadata/audio_qc.json","timeline_work":work/"timeline/timeline.json",
            "visual":work/"visual","scenes":work/"scenes","backups":work/"backups","logs":work/"logs",
            "state":work/"state.json","lock":work/"run.lock","output":output,
            "audio":output/f"{slug}_audio.wav","video":output/f"{slug}_video.mp4",
            "short_audio":output/f"{slug}_short_audio.wav",
            "short_video":output/f"{slug}_short_video.mp4",
            "short_subtitles":output/f"{slug}_short_subtitles.srt",
            "short_timeline":output/f"{slug}_short_timeline.json",
            "short_manifest":output/f"{slug}_short_audio_manifest.json",
            "short_work":work/"short","short_state":work/"short/state.json",
            "short_segments":work/"short/audio_segments",
            "short_segment_metadata":work/"short/metadata/audio_segments",
            "manifest":output/f"{slug}_audio_manifest.json","timeline":output/f"{slug}_timeline.json",
            "visual_plan":output/f"{slug}_visual_plan.json","report":output/f"{slug}_report.json",
            "scene_manifest":work/"visual/scene_asset_manifest.json","verification":work/"visual/video_verification.json",
            "frames":work/"visual/verification_frames"}


def _proc_start(pid: int) -> str | None:
    try: return Path(f"/proc/{pid}/stat").read_text().split()[21]
    except (OSError, IndexError): return None


class StoryLock:
    def __init__(self, path: Path): self.path=path; self.acquired=False
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.exists():
            try: old=json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError,json.JSONDecodeError): old={}
            active=(old.get("hostname")==socket.gethostname() and isinstance(old.get("pid"),int)
                    and old.get("process_start")==_proc_start(old["pid"]) and _proc_start(old["pid"]) is not None)
            if active: raise StorycastError(f"Storia già in esecuzione (PID {old['pid']}, lock {self.path})")
            stale=self.path.with_name(f"run.lock.stale_{stamp()}"); os.replace(self.path,stale)
        payload={"pid":os.getpid(),"hostname":socket.gethostname(),"process_start":_proc_start(os.getpid()),"created_at":now()}
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL; fd=os.open(self.path,flags,0o644)
        with os.fdopen(fd,"w",encoding="utf-8") as handle: json.dump(payload,handle); handle.flush(); os.fsync(handle.fileno())
        self.acquired=True; return self
    def __exit__(self,*_):
        if self.acquired: self.path.unlink(missing_ok=True)


def load_state(path: Path) -> dict | None:
    if not path.is_file(): return None
    try: data=json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc: raise StorycastError(f"state.json corrotto: {path}; recuperare dal backup prima di continuare") from exc
    if not isinstance(data,dict) or data.get("schema_version")!=1: raise StorycastError(f"state.json non valido: {path}")
    return data


def initial_state(slug: str, input_rel: str, input_hash: str, config_hashes: dict,
                  instruction_profile: str | None = None, profile_hash: str | None = None) -> dict:
    timestamp=now()
    return {"schema_version":1,"slug":slug,"input_original":input_rel,"input_hash":input_hash,
            "configurations":config_hashes,"model":None,"instruction_profile":instruction_profile,
            "instruction_profile_hash":profile_hash,"created_at":timestamp,"updated_at":timestamp,
            "phase":"initialized","completed_phases":["initialized"],"segments_valid":[],"segments_missing":[],
            "segments_needing_review":[],"audio_complete":None,"timeline":None,"visual_plan":None,
            "scenes_rendered":[],"video_final":None,"errors":[],"final_status":"running"}


def save_state(path: Path, state: dict) -> None:
    state["updated_at"]=now(); write_json_atomic(path,state)


def mark(path: Path, state: dict, phase: str, **values) -> None:
    state["phase"]=phase
    if phase not in state["completed_phases"] and phase not in {"tts_running","rendering","awaiting_review","failed"}: state["completed_phases"].append(phase)
    state.update(values); save_state(path,state)


def config_hashes(root: Path) -> dict[str,str]:
    names=("config/tts.json","config/voices.yaml","config/visual_library.yaml","config/render.yaml")
    return {name:file_hash(root/name) for name in names}


def profile_hash(root: Path, name: str) -> str:
    data=load_instruction_configuration(root)
    voices={key:value.get("instructions",{}).get(name,value.get("instruction")) for key,value in data["voices"].items()}
    return canonical_hash({"name":name,"profile":data["instruction_profiles"][name],
                           "emotion_mapping":data.get("emotion_mappings",{}).get(name,{}),"voices":voices})


def _story_profile(root: Path, state: dict | None) -> tuple[str,bool]:
    if state:
        selected=state.get("instruction_profile")
        return (selected,False) if selected else ("italian_legacy",True)
    return active_instruction_profile(root),False


def context(root: Path, input_path: Path, slug: str, mock: bool=False,
            instruction_profile: str | None=None, legacy_hash_compat: bool=False) -> tuple[dict,list[dict],list[dict],dict]:
    chars=load_characters(root); voices=load_voices(root,chars,instruction_profile=instruction_profile,
                                                    legacy_hash_compat=legacy_hash_compat); config=load_tts_config(root)
    entries=parse_dialogue(input_path,chars)
    used={x["speaker"] for x in entries}; available=set(config["verification"].get("available_voices",[]))
    for speaker in used:
        voice=voices.get(speaker)
        if not voice: raise StorycastError(f"Speaker senza voce configurata: {speaker}")
        if voice["voice"] not in available: raise StorycastError(f"Voce non disponibile nel modello per {speaker}: {voice['voice']}")
    config=dict(config); config["backend"]="mock" if mock else config["backend"]
    plan=build_plan(root,entries,voices,config); pp=paths(root,slug)
    for item in plan:
        stem=f"{item['index']:04d}_{item['speaker']}"
        item["wav_path"]=pp["segments"]/f"{stem}.wav"; item["metadata_path"]=pp["segment_metadata"]/f"{stem}.json"; item["cache_status"]=cache_status(item)
    return config,entries,plan,voices


def _jsonable_plan(plan: list[dict]) -> list[dict]:
    return [{k:(v.as_posix() if isinstance(v,Path) else v) for k,v in item.items()} for item in plan]


def plan_summary(root: Path, input_path: Path, slug: str, mock=False) -> dict:
    bundle=precheck_episode_bundle(root,input_path)
    pp=paths(root,slug); previous=load_state(pp["state"]); selected,legacy=_story_profile(root,previous)
    config,entries,plan,voices=context(root,input_path,slug,mock,selected,legacy)
    library=inspect_library(root); free=shutil.disk_usage(root).free; estimated=max(100*1024*1024,len(entries)*35*1024*1024)
    valid=sum(x["cache_status"]=="valid" for x in plan)
    return {"input":input_path.relative_to(root).as_posix(),"slug":slug,"characters":sorted({x["speaker"] for x in entries}),
            "episode_pipeline":pipeline_plan(root,slug,bundle),
            "voices":{speaker:voices[speaker]["voice"] for speaker in sorted({x["speaker"] for x in entries})},
            "utterances":len(entries),"segments":[{"index":x["index"],"speaker":x["speaker"],"voice":x["voice"],"cache":x["cache_status"],"wav":x["wav_path"].relative_to(root).as_posix()} for x in plan],
            "cache":{"valid":valid,"to_generate":len(plan)-valid},"phases":PHASES,"visual_assets":library["assets"],
            "outputs":{k:v.relative_to(root).as_posix() for k,v in pp.items() if k in {"audio","video","manifest","timeline","visual_plan","report"}},
            "estimated_space_bytes":estimated,"free_space_bytes":free,"previous_state":previous,
            "resume_from":_resume_phase(previous),
            "backend":config["backend"],"model":config["model"]["id"],"instruction_profile":selected,
            "instruction_language":next(iter(voices.values()))["instruction_language"],"spoken_language":"Italian"}


def _resume_phase(previous: dict | None) -> str:
    if not previous: return "initialized"
    if previous.get("final_status")=="verified": return "verified"
    current=previous.get("phase")
    if current in PHASES and current not in previous.get("completed_phases",[]): return current
    core=[x for x in PHASES if x not in {"tts_running","awaiting_review","rendering"}]
    return next((x for x in core if x not in previous.get("completed_phases",[])),"verified")


def _review_data(pp: dict, plan: list[dict]) -> dict:
    old={}
    if pp["review"].is_file():
        try: old=json.loads(pp["review"].read_text(encoding="utf-8")).get("segments",{})
        except (OSError,json.JSONDecodeError): old={}
    rows={}
    for item in plan:
        key=str(item["index"]); saved=old.get(key,{}); current=file_hash(item["wav_path"]) if item["wav_path"].is_file() else None
        state=saved.get("review_state","pending_review"); reviewed=saved.get("reviewed_wav_hash")
        if state in {"approved","rejected"} and reviewed!=current: state="pending_review"; reviewed=None
        rows[key]={"review_state":state if state in {"pending_review","approved","rejected"} else "pending_review",
                   "reviewed_wav_hash":reviewed,"reviewed_at":saved.get("reviewed_at"),"note":saved.get("note"),
                   "qc_state":saved.get("qc_state","warning"),"qc_errors":saved.get("qc_errors",[]),
                   "qc_warnings":saved.get("qc_warnings",[]),"wav_hash":current}
    return {"schema_version":1,"slug":pp["work"].name,"updated_at":now(),"segments":rows}


def technical_qc(pp: dict, plan: list[dict], config: dict, write=True) -> dict:
    review=_review_data(pp,plan); base=verify_plan(plan,config); rows=[]
    for item,check in zip(plan,base):
        errors=[]; warnings=list(check["errors"]); info=None
        try:
            info=wav_info(item["wav_path"])
            if (info["sample_rate"],info["channels"],info["sample_width"])!=(24000,1,2): errors.append("formato_non_mono_pcm16_24khz")
            if info["complete_silence"] or info["rms"]==0: errors.append("silenzio_completo")
            if info["clipped_percent"]>1: warnings.append("clipping_elevato")
            meta=json.loads(item["metadata_path"].read_text(encoding="utf-8"))
            for key,expected in (("speaker",item["speaker"]),("requested_voice",item["voice"]),("backend_voice",item["voice"]),("model",item["model"]),("wav_hash",info["wav_hash"])):
                if meta.get(key)!=expected: errors.append(f"metadata_{key}_incoerente")
            if not isinstance(meta.get("seed"),int): errors.append("metadata_seed_mancante")
        except (StorycastError,OSError,json.JSONDecodeError) as exc: errors.append(str(exc))
        qc_state="failed" if errors else ("warning" if warnings else "passed"); entry=review["segments"][str(item["index"])]
        entry.update(qc_state=qc_state,qc_errors=sorted(set(errors)),qc_warnings=sorted(set(warnings)),wav_hash=info["wav_hash"] if info else None)
        rows.append({"index":item["index"],"speaker":item["speaker"],"qc_state":qc_state,"review_state":entry["review_state"],"errors":entry["qc_errors"],"warnings":entry["qc_warnings"],"audio":info})
    result={"schema_version":1,"slug":pp["work"].name,"checked_at":now(),"status":"failed" if any(x["qc_state"]=="failed" for x in rows) else "passed","segments":rows}
    if write: write_json_atomic(pp["review"],review); write_json_atomic(pp["qc"],result)
    return result


def _final_valid(pp: dict,state: dict) -> bool:
    expected=(("audio",pp["audio"]),("video",pp["video"]),("manifest",pp["manifest"]),("timeline",pp["timeline"]),("visual_plan",pp["visual_plan"]),("report",pp["report"]))
    return state.get("final_status")=="verified" and all(path.is_file() and (not state.get(key+"_sha256") or file_hash(path)==state[key+"_sha256"]) for key,path in expected)


def _speed_settings(root: Path, override: float | None, disabled: bool) -> dict:
    render = load_json_config(root/"config/render.yaml")
    raw = dict(render.get("final_speed_version", {}))
    enabled = bool(raw.get("enabled", True)) and not disabled
    if "factor" not in raw:
        raise StorycastError("config/render.yaml: final_speed_version.factor mancante")
    configured_factor = validate_factor(raw["factor"])
    factor = validate_factor(override) if override is not None else configured_factor
    return {"enabled": enabled, "factor": factor, "suffix": raw.get("suffix", "_speed{percent}"),
            "video_codec": render.get("video", {}).get("codec", "libx264"),
            "pixel_format": render.get("video", {}).get("pixel_format", "yuv420p"),
            "audio_codec": render.get("audio", {}).get("codec", "aac"),
            "video_preset": raw.get("video_preset", "veryfast"), "video_crf": int(raw.get("video_crf", 20)),
            "audio_bitrate": raw.get("audio_bitrate", "96k")}


def _postprocess_speed(root: Path, normal_path: Path, normal_duration: float, settings: dict,
                       overwrite: bool, run_log: RunLogger | None) -> dict:
    normal_rel = normal_path.relative_to(root).as_posix()
    print(f"[VIDEO] Versione normale completata: {normal_rel}", flush=True)
    if run_log: run_log.record(f"VIDEO normale={normal_rel} durata={normal_duration:.3f}")
    if not settings["enabled"]:
        return {"status":"disabled", "video_normal":normal_rel, "video_speed":None, "speed_factor":None}
    factor = settings["factor"]
    target = speed_output_path(normal_path, factor, settings["suffix"])
    target_rel = target.relative_to(root).as_posix()
    print(f"[VIDEO] Creazione versione accelerata {factor:.2f}x", flush=True)
    try:
        result = create_speed_version(normal_path, target, factor, overwrite=overwrite,
            **{key:settings[key] for key in ("video_codec","video_preset","video_crf","pixel_format","audio_codec","audio_bitrate")})
        duration = result["accelerated"]["duration"]
        print(f"[VIDEO] Versione accelerata completata: {target_rel}", flush=True)
        if run_log: run_log.record(f"VIDEO accelerato={target_rel} fattore={factor:.2f} durata={duration:.3f} esito={result['status']}")
        return {"status":result["status"], "video_normal":normal_rel, "video_speed":target_rel,
                "speed_factor":factor, "normal_duration":result["normal"]["duration"],
                "speed_duration":duration}
    except Exception as exc:
        message = str(exc).strip().splitlines()[0]
        print(f"[VIDEO] ERRORE versione accelerata: {message}", flush=True)
        print(f"[VIDEO] Versione normale conservata: {normal_rel}", flush=True)
        if run_log: run_log.record(f"VIDEO accelerato={target_rel} fattore={factor:.2f} esito=errore errore={message}")
        return {"status":"error", "video_normal":normal_rel, "video_speed":None,
                "speed_factor":factor, "error":message, "attempted_path":target_rel}


def _preflight(root: Path, pp: dict, config: dict, mock: bool) -> dict:
    library=inspect_library(root)
    if not mock:
        model=Path(config["model"]["local_dir"])
        if not model.is_dir() or not (model/"config.json").is_file(): raise StorycastError(f"Modello locale mancante: {model}; nessun download automatico")
    if shutil.disk_usage(root).free < 1024**3: raise StorycastError("Spazio libero insufficiente: richiesto almeno 1 GiB")
    required=(pp["work"],pp["source"],pp["dialogue"].parent,pp["segments"],pp["segment_metadata"],
              pp["timeline_work"].parent,pp["visual"],pp["scenes"],pp["backups"],pp["logs"],pp["output"])
    for folder in required:
        folder.mkdir(parents=True,exist_ok=True)
        if not os.access(folder,os.W_OK): raise StorycastError(f"Cartella non scrivibile: {folder}")
    return library


def _run_short_package(root: Path, input_path: Path, slug: str, pp: dict, state: dict,
                       *, mock: bool, run_log: RunLogger | None = None) -> dict:
    """Completa lo Short in sequenza e registra uno stato di pacchetto riprendibile."""
    print("[storycast] controllo episodio principale", flush=True)
    state.update(main_episode="completed", package="partial", short_audio="pending",
                 short_video="pending")
    save_state(pp["state"], state)
    try:
        audio = run_short_audio(root, input_path, slug, mock=mock, announce_precheck=False)
        state.update(short_audio="ready")
        save_state(pp["state"], state)
        print("[storycast] short video", flush=True)
        video = render_short_video(root, input_path, slug, mock=mock, audio_result=audio)
        state.update(short_video="ready")
        print("[storycast] controlli finali", flush=True)
        if not all((pp["video"].is_file(), pp["short_audio"].is_file(),
                    pp["short_video"].is_file(), pp["short_subtitles"].is_file())):
            raise StorycastError("Controllo finale del pacchetto: uno o più output obbligatori mancano")
        state.update(package="completed", package_error=None)
        save_state(pp["state"], state)
        print("[storycast] completato", flush=True)
        if run_log:
            run_log.info("Pacchetto completato: episodio principale e Short validi")
        return {"audio": audio, "video": video, "status": "completed"}
    except Exception as exc:
        state.update(main_episode="completed", package="partial", package_error=str(exc))
        if state.get("short_audio") != "ready": state["short_audio"] = "error"
        if state.get("short_video") != "ready": state["short_video"] = "error"
        save_state(pp["state"], state)
        raise StorycastError(f"EPISODIO PRINCIPALE: OK\nSHORT: ERRORE\n{exc}") from exc


def run_story(root: Path, input_path: Path, slug: str, *, mock=False, review_audio=False, replace=False,
              refresh_voice_instructions=False, final_speed: float | None = None,
              no_speed_version: bool = False, run_log: RunLogger | None = None,
              story_images: str = "ask") -> dict:
    print("[storycast] precheck input",flush=True)
    bundle=precheck_episode_bundle(root,input_path)
    episode_pipeline=pipeline_plan(root,slug,bundle)
    story_image_choice=choose_story_images(root,story_images)
    print("[storycast] episodio principale",flush=True)
    pp=paths(root,slug); digest=file_hash(input_path); rel=input_path.relative_to(root).as_posix()
    with StoryLock(pp["lock"]):
        state=load_state(pp["state"])
        if state and state.get("input_hash")!=digest:
            if not replace: raise StorycastError(f"Lo slug '{slug}' esiste con un input diverso. Usare --nome con un nuovo slug oppure --sostituisci esplicitamente.")
            backup=pp["backups"]/f"replace_{stamp()}"; backup.mkdir(parents=True)
            for path in (pp["state"],pp["audio"],pp["video"],pp["manifest"],pp["timeline"],pp["visual_plan"],pp["report"]):
                if path.is_file(): shutil.copy2(path,backup/path.name)
            state=None
        selected,legacy=_story_profile(root,state)
        if refresh_voice_instructions and state and state.get("instruction_profile")!=active_instruction_profile(root):
            raise StorycastError("Per cambiare le istruzioni di una storia esistente usare un nuovo slug; nessun WAV e' stato modificato")
        if refresh_voice_instructions: selected,legacy=active_instruction_profile(root),False
        config,entries,plan,voices=context(root,input_path,slug,mock,selected,legacy)
        speed_settings = _speed_settings(root, final_speed, no_speed_version)
        if run_log:
            run_log.set_phase("validazione e parsing")
            run_log.info(f"Dialogo validato: {len(entries)} battute, {len({x['speaker'] for x in entries})} personaggi")
        library=_preflight(root,pp,config,mock)
        if run_log:
            counts={speaker:0 for speaker in sorted({x['speaker'] for x in entries})}; groups=0
            for asset in library["assets"]:
                if asset.get("character") is None: groups+=1
                elif asset["character"] in counts: counts[asset["character"]]+=1
            run_log.info("Libreria visiva: " + ", ".join([f"{name}={value}" for name,value in counts.items()]+[f"gruppi={groups}",f"totale={library['asset_count']}"]))
            for warning in library.get("warnings",[]): run_log.warning(f"Libreria visiva: {warning}")
        if state is None:
            state=initial_state(slug,rel,digest,config_hashes(root),selected,profile_hash(root,selected)); state["model"]={"id":config["model"]["id"],"backend":config["backend"]}; save_state(pp["state"],state)
            target=pp["source"]/input_path.name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(input_path,target)
            shutil.copy2(bundle.short_path,pp["source"]/bundle.short_path.name)
        profile_ok=legacy or state.get("instruction_profile_hash")==profile_hash(root,selected)
        saved_story_signature=state.get("story_images_signature")
        story_images_ok=(saved_story_signature==story_image_choice["signature"] if saved_story_signature
                         else not story_image_choice["enabled"])
        if profile_ok and story_images_ok and _final_valid(pp,state) and all(cache_status(x)=="valid" for x in plan):
            visual=build_library(root); valid=sum(cache_status(x)=="valid" for x in plan)
            verification=json.loads(pp["report"].read_text(encoding="utf-8"))
            speed = _postprocess_speed(root, pp["video"], verification["video"]["duration"], speed_settings, replace, run_log)
            verification.update(video_normal=speed["video_normal"],video_speed=speed["video_speed"],
                                speed_factor=speed["speed_factor"],final_speed_version=speed)
            write_json_atomic(pp["report"],verification); state["report_sha256"]=file_hash(pp["report"])
            state.update(video_speed=speed["video_speed"],speed_factor=speed["speed_factor"],speed_version_status=speed["status"])
            save_state(pp["state"],state)
            if run_log:
                run_log.info(f"Risultato già in cache: {valid} segmenti TTS riutilizzati")
                run_log.info(f"Output: {pp['video'].relative_to(root)}")
                run_log.info(f"Durata audio: {format_duration(verification['audio']['duration'])}; video: {format_duration(verification['video']['duration'])}; differenza: {verification['video']['duration_delta']:.3f}s")
                run_log.info("Elaborazione completata con successo")
            short_result=_run_short_package(root,input_path,slug,pp,state,mock=config["backend"]=="mock",run_log=run_log)
            return {"status":"cached","package_status":"completed","slug":slug,"tts_generated":0,"tts_cached":valid,"visual_cache_hits":sum(x["cache_status"]=="cache_hit" for x in visual["assets"]),"audio":str(pp["audio"].relative_to(root)),"video":str(pp["video"].relative_to(root)),"audio_sha256":file_hash(pp["audio"]),"video_sha256":file_hash(pp["video"]),"episode_pipeline":episode_pipeline,"short":short_result,"short_video":short_result["video"]["video"],"short_subtitles":short_result["video"]["subtitles"],**speed}
        try:
            mark(pp["state"],state,"validated"); write_json_atomic(pp["dialogue"],{"schema_version":1,"source":rel,"input_hash":digest,"entries":entries}); mark(pp["state"],state,"parsed")
            write_json_atomic(pp["tts_plan"],{"schema_version":1,"backend":config["backend"],"model":config["model"]["id"],"segments":_jsonable_plan(plan)}); mark(pp["state"],state,"tts_planned")
            mark(pp["state"],state,"tts_running")
            if run_log: run_log.set_phase("TTS"); run_log.info("Avvio generazione TTS")
            generated=generate(root,plan,config,config["backend"],state_path=pp["state"]); plan=[dict(x,cache_status=cache_status(x)) for x in plan]
            valid=[x["index"] for x in plan if x["cache_status"]=="valid"]; missing=[x["index"] for x in plan if x["cache_status"]!="valid"]
            if missing: raise StorycastError(f"Segmenti mancanti o invalidi dopo TTS: {missing}")
            mark(pp["state"],state,"tts_complete",segments_valid=valid,segments_missing=[])
            if run_log: run_log.info(f"TTS completato: {len(plan)} segmenti, {generated['cached']} cache, {generated['generated']} generati, {len(missing)} falliti")
            qc=technical_qc(pp,plan,config); needing=[x["index"] for x in qc["segments"] if x["qc_state"]!="passed"]
            if qc["status"]=="failed": raise StorycastError(f"QC tecnico bloccante sui segmenti: {[x['index'] for x in qc['segments'] if x['qc_state']=='failed']}")
            if run_log and needing: run_log.warning(f"QC audio: {len(needing)} segmenti richiedono revisione")
            mark(pp["state"],state,"audio_qc_complete",segments_needing_review=needing)
            reviews=_review_data(pp,plan); not_approved=[int(k) for k,v in reviews["segments"].items() if v["review_state"]!="approved"]
            if review_audio and not_approved:
                mark(pp["state"],state,"awaiting_review",final_status="awaiting_review")
                if run_log: run_log.warning(f"Elaborazione in attesa di revisione: {len(not_approved)} segmenti")
                return {"status":"awaiting_review","slug":slug,"segments":segment_list(root,slug),"not_approved":not_approved,"tts":generated}
            audio_cached=(pp["audio"].is_file() and pp["manifest"].is_file() and pp["timeline"].is_file()
                          and state.get("audio_sha256")==file_hash(pp["audio"])
                          and state.get("manifest_sha256")==file_hash(pp["manifest"])
                          and state.get("timeline_sha256")==file_hash(pp["timeline"]))
            if audio_cached:
                merged=json.loads(pp["manifest"].read_text(encoding="utf-8")); merged["cache_status"]="valid"
            else:
                merged=merge_audio(root,plan,config,output_rel=pp["audio"].relative_to(root).as_posix(),manifest_rel=pp["manifest"].relative_to(root).as_posix(),timeline_rel=pp["timeline"].relative_to(root).as_posix(),input_path=input_path)
            mark(pp["state"],state,"audio_merged",audio_complete=pp["audio"].relative_to(root).as_posix(),audio_sha256=file_hash(pp["audio"]),manifest_sha256=file_hash(pp["manifest"])); mark(pp["state"],state,"timeline_complete",timeline=pp["timeline"].relative_to(root).as_posix(),timeline_sha256=file_hash(pp["timeline"]))
            if run_log: run_log.set_phase("unione audio"); run_log.info(f"Timeline audio completata: durata {format_duration(merged['total_duration'])}")
            manifest=build_library(root)
            plan_cached=pp["visual_plan"].is_file() and state.get("visual_plan_sha256")==file_hash(pp["visual_plan"])
            visual_plan=json.loads(pp["visual_plan"].read_text(encoding="utf-8")) if plan_cached else plan_library(root,manifest=manifest,timeline_path=pp["timeline"],output_path=pp["visual_plan"].relative_to(root))
            mark(pp["state"],state,"visual_plan_complete",visual_plan=pp["visual_plan"].relative_to(root).as_posix(),visual_plan_sha256=file_hash(pp["visual_plan"]))
            if run_log:
                run_log.set_phase("timeline visiva"); run_log.info(f"Timeline visiva completata: {len(visual_plan['scenes'])} scene")
                for fallback in visual_plan.get("fallbacks",[]): run_log.warning(f"Fallback visivo: {fallback}")
            mark(pp["state"],state,"rendering")
            if run_log: run_log.set_phase("montaggio video"); run_log.info("Avvio montaggio video")
            rendered=render_library(root,timeline_path=pp["timeline"],audio_path=pp["audio"],final_path=pp["video"],plan_path=pp["visual_plan"],scene_dir=pp["scenes"],scene_manifest_path=pp["scene_manifest"],backup_dir=pp["backups"],progress_logger=run_log,story_images=story_image_choice["images"])
            insert_count=len(visual_plan.get("story_images",[])) if visual_plan else 0
            visual_plan=json.loads(pp["visual_plan"].read_text(encoding="utf-8"))
            insert_count=len(visual_plan.get("story_images",[]))
            print(f"[storycast] immagini storia: {insert_count} inserti pianificati",flush=True)
            mark(pp["state"],state,"video_complete",video_final=pp["video"].relative_to(root).as_posix(),video_sha256=file_hash(pp["video"]),scenes_rendered=list(range(1,len(visual_plan["scenes"])+1)))
            verification=verify_library_video(root,final_path=pp["video"],audio_path=pp["audio"],plan_path=pp["visual_plan"],frame_dir=pp["frames"],verification_path=pp["verification"],require_all_assets=False)
            speed = _postprocess_speed(root, pp["video"], verification["video_duration"], speed_settings, replace or not story_images_ok, run_log)
            if run_log:
                run_log.info("Video completato")
                run_log.info(f"Output: {pp['video'].relative_to(root)}")
                run_log.info(f"Durata audio: {format_duration(verification['audio_duration'])}; video: {format_duration(verification['video_duration'])}; differenza: {verification['duration_delta']:.3f}s")
            state.update(main_episode="completed", package="partial", short_audio="ready", short_video="ready")
            save_state(pp["state"],state)
            short_result=_run_short_package(root,input_path,slug,pp,state,mock=config["backend"]=="mock",run_log=run_log)
            report={"schema_version":1,"slug":slug,"status":"verified","package_status":"completed","input":rel,"input_hash":digest,"backend":config["backend"],"model":config["model"]["id"],"instruction_profile":selected,"instruction_language":next(iter(voices.values()))["instruction_language"],"spoken_language":"Italian","segments":len(plan),"voices":{x["speaker"]:x["voice"] for x in plan},"tts":generated,"qc":qc,"audio":{"path":pp["audio"].relative_to(root).as_posix(),"sha256":file_hash(pp["audio"]),"duration":merged["total_duration"]},"video":{"path":pp["video"].relative_to(root).as_posix(),"sha256":file_hash(pp["video"]),"duration":verification["video_duration"],"duration_delta":verification["duration_delta"]},"video_normal":speed["video_normal"],"video_speed":speed["video_speed"],"speed_factor":speed["speed_factor"],"final_speed_version":speed,"episode_pipeline":episode_pipeline,"short":short_result,"short_video":short_result["video"]["video"],"short_subtitles":short_result["video"]["subtitles"],"visual":{"scenes":len(visual_plan["scenes"]),"assets":sorted({x["asset_id"] for x in visual_plan["scenes"]}),"render":rendered},"completed_at":now()}
            write_json_atomic(pp["report"],report); state["report_sha256"]=file_hash(pp["report"]); state["manifest_sha256"]=file_hash(pp["manifest"]); state["timeline_sha256"]=file_hash(pp["timeline"]); state["visual_plan_sha256"]=file_hash(pp["visual_plan"])
            mark(pp["state"],state,"verified",final_status="verified",errors=[],video_speed=speed["video_speed"],speed_factor=speed["speed_factor"],speed_version_status=speed["status"],story_images_signature=story_image_choice["signature"],story_images_enabled=story_image_choice["enabled"],story_images_count=insert_count)
            if run_log: run_log.set_phase("completata"); run_log.info("Elaborazione completata con successo")
            return report
        except Exception as exc:
            state["errors"].append({"at":now(),"phase":state.get("phase"),"message":str(exc)})
            if state.get("main_episode")=="completed" and state.get("package")=="partial":
                state["phase"]="video_complete"; state["final_status"]="partial"
            else:
                state["phase"]="failed"; state["final_status"]="failed"
            save_state(pp["state"],state); raise


def load_story(root: Path, slug: str, mock=False):
    pp=paths(root,slug); state=load_state(pp["state"])
    if not state: raise StorycastError(f"Storia inesistente: {slug}")
    source=root/state["input_original"]
    if not source.is_file() or file_hash(source)!=state["input_hash"]:
        candidates=list(pp["source"].glob("*.txt")); source=candidates[0] if candidates and file_hash(candidates[0])==state["input_hash"] else source
    selected,legacy=_story_profile(root,state)
    config,entries,plan,voices=context(root,source,slug,mock or state.get("model",{}).get("backend")=="mock",selected,legacy)
    return pp,state,source,config,entries,plan,voices


def segment_list(root: Path, slug: str) -> list[dict]:
    pp,state,source,config,entries,plan,voices=load_story(root,slug); review=_review_data(pp,plan); rows=[]
    for item in plan:
        info=wav_info(item["wav_path"]) if item["wav_path"].is_file() else None
        rows.append({"index":item["index"],"speaker":item["speaker"],"voice":item["voice"],"text":item["text"],"wav":item["wav_path"].relative_to(root).as_posix(),"duration":info["duration"] if info else None,"sha256":info["wav_hash"] if info else None,"cache":cache_status(item),**review["segments"][str(item["index"])]})
    return rows


def review_set(root: Path, slug: str, index: int, value: str) -> dict:
    pp,state,source,config,entries,plan,voices=load_story(root,slug); item=next((x for x in plan if x["index"]==index),None)
    if not item: raise StorycastError(f"Indice inesistente: {index}")
    info=wav_info(item["wav_path"]); data=_review_data(pp,plan); row=data["segments"][str(index)]
    if value not in {"pending_review","approved","rejected"}: raise StorycastError(f"Stato revisione non valido: {value}")
    row.update(review_state=value,reviewed_wav_hash=info["wav_hash"] if value in {"approved","rejected"} else None,reviewed_at=now()); write_json_atomic(pp["review"],data); return row


def regenerate_story(root: Path, slug: str, index: int, alternate=False, prudent=False, dry_run=False) -> dict:
    pp,state,source,config,entries,plan,voices=load_story(root,slug); item=next((x for x in plan if x["index"]==index),None)
    if not item: raise StorycastError(f"Indice inesistente: {index}")
    selected=dict(item); default=item["seed"]
    if alternate:
        selected["seed"]=default+int(config["verification"].get("alternate_seed_offset",100003))
        selected.update(generation_hashes(voice=selected["voice"],language=selected["language"],instruction=selected["instruction"],parameters=selected["parameters"],effective_seed=selected["seed"],default_seed=default,alternate_seed=True,model_hash=selected["model_hash"],backend=config["backend"],text_hash=selected["text_hash"],instruction_profile=None if selected.get("legacy_hash_compat") else selected.get("instruction_profile"),instruction_language=None if selected.get("legacy_hash_compat") else selected.get("instruction_language"),spoken_language=None if selected.get("legacy_hash_compat") else selected.get("spoken_language")))
    if prudent: selected["chunks"]=split_text(selected["text"],int(config["text"]["prudent_max_words"]),int(config["text"]["prudent_hard_limit_words"]))
    backup=pp["backups"]/f"segment_{index:04d}_{stamp()}"
    preview={"slug":slug,"index":index,"speaker":selected["speaker"],"voice":selected["voice"],"seed":selected["seed"],"alternate_seed":alternate,"wav":selected["wav_path"].relative_to(root).as_posix(),"metadata":selected["metadata_path"].relative_to(root).as_posix(),"backup":backup.relative_to(root).as_posix(),"dry_run":dry_run}
    if dry_run: return preview
    backup.mkdir(parents=True); before={x["index"]:file_hash(x["wav_path"]) for x in plan if x["wav_path"].is_file()}
    for path in (item["wav_path"],item["metadata_path"]):
        if path.is_file(): shutil.copy2(path,backup/path.name)
    result=generate(root,[selected],config,config["backend"],force_index=index,state_path=pp["state"])
    meta=json.loads(selected["metadata_path"].read_text(encoding="utf-8")); meta.update(alternate_seed=alternate,prudent_mode=prudent,seed_origin=selected["seed_origin"],previous_backup=backup.relative_to(root).as_posix()); write_json_atomic(selected["metadata_path"],meta)
    for other in plan:
        if other["index"]!=index and other["index"] in before and file_hash(other["wav_path"])!=before[other["index"]]: raise StorycastError("Rigenerazione ha modificato un altro WAV")
    review_set(root,slug,index,"pending_review"); state["completed_phases"]=[x for x in state["completed_phases"] if PHASES.index(x)<=PHASES.index("tts_complete")]; mark(pp["state"],state,"awaiting_review",final_status="awaiting_review")
    return {**preview,"result":result,"sha256":file_hash(selected["wav_path"])}


def cleanup_inventory(root: Path, slug: str) -> dict:
    pp=paths(root,slug); rows=[]
    categories=(("essential",[pp["state"],pp["dialogue"],pp["tts_plan"],pp["short_state"]]),("valid_wav",list(pp["segments"].glob("*.wav"))+list(pp["short_segments"].glob("*.wav"))),("metadata",list(pp["segment_metadata"].glob("*.json"))+list(pp["short_segment_metadata"].glob("*.json"))+[pp["review"],pp["qc"]]),("final_output",[pp["audio"],pp["video"],pp["manifest"],pp["timeline"],pp["visual_plan"],pp["report"],pp["short_audio"],pp["short_timeline"],pp["short_manifest"]]),("rebuildable_cache",list(pp["visual"].glob("*.json"))),("temporary_scenes",list(pp["scenes"].glob("*"))),("verification_frames",list(pp["frames"].glob("*.png"))),("backups",list(pp["backups"].rglob("*"))))
    categories[3][1].extend([pp["short_video"],pp["short_subtitles"]])
    for category,candidates in categories:
        for path in sorted(set(x for x in candidates if x.is_file())): rows.append({"category":category,"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size})
    return {"slug":slug,"files":rows,"deletable_only_with_yes":[x for x in rows if x["category"] in {"rebuildable_cache","temporary_scenes","verification_frames"}]}


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="storycast-universale"); p.add_argument("command",choices=("genera","precheck","short-audio","short-video","short-status","short-list-segments","stato","piano","segmenti","approva","rifiuta","rigenera","riprendi","verifica","pulisci","cpu-cooldown-status","cpu-cooldown-check","cpu-cooldown-plan","diagnostica-segmento","verifica-segmenti","segmenti-sospetti","ripara-segmento","ripara-sospetti")); p.add_argument("positional_input",nargs="?"); p.add_argument("--input"); p.add_argument("--nome"); p.add_argument("--indice",type=int); p.add_argument("--review-audio",action="store_true"); p.add_argument("--dry-run",action="store_true"); p.add_argument("--mock",action="store_true"); p.add_argument("--alternate-seed",action="store_true"); p.add_argument("--prudent",action="store_true"); p.add_argument("--yes",action="store_true"); p.add_argument("--sostituisci",action="store_true"); p.add_argument("--refresh-voice-instructions",action="store_true"); p.add_argument("--final-speed","--velocita",dest="final_speed",type=float,help="velocità della variante principale (default da config/render.yaml: 1.15)"); p.add_argument("--story-images",choices=("ask","yes","no"),default="ask",help="uso immagini in assets/story_images (default: ask)"); p.add_argument("--no-speed-version",action="store_true",help="non creare la seconda versione accelerata"); return p


def print_completion_summary(result: dict) -> None:
    short = result.get("short", {}).get("video", {})
    print("\nSTORYCAST COMPLETATO\n")
    print(f"Video principale:\n{result['video']}\n")
    print(f"Short:\n{short.get('video', result.get('short_video'))}\n")
    print(f"Sottotitoli Short:\n{short.get('subtitles', result.get('short_subtitles'))}\n")
    if short.get("final_duration") is not None:
        print(f"Durata Short: {short['final_duration']:.3f} s\n")
    print("Stato:\nepisodio principale OK\nShort OK")


def main(argv=None) -> int:
    a=parser().parse_args(argv)
    run_log: RunLogger | None = None
    try:
        if a.positional_input and a.input: raise StorycastError("Specificare l'input una sola volta")
        if a.command in {"genera","piano","precheck","short-audio","short-video","short-status","short-list-segments"}:
            input_path=safe_input(ROOT,a.positional_input or a.input); slug=slug_from(a.nome,input_path)
            if a.command=="precheck":
                print("[storycast] precheck input",flush=True)
                bundle=precheck_episode_bundle(ROOT,input_path)
                print(json.dumps({"status":"valid","main_utterances":len(bundle.main_entries),"short_utterances":len(bundle.short_entries),"episode_pipeline":pipeline_plan(ROOT,slug,bundle)},ensure_ascii=False,indent=2)); return 0
            if a.command=="short-audio": print(json.dumps(run_short_audio(ROOT,input_path,slug,mock=a.mock),ensure_ascii=False,indent=2)); return 0
            if a.command=="short-video": print(json.dumps(render_short_video(ROOT,input_path,slug,mock=a.mock),ensure_ascii=False,indent=2)); return 0
            if a.command=="short-status": print(json.dumps(short_video_status(ROOT,input_path,slug,mock=a.mock),ensure_ascii=False,indent=2)); return 0
            if a.command=="short-list-segments": print(json.dumps(short_list_segments(ROOT,input_path,slug,mock=a.mock),ensure_ascii=False,indent=2)); return 0
            if a.command=="piano" or a.dry_run: print(json.dumps(plan_summary(ROOT,input_path,slug,a.mock),ensure_ascii=False,indent=2)); return 0
            run_log=RunLogger(ROOT,slug,input_path.relative_to(ROOT).as_posix())
            result=run_story(ROOT,input_path,slug,mock=a.mock,review_audio=a.review_audio,replace=a.sostituisci,refresh_voice_instructions=a.refresh_voice_instructions,final_speed=a.final_speed,no_speed_version=a.no_speed_version,run_log=run_log,story_images=a.story_images)
            print_completion_summary(result); print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
        if a.command in {"cpu-cooldown-status","cpu-cooldown-check"} and not a.nome:
            print(json.dumps(cooldown_status(load_tts_config(ROOT)),ensure_ascii=False,indent=2)); return 0
        if not a.nome: raise StorycastError("--nome SLUG è obbligatorio per questo comando")
        slug=slug_from(a.nome,Path(a.nome+".txt"))
        if a.command in {"cpu-cooldown-status","cpu-cooldown-check","cpu-cooldown-plan"}:
            pp,state,source,config,entries,plan,voices=load_story(ROOT,slug)
            result=cooldown_status(config,state); result.update(slug=slug,real_inferences_planned=sum(cache_status(x)!="valid" for x in plan))
            print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
        if a.command in {"diagnostica-segmento","verifica-segmenti","segmenti-sospetti","ripara-sospetti","ripara-segmento"}:
            pp,state,source,config,entries,plan,voices=load_story(ROOT,slug)
            if a.command=="diagnostica-segmento":
                if a.indice is None: raise StorycastError("--indice è obbligatorio")
                item=next((x for x in plan if x["index"]==a.indice),None)
                if not item: raise StorycastError("Indice inesistente")
                try: existing_meta=json.loads(item["metadata_path"].read_text(encoding="utf-8"))
                except (OSError,json.JSONDecodeError): existing_meta={}
                first_report=existing_meta.get("audio_qc_schema_version") != 4
                print(json.dumps(inspect_segment(ROOT,pp,item,config,reported_incomplete=first_report,update=True),ensure_ascii=False,indent=2)); return 0
            report=scan_segments(ROOT,pp,plan,config,update=a.command=="verifica-segmenti")
            if a.command=="verifica-segmenti":
                write_json_atomic(pp["qc"],report); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
            if a.command=="segmenti-sospetti": print(json.dumps({"slug":slug,"suspicious_indices":report["suspicious_indices"]},indent=2)); return 0
            if a.command=="ripara-sospetti":
                preview={"slug":slug,"indices":report["suspicious_indices"],"would_infer":len(report["suspicious_indices"]),"dry_run":a.dry_run}
                if a.dry_run: print(json.dumps(preview,indent=2)); return 0
                raise StorycastError("Riparazione multipla non eseguita: usare ripara-segmento per selezione controllata")
            if a.indice is None: raise StorycastError("--indice è obbligatorio")
            if not a.dry_run and not a.yes: raise StorycastError("Usare --yes per la singola rigenerazione")
            item=next((x for x in plan if x["index"]==a.indice),None)
            if not a.dry_run and item:
                recovered=recover_latest_rejected(ROOT,pp,item,config)
                if recovered:
                    review_set(ROOT,slug,a.indice,"pending_review"); mark(pp["state"],state,"awaiting_review",final_status="awaiting_review")
                    print(json.dumps(recovered,ensure_ascii=False,indent=2)); return 0
            print(json.dumps(regenerate_story(ROOT,slug,a.indice,True,True,a.dry_run),ensure_ascii=False,indent=2)); return 0
        if a.command=="stato": print(json.dumps(load_state(paths(ROOT,slug)["state"]),ensure_ascii=False,indent=2)); return 0
        if a.command=="segmenti": print(json.dumps(segment_list(ROOT,slug),ensure_ascii=False,indent=2)); return 0
        if a.command in {"approva","rifiuta"}:
            if a.indice is None: raise StorycastError("--indice è obbligatorio")
            print(json.dumps(review_set(ROOT,slug,a.indice,"approved" if a.command=="approva" else "rejected"),ensure_ascii=False,indent=2)); return 0
        if a.command=="rigenera":
            if a.indice is None: raise StorycastError("--indice è obbligatorio")
            if not a.dry_run and not a.yes:
                if input("Confermare la rigenerazione reale di un solo segmento? [SI]: ").strip()!="SI": raise StorycastError("Rigenerazione annullata")
            print(json.dumps(regenerate_story(ROOT,slug,a.indice,a.alternate_seed,a.prudent,a.dry_run),ensure_ascii=False,indent=2)); return 0
        if a.command=="riprendi":
            pp,state,source,config,entries,plan,voices=load_story(ROOT,slug,a.mock); print(json.dumps(run_story(ROOT,source,slug,mock=config["backend"]=="mock",review_audio=state.get("final_status")=="awaiting_review",final_speed=a.final_speed,no_speed_version=a.no_speed_version),ensure_ascii=False,indent=2)); return 0
        if a.command=="verifica":
            pp,state,source,config,entries,plan,voices=load_story(ROOT,slug); print(json.dumps({"state":state,"segments":technical_qc(pp,plan,config,write=False),"final_valid":_final_valid(pp,state)},ensure_ascii=False,indent=2)); return 0
        if a.command=="pulisci":
            inventory=cleanup_inventory(ROOT,slug); print(json.dumps(inventory,ensure_ascii=False,indent=2))
            if a.dry_run: print("Dry-run: nessun file cancellato."); return 0
            if not a.yes: raise StorycastError("Pulizia annullata: usare --yes dopo aver esaminato il dry-run")
            for row in inventory["deletable_only_with_yes"]: (ROOT/row["path"]).unlink()
            return 0
        return 0
    except KeyboardInterrupt:
        if run_log:
            run_log.error(f"Fase {run_log.phase}: elaborazione interrotta dall'utente")
            run_log.error("Elaborazione interrotta con codice 130")
        print("ERRORE: elaborazione interrotta dall'utente",file=sys.stderr); return 130
    except (StorycastError,OSError,ValueError,subprocess.CalledProcessError) as exc:
        if run_log:
            run_log.error(f"Fase {run_log.phase}: {exc}")
            run_log.error("Elaborazione interrotta con codice 1")
        print(f"ERRORE: {exc}",file=sys.stderr); return 1


if __name__=="__main__": raise SystemExit(main())
