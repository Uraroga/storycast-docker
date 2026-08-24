from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

from .core import StorycastError, write_json

ASSET_MANIFEST = Path("work/visual/manifests/visual_assets_manifest.json")
SHOT_PLAN = Path("work/visual/test_reale_shot_plan.json")
FINAL_VIDEO = Path("output/test_reale_storycast_video.mp4")

def load_json_config(path: Path) -> dict[str, Any]:
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise StorycastError(f"Configurazione visiva non valida: {path}: {exc}") from exc
    if data.get("schema_version") != 1: raise StorycastError(f"schema_version non supportata: {path}")
    return data

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def png_size(path: Path) -> tuple[int,int]:
    with path.open("rb") as f:
        if f.read(8)!=b"\x89PNG\r\n\x1a\n": raise StorycastError(f"PNG assente o corrotto: {path}")
        f.seek(16); return struct.unpack(">II",f.read(8))

def image_size(path: Path) -> tuple[int,int]:
    """Legge le dimensioni PNG/JPEG senza dipendenze Python esterne."""
    try:
        with path.open("rb") as f:
            head=f.read(24)
            if head[:8]==b"\x89PNG\r\n\x1a\n" and head[12:16]==b"IHDR":
                return struct.unpack(">II",head[16:24])
            if head[:2]!=b"\xff\xd8":
                raise StorycastError(f"Immagine non leggibile o formato non supportato: {path}")
            f.seek(2)
            while True:
                marker=f.read(1)
                while marker==b"\xff": marker=f.read(1)
                if not marker: break
                length_raw=f.read(2)
                if len(length_raw)!=2: break
                length=struct.unpack(">H",length_raw)[0]
                if marker[0] in {0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF}:
                    data=f.read(5)
                    if len(data)==5:
                        height,width=struct.unpack(">HH",data[1:5]); return width,height
                    break
                f.seek(max(0,length-2),1)
    except OSError as exc:
        raise StorycastError(f"Immagine non leggibile: {path}: {exc}") from exc
    raise StorycastError(f"Immagine JPEG corrotta: {path}")

def validate_crop(crop: list[float]) -> None:
    if len(crop)!=4 or any(not isinstance(x,(int,float)) for x in crop): raise StorycastError("Ritaglio normalizzato non valido")
    x,y,w,h=map(float,crop)
    if min(x,y,w,h)<0 or w<=0 or h<=0 or x+w>1.000001 or y+h>1.000001: raise StorycastError(f"Coordinate normalizzate fuori intervallo: {crop}")

def load_visual(root: Path) -> tuple[dict,dict]:
    visual=load_json_config(root/"config/visual_assets.yaml"); render=load_json_config(root/"config/render.yaml")
    source=root/visual["group"]["source"]
    if not source.is_file(): raise StorycastError(f"Master visivo mancante: {source}")
    validate_crop(visual["group"]["wide_crop"])
    seen=set()
    for char in visual.get("characters",[]):
        if char["id"] in seen: raise StorycastError(f"Personaggio visivo duplicato: {char['id']}")
        seen.add(char["id"])
        if not (root/char["source"]).is_file(): raise StorycastError(f"Asset sorgente mancante: {char['source']}")
        for key in ("scene_area","medium_crop","closeup_crop"): validate_crop(char[key])
        if not char.get("shots"): raise StorycastError(f"Nessuna inquadratura: {char['id']}")
    if not seen: raise StorycastError("Nessun personaggio visivo configurato")
    v=render["video"]
    if min(v["width"],v["height"],v["fps"])<=0: raise StorycastError("Formato video non valido")
    return visual,render

def asset_specs(root: Path, visual: dict, render: dict) -> list[dict]:
    w,h=render["video"]["width"],render["video"]["height"]
    specs=[{"id":visual["group"]["id"]+"_wide","speaker":None,"shot_type":"wide","source":visual["group"]["source"],"crop":visual["group"]["wide_crop"]}]
    for c in sorted((x for x in visual["characters"] if x.get("enabled",True)),key=lambda x:(-x.get("priority",0),x["id"])):
        for shot in c["shots"]:
            specs.append({"id":f"{c['id']}_{shot}","speaker":c["speaker"],"shot_type":shot,"source":c["source"],"crop":c[f"{shot}_crop"]})
    for s in specs: s.update({"output":f"work/visual/derived/{s['id']}.png","final_resolution":[w,h]})
    return specs

def pixel_crop(crop:list[float], size:tuple[int,int]) -> list[int]:
    sw,sh=size; x,y,w,h=crop
    px=[round(x*sw),round(y*sh),round(w*sw),round(h*sh)]
    px[2]-=px[2]%2; px[3]-=px[3]%2
    return px

