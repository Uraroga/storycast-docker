from __future__ import annotations
import json
import datetime as dt
import shutil
from pathlib import Path
from typing import Any
from .core import StorycastError, load_characters, parse_dialogue
from .tts import build_plan, cache_status, generate, load_tts_config, load_voices, merge_audio, verify_plan, wav_info
from .visual import build_assets, plan_shots, render_video, verify_video, sha256

NAME="storycast_episode_01"
INPUT=Path("input/storycast_episode_01.txt")
AUDIO_DIR=Path("work/episode_01/audio_segments")
META_DIR=Path("work/episode_01/metadata/audio_segments")
AUDIO=Path("output/storycast_episode_01_audio.wav")
MANIFEST=Path("work/metadata/storycast_episode_01_audio_manifest.json")
TIMELINE=Path("work/timeline/storycast_episode_01_timeline.json")
SHOT_PLAN=Path("work/visual/storycast_episode_01_shot_plan.json")
VIDEO=Path("output/storycast_episode_01_video.mp4")

def context(root:Path):
    chars=load_characters(root); voices=load_voices(root,chars,instruction_profile="italian_legacy",legacy_hash_compat=True); config=load_tts_config(root)
    expected=config["verification"].get("expected_backend","real")
    if config["backend"]!=expected or expected!="real": raise StorycastError("Backend TTS episodio ambiguo o diverso da real")
    available=set(config["verification"].get("available_voices",[]))
    if not available: raise StorycastError("Elenco voci disponibili nel modello mancante")
    for speaker,voice in voices.items():
        if not voice.get("voice") or voice["voice"] not in available: raise StorycastError(f"Voce richiesta non disponibile nel modello per {speaker}: {voice['voice']}")
    entries=parse_dialogue(root/INPUT,chars)
    if not 8<=len(entries)<=14: raise StorycastError("L'episodio deve contenere 8-14 battute")
    plan=build_plan(root,entries,voices,config)
    for item in plan:
        stem=f"{item['index']:04d}_{item['speaker']}"; item["wav_path"]=root/AUDIO_DIR/f"{stem}.wav"; item["metadata_path"]=root/META_DIR/f"{stem}.json"; item["cache_status"]=cache_status(item)
    return config,entries,plan

def show_plan(root:Path)->dict:
    config,entries,plan=context(root); counts={x:sum(i["cache_status"]==x for i in plan) for x in ("valid","missing","regenerate")}
    print(f"Input: {INPUT}; battute: {len(entries)}; backend: real; modello: {config['model']['id']}")
    for x in plan: print(f"{x['index']:04d} {x['speaker']} voice={x['voice']} seed={x['seed']} cache={x['cache_status']} -> {x['wav_path'].relative_to(root)}")
    known=sum(wav_info(x["wav_path"])["duration"] for x in plan if x["cache_status"]=="valid")
    estimate=known+sum(max(2.5,len(x["text"].split())*.34) for x in plan if x["cache_status"]!="valid")+sum(float(x["pause_after"] or config["audio"]["utterance_pause_seconds"]) for x in plan[:-1])
    print(f"Durata {'nota' if counts['valid']==len(plan) else 'stimata'}: {estimate:.1f}s; scene previste: circa {len(plan)*2+1}")
    print(f"Asset: group_01_wide e crop medium/closeup configurati; output: {AUDIO}, {MANIFEST}, {TIMELINE}, {SHOT_PLAN}, {VIDEO}")
    return {"entries":len(entries),"counts":counts,"estimated_duration":estimate}

def tts(root:Path,dry_run=False):
    config,_,plan=context(root); show_plan(root)
    if dry_run:
        valid=sum(x["cache_status"]=="valid" for x in plan)
        return {"selected":len(plan),"generated":0,"cached":valid,"would_generate":len(plan)-valid}
    if not dry_run:
        stale=[x for x in plan if x["cache_status"]=="regenerate" and x["wav_path"].is_file()]
        if stale:
            folder=root/"work/episode_01/backups"/dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f"); folder.mkdir(parents=True,exist_ok=True)
            for x in stale:
                shutil.copy2(x["wav_path"],folder/x["wav_path"].name)
                if x["metadata_path"].is_file(): shutil.copy2(x["metadata_path"],folder/x["metadata_path"].name)
            print(f"Backup automatico segmenti precedenti: {folder.relative_to(root)}")
    return generate(root,plan,config,"real")