def expected_key(master_hash:str,crop:list[float],resolution:list[int])->str:
    return hashlib.sha256(json.dumps([master_hash,crop,resolution],separators=(",",":"),sort_keys=True).encode()).hexdigest()

def build_assets(root:Path,dry_run=False,runner=subprocess.run)->dict:
    visual,render=load_visual(root); specs=asset_specs(root,visual,render)
    old={x["id"]:x for x in json.loads((root/ASSET_MANIFEST).read_text()).get("assets",[])} if (root/ASSET_MANIFEST).is_file() else {}
    records=[]
    for s in specs:
        src=root/s["source"]; out=root/s["output"]; source_size=png_size(src); mh=sha256(src); crop_px=pixel_crop(s["crop"],source_size); key=expected_key(mh,s["crop"],s["final_resolution"])
        prior=old.get(s["id"],{}); cached=out.is_file() and prior.get("cache_key")==key and prior.get("derived_sha256")==sha256(out)
        action="cache_hit" if cached else "create"
        print(f"{s['id']}: {s['source']} crop={s['crop']} px={crop_px} -> {s['output']} {s['final_resolution']} [{action}]")
        if not dry_run and not cached:
            out.parent.mkdir(parents=True,exist_ok=True)
            x,y,w,h=crop_px; ow,oh=s["final_resolution"]
            runner(["ffmpeg","-v","error","-y","-i",str(src),"-vf",f"crop={w}:{h}:{x}:{y},scale={ow}:{oh}:flags=lanczos","-frames:v","1",str(out)],check=True)
        dh=sha256(out) if out.is_file() else None
        records.append({**s,"normalized_crop":s.pop("crop"),"pixel_crop":crop_px,"source_resolution":list(source_size),"source_master_sha256":mh,"derived_sha256":dh,"cache_key":key,"created_at":prior.get("created_at") if cached else (dt.datetime.now(dt.timezone.utc).isoformat() if not dry_run else None),"validation_status":"valid" if dh else "planned","cache_status":action})
    manifest={"schema_version":1,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),"assets":records}
    if not dry_run: write_json(root/ASSET_MANIFEST,manifest)
    return manifest

def _append_scene(scenes:list,start:float,end:float,speaker,shot,asset,motion,reason,render):
    if end-start<1e-6:return
    scenes.append({"index":len(scenes)+1,"start":round(start,6),"end":round(end,6),"duration":round(end-start,6),"speaker":speaker,"shot_type":shot,"visual_asset":asset,"crop":"precomputed","camera_movement":motion,"movement_intensity":render["camera"]["intensity"],"transition":"cut","reason":reason,"status":"planned"})

def plan_shots(root:Path,dry_run=False,timeline_path:Path|None=None,output_path:Path|None=None)->dict:
    visual,render=load_visual(root); timeline_path=timeline_path or root/"work/timeline/test_reale_timeline.json"
    data=json.loads(timeline_path.read_text(encoding="utf-8")); entries=data["entries"]
    if not entries: raise StorycastError("Timeline audio vuota")
    chars={x["speaker"]:x for x in visual["characters"] if x.get("enabled",True)}; total=max(float(x["end"])+float(x.get("pause_after",0) or 0) for x in entries)
    cfg=render["planner"]; opening=min(cfg["opening_seconds"],total/5); closing=min(cfg["closing_seconds"],total/5); scenes=[]
    _append_scene(scenes,0,opening,None,"wide",visual["group"]["id"]+"_wide","slow_zoom_in","apertura con entrambi",render)
    cursor=opening
    motions=["pan_horizontal","slow_zoom_in","slow_zoom_out"]; shot_counts={speaker:0 for speaker in chars}
    for i,e in enumerate(entries):
        start=max(cursor,float(e["start"])); end=min(float(e["end"]),total-closing if i==len(entries)-1 else float(e["end"])); speaker=e["speaker"]
        if speaker not in chars: raise StorycastError(f"Speaker senza asset visivo: {speaker}")
        if start>end: start=end
        available=chars[speaker]["shots"]; shot=available[shot_counts[speaker]%len(available)]; shot_counts[speaker]+=1; _append_scene(scenes,start,end,speaker,shot,f"{speaker}_{shot}",motions[i%len(motions)],"personaggio attualmente parlante",render); cursor=end
        next_start=float(entries[i+1]["start"]) if i+1<len(entries) else total-closing
        pause_end=min(next_start,total-closing)
        _append_scene(scenes,cursor,pause_end,None,"wide",visual["group"]["id"]+"_wide","static","pausa tra le battute",render); cursor=max(cursor,pause_end)
    if cursor<total-closing: _append_scene(scenes,cursor,total-closing,None,"wide",visual["group"]["id"]+"_wide","static","intervallo senza parlato",render)
    _append_scene(scenes,max(cursor,total-closing),total,None,"wide",visual["group"]["id"]+"_wide","slow_zoom_out","conclusione con entrambi",render)
    # Rendi contigui e quantizza al frame, assegnando l'ultimo frame alla durata audio.
    fps=render["video"]["fps"]; boundaries=[0]+[round(s["end"]*fps)/fps for s in scenes[:-1]]+[total]
    for i,s in enumerate(scenes): s.update(index=i+1,start=round(boundaries[i],6),end=round(boundaries[i+1],6),duration=round(boundaries[i+1]-boundaries[i],6))
    if any(s["duration"]<=0 for s in scenes): raise StorycastError("Piano con scena nulla")
    plan={"schema_version":1,"timeline_source":str(timeline_path.relative_to(root)),"total_duration":round(total,6),"fps":fps,"seed":cfg["seed"],"scenes":scenes}
    for s in scenes: print(f"{s['index']:02d} {s['start']:.3f}-{s['end']:.3f}s {s['shot_type']} {s['visual_asset']} {s['camera_movement']}")
    if not dry_run: write_json(root/(output_path or SHOT_PLAN),plan)
    return plan

def ffmpeg_scene_command(root:Path,s:dict,render:dict,out:Path)->list[str]:
    fps=render["video"]["fps"]; frames=max(1,round(s["duration"]*fps)); z=render["camera"]["max_zoom"]
    motion=s["camera_movement"]
    if motion=="slow_zoom_in": expr=f"min(1+on*({z}-1)/{max(frames-1,1)},{z})"
    elif motion=="slow_zoom_out": expr=f"max({z}-on*({z}-1)/{max(frames-1,1)},1)"
    else: expr=str(z if motion=="pan_horizontal" else 1.0)
    if motion=="pan_horizontal": x=f"(iw-iw/zoom)*on/{max(frames-1,1)}"
    else:x="iw/2-iw/zoom/2"
    vf=f"zoompan=z='{expr}':x='{x}':y='ih/2-ih/zoom/2':d={frames}:s={render['video']['width']}x{render['video']['height']}:fps={fps},format={render['video']['pixel_format']}"
    return ["ffmpeg","-v","error","-y","-loop","1","-i",str(root/f"work/visual/derived/{s['visual_asset']}.png"),"-vf",vf,"-frames:v",str(frames),"-an","-c:v",render["video"]["codec"],"-preset",render["video"]["preset"],"-crf",str(render["video"]["crf"]),str(out)]

def render_video(root:Path,dry_run=False,runner=subprocess.run,*,timeline_path:Path|None=None,audio_path:Path|None=None,final_path:Path|None=None,shot_plan_path:Path|None=None,scene_subdir:str="scenes")->dict:
    _,render=load_visual(root); plan=plan_shots(root,dry_run=dry_run,timeline_path=timeline_path,output_path=shot_plan_path); manifest=build_assets(root,dry_run=dry_run)
    audio=audio_path or root/"output/test_reale_storycast_audio.wav"; final=final_path or root/FINAL_VIDEO
    if not audio.is_file(): raise StorycastError(f"Audio reale mancante: {audio}")
    scene_dir=root/"work/visual"/scene_subdir; commands=[]
    for s in plan["scenes"]: commands.append(ffmpeg_scene_command(root,s,render,scene_dir/f"scene_{s['index']:04d}.mp4"))
    shown=final.relative_to(root)
    print(f"Output: {shown}; {render['video']['width']}x{render['video']['height']} {render['video']['fps']}fps H.264/yuv420p; AAC mono; durata {plan['total_duration']:.3f}s")
    for c in commands: print("FFmpeg:"," ".join(c))
    if dry_run:return {"status":"dry-run","commands":commands,"plan":plan,"assets":manifest}
    scene_dir.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    for c in commands: runner(c,check=True)
    concat=scene_dir/"concat.txt"; concat.write_text("".join(f"file 'scene_{s['index']:04d}.mp4'\n" for s in plan["scenes"]),encoding="utf-8")
    silent=scene_dir/"test_reale_silent.mp4"; runner(["ffmpeg","-v","error","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(silent)],check=True)
    if final.exists():
        backup=root/"output/backups"/f"{final.stem}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.mp4"; backup.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(final,backup); print(f"Backup: {backup.relative_to(root)}")
    # Il remux con copia può conservare piccoli buchi di timestamp tra segmenti.
    # fps+apad video producono esattamente ceil(durata_audio*fps) frame contigui.
    target_frames=math.ceil(plan["total_duration"]*render["video"]["fps"])
    runner(["ffmpeg","-v","error","-y","-i",str(silent),"-i",str(audio),"-map","0:v:0","-map","1:a:0","-vf",f"fps={render['video']['fps']},tpad=stop_mode=clone:stop_duration=1,format={render['video']['pixel_format']}","-frames:v",str(target_frames),"-c:v",render["video"]["codec"],"-preset",render["video"]["preset"],"-crf",str(render["video"]["crf"]),"-c:a",render["audio"]["codec"],"-b:a",render["audio"]["bitrate"],"-ac","1","-ar",str(render["audio"]["sample_rate"]),"-movflags","+faststart",str(final)],check=True)
    elapsed=time.monotonic()-started
    return {"status":"rendered","output":str(shown),"sha256":sha256(final),"render_seconds":round(elapsed,3)}

def probe(root:Path,path:Path)->dict:
    raw=subprocess.run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],check=True,text=True,stdout=subprocess.PIPE).stdout
    return json.loads(raw)