def audio(root:Path):
    config,_,plan=context(root)
    result=merge_audio(root,plan,config,output_rel=str(AUDIO),manifest_rel=str(MANIFEST),timeline_rel=str(TIMELINE),input_path=root/INPUT)
    if not 30<=result["total_duration"]<=60: raise StorycastError(f"Durata episodio fuori target: {result['total_duration']:.3f}s")
    # Arricchisce il manifest mantenendo compatibilità con lo schema audio.
    for element in result["elements"]:
        if element["type"]=="segment":
            item=next(x for x in plan if x["index"]==element["index"]); meta=json.loads(item["metadata_path"].read_text())
            element.update(voice=item["voice"],file=element["audio_file"],wav_hash=meta["wav_hash"],status="valid")
        else: element.update(speaker=None,voice=None,file=None,wav_hash=None,status="valid")
    from .core import write_json; write_json(root/MANIFEST,result)
    timeline=json.loads((root/TIMELINE).read_text(encoding="utf-8"))
    for item in timeline["entries"]: item["stato_audio"]=item.get("audio_status")
    write_json(root/TIMELINE,timeline)
    return result

def rebuild_after_segment(root:Path,index:int):
    from . import audio_qc
    config,_,plan=context(root)
    if not any(x["index"]==index for x in plan): raise StorycastError(f"Indice battuta inesistente: {index}")
    report=audio_qc.qc(root,plan,config,strict=True)
    if report["status"]=="blocked": raise StorycastError(f"Merge strict bloccato; needs_review: {report.get('needs_review')}")
    backup=audio_qc.backup_outputs(root,index)
    merged=audio(root)
    rendered=render(root)
    checked=check(root,extract=True)
    return {"index":index,"backup":backup.relative_to(root).as_posix(),"audio_hash":merged["wav_hash"],"video_hash":checked["video_sha256"],"verification":checked,"render":rendered}

def visual(root:Path,dry_run=False): return plan_shots(root,dry_run,timeline_path=root/TIMELINE,output_path=SHOT_PLAN)
def render(root:Path,dry_run=False): return render_video(root,dry_run,timeline_path=root/TIMELINE,audio_path=root/AUDIO,final_path=root/VIDEO,shot_plan_path=SHOT_PLAN,scene_subdir="scenes_episode_01")
def check(root:Path,extract=True):
    config,entries,plan=context(root); checks=verify_plan(plan,config)
    if any(x["status"]!="valid" for x in checks): raise StorycastError("Segmenti episodio incompleti o invalidi")
    ai=wav_info(root/AUDIO)
    if not 30<=ai["duration"]<=60: raise StorycastError("Audio episodio fuori target 30-60 secondi")
    result=verify_video(root,extract,final_path=root/VIDEO,audio_path=root/AUDIO,frames_subdir="verification_frames_episode_01",verification_name="storycast_episode_01_video_verification.json",frame_count=5)
    result.update(audio_sha256=sha256(root/AUDIO),segments=len(entries)); return result

def status(root:Path)->int:
    config,_,plan=context(root); states=verify_plan(plan,config)
    for x in states: print(f"{x['index']:04d} {x['speaker']}: {x['status']}")
    print(f"audio={'presente' if (root/AUDIO).is_file() else 'assente'} timeline={'presente' if (root/TIMELINE).is_file() else 'assente'} video={'presente' if (root/VIDEO).is_file() else 'assente'}")
    return 0

def clean(root:Path,dry_run:bool,yes:bool)->int:
    folders=[root/AUDIO_DIR.parent,root/"work/visual/scenes_episode_01",root/"work/visual/verification_frames_episode_01"]
    exact=[root/AUDIO,root/VIDEO,root/MANIFEST,root/TIMELINE,root/SHOT_PLAN,root/"work/visual/manifests/storycast_episode_01_video_verification.json"]
    files=sorted(set([p for f in folders if f.exists() for p in f.rglob("*") if p.is_file()]+[p for p in exact if p.is_file()]))
    for p in files: print(p.relative_to(root))
    if dry_run: print(f"Dry-run: {len(files)} file episodio; nessuna eliminazione."); return 0
    if not yes: print("Pulizia annullata: usare --yes."); return 2
    for p in files:p.unlink()
    print(f"Eliminati {len(files)} file episodio."); return 0