def verify_video(root:Path,extract=True,*,final_path:Path|None=None,audio_path:Path|None=None,frames_subdir:str="verification_frames",verification_name:str="video_verification.json",frame_count:int=3)->dict:
    _,expected=load_visual(root)
    final=final_path or root/FINAL_VIDEO
    if not final.is_file(): raise StorycastError("Video finale mancante")
    data=probe(root,final); streams=data["streams"]; v=next((x for x in streams if x["codec_type"]=="video"),None); a=next((x for x in streams if x["codec_type"]=="audio"),None)
    audio=audio_path or root/"output/test_reale_storycast_audio.wav"
    audio_duration=float(probe(root,audio)["format"]["duration"]); video_duration=float(v.get("duration",data["format"]["duration"])); fps=eval(v["r_frame_rate"],{"__builtins__":{}},{})
    ev,ea=expected["video"],expected["audio"]
    checks={"video_codec":v and v["codec_name"]=="h264","audio_codec":a and a["codec_name"]=="aac","resolution":v and [v["width"],v["height"]]==[ev["width"],ev["height"]],"fps":abs(fps-ev["fps"])<.001,"pixel_format":v and v["pix_fmt"]==ev["pixel_format"],"audio_mono":a and a["channels"]==ea["channels"],"sample_rate":a and int(a["sample_rate"])==ea["sample_rate"],"sync_within_frame":abs(audio_duration-video_duration)<=1/ev["fps"]+1e-3}
    frame_results=[]
    if extract:
        folder=root/"work/visual"/frames_subdir; folder.mkdir(parents=True,exist_ok=True)
        points=(("inizio",.1),("meta",audio_duration/2),("fine",max(.1,audio_duration-.2))) if frame_count==3 else (("inizio",.1),("primo_terzo",audio_duration/3),("meta",audio_duration/2),("ultimo_terzo",audio_duration*2/3),("fine",max(.1,audio_duration-.2)))
        for name,t in points:
            out=folder/f"{name}.png"; subprocess.run(["ffmpeg","-v","error","-y","-ss",str(t),"-i",str(final),"-frames:v","1",str(out)],check=True)
            measured=subprocess.run(["ffmpeg","-v","error","-i",str(out),"-vf","signalstats,metadata=print:file=-","-frames:v","1","-f","null","-"],text=True,stderr=subprocess.PIPE,stdout=subprocess.PIPE,check=True)
            raw=measured.stdout+measured.stderr
            yavg=next((float(x.split("=",1)[1]) for x in raw.splitlines() if "lavfi.signalstats.YAVG=" in x),0)
            frame_results.append({"file":str(out.relative_to(root)),"time":round(t,3),"sha256":sha256(out),"yavg":yavg,"valid":out.stat().st_size>100 and yavg>2})
    checks["verification_frames"]=all(x["valid"] for x in frame_results) if extract else True
    result={"status":"passed" if all(checks.values()) else "failed","checks":checks,"audio_duration":audio_duration,"video_duration":video_duration,"duration_delta":abs(audio_duration-video_duration),"video_sha256":sha256(final),"frames":frame_results,"ffprobe":data}
    write_json(root/"work/visual/manifests"/verification_name,result)
    if result["status"]!="passed": raise StorycastError(f"Verifica video fallita: {checks}")
    return result
