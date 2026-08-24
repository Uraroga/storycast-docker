from __future__ import annotations
import html, json, os, shutil, statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .core import StorycastError, write_json
from .tts import canonical_hash, file_hash, generate, normalize_spoken_text, verify_plan, wav_info

STATES={"pending_review","approved","rejected","needs_review"}
STATE_PATH=Path("work/episode_01/review_status.json")
REVIEW_PAGE=Path("output/review_episode_01.html")
def now(): return datetime.now(timezone.utc).isoformat()
def stamp(): return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

def load_states(root,plan):
    saved={}
    p=root/STATE_PATH
    if p.is_file():
        try: saved=json.loads(p.read_text(encoding="utf-8")).get("segments",{})
        except (OSError,json.JSONDecodeError): pass
    segments={}
    for x in plan:
        old=saved.get(str(x["index"]),{}); state=old.get("state","pending_review")
        segments[str(x["index"])]={"state":state if state in STATES else "pending_review","updated_at":old.get("updated_at"),"note":old.get("note"),"wav_hash":old.get("wav_hash")}
    return {"schema_version":1,"episode":"01","segments":segments,"updated_at":now()}
def save_states(root,data): data["updated_at"]=now(); write_json(root/STATE_PATH,data)
def set_state(root,plan,index,state,note=None):
    if state not in STATES: raise StorycastError(f"Stato revisione non valido: {state}")
    item=next((x for x in plan if x["index"]==index),None)
    if not item: raise StorycastError(f"Indice battuta inesistente: {index}")
    info=wav_info(item["wav_path"]); data=load_states(root,plan)
    data["segments"][str(index)]={"state":state,"updated_at":now(),"note":note,"wav_hash":info["wav_hash"]}
    save_states(root,data); return data["segments"][str(index)]

def enrich_metadata(root,plan):
    changed=0
    for x in plan:
        p=x["metadata_path"]
        if not p.is_file(): continue
        m=json.loads(p.read_text(encoding="utf-8"))
        values={"character":x["speaker"],"requested_voice":x["voice"],"backend_voice":m.get("voice",x["voice"]),"effective_instruction":m.get("tts_instruction",x["instruction"]),"config_hash":m.get("voice_config_hash",x["voice_config_hash"])}
        if any(m.get(k)!=v for k,v in values.items()): m.update(values); write_json(p,m); changed+=1
    return changed
def metadata_errors(x,m):
    expected={"index":x["index"],"speaker":x["speaker"],"character":x["speaker"],"original_text":x["text"],"requested_voice":x["voice"],"backend_voice":x["voice"],"effective_instruction":x["instruction"],"language":x["language"],"seed":x["seed"],"model":x["model"],"backend":"real","config_hash":x["voice_config_hash"]}
    return [f"metadata_{k}_mancante_o_incoerente" for k,v in expected.items() if m.get(k)!=v]
def qc(root,plan,config,strict=False,update_states=True):
    base={x["index"]:x for x in verify_plan(plan,config)}
    ratios=[base[x["index"]]["audio"]["duration"]/max(1,len(normalize_spoken_text(x["text"]).split())) for x in plan if base[x["index"]]["audio"]]
    median=statistics.median(ratios) if ratios else 0; states=load_states(root,plan); rows=[]; limits=config["verification"]
    for x in plan:
        check=base[x["index"]]; errors=list(check["errors"]); info=check["audio"]
        try:
            m=json.loads(x["metadata_path"].read_text(encoding="utf-8")); errors+=metadata_errors(x,m)
        except (OSError,json.JSONDecodeError): errors.append("metadata_mancanti_o_illeggibili")
        if info:
            ratio=info["duration"]/max(1,len(normalize_spoken_text(x["text"]).split()))
            if (info["sample_rate"],info["channels"],info["sample_width"])!=(24000,1,2): errors.append("formato_richiesto_non_mono_pcm16_24khz")
            if info["complete_silence"] or info["rms"]==0: errors.append("silenzio_completo")
            if info["frames"]<=0: errors.append("wav_vuoto")
            if info["duration"]>float(limits.get("max_duration_seconds",45)): errors.append("segmento_eccessivamente_lungo")
            if median and ratio>median*float(limits.get("duration_outlier_factor",1.8)): errors.append("durata_anomala_rispetto_agli_altri_segmenti")
        e=states["segments"][str(x["index"])]; state=e["state"]
        if errors and state!="rejected":
            state="needs_review"
            if update_states: e.update(state=state,updated_at=now(),note=", ".join(sorted(set(errors))),wav_hash=info["wav_hash"] if info else None)
        rows.append({"index":x["index"],"speaker":x["speaker"],"state":state,"errors":sorted(set(errors)),"audio":info})
    if update_states: save_states(root,states)
    bad=[x for x in rows if x["state"]=="needs_review"]
    return {"status":"blocked" if strict and bad else "passed","strict":strict,"median_seconds_per_word":median,"segments":rows,"needs_review":[x["index"] for x in bad]}
def recovery_scan(root):
    incomplete=sorted({p.relative_to(root).as_posix() for pat in ("*.tmp","*.part","*.lock") for p in root.rglob(pat) if p.is_file()}); stale=[]
    for p in root.rglob("*.pid"):
        try: os.kill(int(p.read_text().strip()),0)
        except (OSError,ValueError): stale.append(p.relative_to(root).as_posix())
    return {"incomplete_files":incomplete,"stale_pid_files":sorted(stale),"action":"Nessun file viene cancellato automaticamente."}

def segment_inventory(root,plan,config):
    checks={x["index"]:x for x in verify_plan(plan,config)}
    ratios=[]
    for x in plan:
        info=checks[x["index"]]["audio"]
        if info: ratios.append(info["duration"]/max(1,len(normalize_spoken_text(x["text"]).split())))
    median=statistics.median(ratios) if ratios else 0.0
    factor=float(config["verification"].get("duration_outlier_factor",1.8)); result=[]
    for x in sorted(plan,key=lambda value:value["index"]):
        check=checks[x["index"]]; info=check["audio"]; anomalies=list(check["errors"])
        meta=json.loads(x["metadata_path"].read_text(encoding="utf-8")) if x["metadata_path"].is_file() else {}
        if info:
            ratio=info["duration"]/max(1,len(normalize_spoken_text(x["text"]).split()))
            if (info["sample_rate"],info["channels"],info["sample_width"])!=(24000,1,2): anomalies.append("formato_non_mono_pcm16_24khz")
            if info["complete_silence"]: anomalies.append("silenzio_completo")
            if median and ratio>median*factor: anomalies.append("durata_anomala_rispetto_agli_altri_segmenti")
        result.append({"index":x["index"],"speaker":x["speaker"],"expected_voice":x["voice"],"original_text":x["text"],
            "emotion":x.get("emotion"),"wav_path":x["wav_path"].relative_to(root).as_posix(),
            "metadata_path":x["metadata_path"].relative_to(root).as_posix(),"duration_seconds":info["duration"] if info else None,
            "sample_rate":info["sample_rate"] if info else None,"channels":info["channels"] if info else None,
            "format":"PCM16" if info and info["sample_width"]==2 else None,"seed":meta.get("seed"),
            "sha256":info["wav_hash"] if info else None,"cache_status":x["cache_status"],"technical_anomalies":sorted(set(anomalies))})
    return result

def write_segment_lists(root,plan,config):
    items=segment_inventory(root,plan,config); folder=root/"work/review"; folder.mkdir(parents=True,exist_ok=True)
    json_path=folder/"episode_01_segments.json"; txt_path=folder/"episode_01_segments.txt"
    write_json(json_path,{"schema_version":1,"episode":"01","segments":items,"generated_at":now()})
    lines=["STORYCAST EPISODIO 01 — SEGMENTI AUDIO REALI",""]
    for x in items:
        lines += [f"{x['index']:02d}. {x['speaker']} — voce {x['expected_voice']}",f"    Testo: {x['original_text']}",
            f"    Emozione: {x['emotion'] or '—'}",f"    WAV: {x['wav_path']}",f"    Metadata: {x['metadata_path']}",
            f"    Durata: {x['duration_seconds']:.3f} s — {x['sample_rate']} Hz — {x['channels']} canale — {x['format']}",
            f"    Seed: {x['seed']} — SHA-256: {x['sha256']}",f"    Cache: {x['cache_status']} — Anomalie: {', '.join(x['technical_anomalies']) or 'nessuna'}",""]
    txt_path.write_text("\n".join(lines),encoding="utf-8")
    return {"text":txt_path.relative_to(root).as_posix(),"json":json_path.relative_to(root).as_posix(),"segments":items}
def segment_status(root,plan,config,index):
    x=next((x for x in plan if x["index"]==index),None)
    if not x: raise StorycastError(f"Indice battuta inesistente: {index}")
    check=next(y for y in qc(root,plan,config,update_states=False)["segments"] if y["index"]==index)
    meta=json.loads(x["metadata_path"].read_text(encoding="utf-8")) if x["metadata_path"].is_file() else None
    return {"plan":{k:x[k] for k in ("index","speaker","text","emotion","voice","language","seed","instruction","model","cache_status")},"wav":x["wav_path"].relative_to(root).as_posix(),"metadata":meta,"qc":check}
def backup_segment(root,x):
    folder=root/"work/episode_01/segment_backups"/f"{x['index']:04d}_{stamp()}"; folder.mkdir(parents=True)
    for p in (x["wav_path"],x["metadata_path"]):
        if p.is_file(): shutil.copy2(p,folder/p.name)
    return folder
def regenerate_segment(root,plan,config,index,dry_run=False,alternate_seed=False,prudent=False,backend="real"):
    x=next((x for x in plan if x["index"]==index),None)
    if not x: raise StorycastError(f"Indice battuta inesistente: {index}")
    before={i["index"]:file_hash(i["wav_path"]) for i in plan if i["wav_path"].is_file()}; selected=dict(x)
    if alternate_seed:
        selected["seed"]=x["seed"]+int(config["verification"].get("alternate_seed_offset",100003))
        from .tts import generation_hashes
        hashes=generation_hashes(voice=selected["voice"],language=selected["language"],instruction=selected["instruction"],
            parameters=selected["parameters"],effective_seed=selected["seed"],default_seed=x["seed"],alternate_seed=True,
            model_hash=selected["model_hash"],backend=backend,text_hash=selected["text_hash"])
        selected.update(hashes)
    if prudent:
        from .tts import split_text
        selected["chunks"]=split_text(selected["text"],int(config["text"].get("prudent_max_words",18)),int(config["text"].get("prudent_hard_limit_words",24)))
    if dry_run:
        backup=root/"work/episode_01/segment_backups"/f"{index:04d}_DATA_ORA"
        return {"index":index,"would_regenerate":True,"speaker":selected["speaker"],"text":selected["text"],
            "voice":selected["voice"],"seed":selected["seed"],"model":selected["model"],"backend":backend,
            "alternate_seed":alternate_seed,"prudent":prudent,"chunks":selected["chunks"],
            "wav_to_replace":selected["wav_path"].relative_to(root).as_posix(),
            "metadata_to_replace":selected["metadata_path"].relative_to(root).as_posix(),
            "backup_planned":backup.relative_to(root).as_posix(),
            "docker_command":f"docker compose run --rm storycast-tts episode-01-regenerate-segment {index} --yes",
            "files_modified":[],"model_loaded":False}
    backup=backup_segment(root,x); result=generate(root,[selected],config,backend,force_index=index)
    m=json.loads(selected["metadata_path"].read_text(encoding="utf-8")); m.update(character=selected["speaker"],requested_voice=selected["voice"],backend_voice=selected["voice"],effective_instruction=selected["instruction"],config_hash=selected["voice_config_hash"],generation_config_hash=selected["generation_config_hash"],effective_generation_hash=selected["effective_generation_hash"],seed_origin=selected["seed_origin"],alternate_seed=alternate_seed,prudent_mode=prudent,previous_backup=backup.relative_to(root).as_posix()); write_json(selected["metadata_path"],m)
    set_state(root,plan,index,"pending_review","Segmento rigenerato: richiede ascolto e approvazione esplicita.")
    unchanged=all(file_hash(i["wav_path"])==before[i["index"]] for i in plan if i["index"]!=index and i["index"] in before)
    if not unchanged: raise StorycastError("La rigenerazione selettiva ha modificato un WAV non richiesto")
    return {"index":index,"result":result,"backup":backup.relative_to(root).as_posix(),"wav_hash":file_hash(selected["wav_path"]),"other_segments_unchanged":unchanged,"review_state":"pending_review","alternate_seed":alternate_seed,"seed":selected["seed"]}
def generate_review_page(root,plan,config):
    enrich_metadata(root,plan); report=qc(root,plan,config); states=load_states(root,plan); rows=[]
    for x in plan:
        info=wav_info(x["wav_path"]); m=json.loads(x["metadata_path"].read_text(encoding="utf-8")); state=states["segments"][str(x["index"])]["review_state"]; esc=html.escape; wav="../"+x["wav_path"].relative_to(root).as_posix(); cmd=f"./avvia-storycast.sh episode-01-regenerate-segment {x['index']}"
        rows.append(f"""<article class="{esc(state)}"><h2>Battuta {x['index']:02d} — {esc(x['speaker'])}</h2><dl><dt>Voce prevista</dt><dd>{esc(x['voice'])}</dd><dt>Testo originale</dt><dd>{esc(x['text'])}</dd><dt>Emozione</dt><dd>{esc(str(x.get('emotion') or '—'))}</dd><dt>Durata</dt><dd>{info['duration']:.3f} s</dd><dt>Seed</dt><dd>{m['seed']}</dd><dt>Hash WAV</dt><dd><code>{info['wav_hash']}</code></dd><dt>Cache</dt><dd>{esc(x['cache_status'])}</dd><dt>Revisione</dt><dd>{esc(state)}</dd></dl><audio controls preload="metadata" src="{esc(wav)}">Audio non supportato.</audio><p>Percorso WAV: <code>{esc(x['wav_path'].relative_to(root).as_posix())}</code></p><p>Rigenera solo questa battuta: <code>{esc(cmd)}</code></p></article>""")
    page=f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Revisione audio — Storycast episodio 01</title><style>body{{font:16px system-ui,sans-serif;max-width:1050px;margin:auto;padding:1rem;background:#f5f5f2}}article{{background:white;border-left:6px solid #777;padding:1rem;margin:1rem 0}}.rejected{{border-color:#b42318}}.approved{{border-color:#16803b}}dl{{display:grid;grid-template-columns:10rem 1fr;gap:.35rem}}dt{{font-weight:700}}dd{{margin:0}}audio{{width:100%}}code{{overflow-wrap:anywhere}}.notice{{background:#fff4ce;padding:1rem}}</style></head><body><h1>Revisione audio — episodio 01</h1><p class="notice">Questa pagina non verifica semanticamente l'identità della voce: ascoltare ogni battuta e approvarla esplicitamente da CLI.</p>{''.join(rows)}</body></html>"""
    p=root/REVIEW_PAGE; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(page,encoding="utf-8")
    return {"page":REVIEW_PAGE.as_posix(),"segments":len(plan),"technical_failed":report["technical_failed"]}
def backup_outputs(root,index):
    from .episode import AUDIO,MANIFEST,TIMELINE,VIDEO
    folder=root/"work/episode_01/rebuild_backups"/f"segment_{index:04d}_{stamp()}"; folder.mkdir(parents=True)
    for rel in (AUDIO,MANIFEST,TIMELINE,VIDEO):
        source=root/rel
        if source.is_file():
            target=folder/rel; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target)
    return folder

# Schema QC/review v2. Le definizioni seguenti sostituiscono intenzionalmente
# le equivalenti dello schema sperimentale precedente mantenendo compatibilità
# di lettura con review_status.json versione 1.
REVIEW_STATES_V2={"pending_review","approved","rejected"}

def load_states(root,plan):
    saved={}; path=root/STATE_PATH
    if path.is_file():
        try: saved=json.loads(path.read_text(encoding="utf-8")).get("segments",{})
        except (OSError,json.JSONDecodeError): pass
    segments={}
    for item in plan:
        old=saved.get(str(item["index"]),{}); current_hash=file_hash(item["wav_path"]) if item["wav_path"].is_file() else None
        review=old.get("review_state")
        if review not in REVIEW_STATES_V2:
            legacy=old.get("state"); review=legacy if legacy in {"approved","rejected"} else "pending_review"
        reviewed_hash=old.get("reviewed_wav_hash") or (old.get("wav_hash") if review in {"approved","rejected"} else None)
        note=old.get("review_note",old.get("note")); review_updated=old.get("review_updated_at",old.get("updated_at"))
        if review=="pending_review" and old.get("qc_state")=="passed" and isinstance(note,str) and note.startswith("metadata_"):
            note=None; review_updated=None
        if review=="approved" and reviewed_hash!=current_hash:
            review="pending_review"; note="Approvazione invalidata: hash WAV cambiato."; review_updated=now(); reviewed_hash=None
        qc_state=old.get("qc_state","warning" if old.get("state")=="needs_review" else "warning")
        if qc_state not in {"passed","warning","failed"}: qc_state="warning"
        segments[str(item["index"])]={"review_state":review,"reviewed_wav_hash":reviewed_hash,
            "review_updated_at":review_updated,"review_note":note,"qc_state":qc_state,
            "qc_errors":old.get("qc_errors",[]),"qc_warnings":old.get("qc_warnings",[]),
            "qc_checked_at":old.get("qc_checked_at"),"wav_hash":current_hash}
    return {"schema_version":2,"episode":"01","segments":segments,"updated_at":now()}

def set_state(root,plan,index,state,note=None):
    if state not in REVIEW_STATES_V2: raise StorycastError(f"Stato revisione non valido: {state}")
    item=next((x for x in plan if x["index"]==index),None)
    if not item: raise StorycastError(f"Indice battuta inesistente: {index}")
    info=wav_info(item["wav_path"]); data=load_states(root,plan); entry=data["segments"][str(index)]
    entry.update(review_state=state,reviewed_wav_hash=info["wav_hash"] if state in {"approved","rejected"} else None,
                 review_updated_at=now(),review_note=note,wav_hash=info["wav_hash"])
    save_states(root,data); return entry

def _reconstruct_metadata(item,meta):
    from .tts import generation_hashes
    sources={
        "character":["dialogo analizzato: speaker","metadata esistenti: speaker"],
        "requested_voice":["config/voices.yaml","piano TTS canonico"],
        "backend_voice":["metadata esistenti: voice","config/voices.yaml"],
        "effective_instruction":["metadata esistenti: tts_instruction","piano TTS canonico"],
    }
    effective_seed=meta.get("seed")
    if not isinstance(effective_seed,int) or isinstance(effective_seed,bool):
        raise StorycastError(f"Seed effettivo mancante o non numerico nel segmento {item['index']}")
    alternate=meta.get("alternate_seed") is True
    hashes=generation_hashes(voice=meta.get("voice"),language=meta.get("language"),
        instruction=meta.get("tts_instruction"),parameters=meta.get("parameters"),effective_seed=effective_seed,
        default_seed=item["seed"],alternate_seed=alternate,model_hash=meta.get("model_hash"),
        backend=meta.get("backend"),text_hash=meta.get("text_hash"),
        instruction_profile=meta.get("instruction_profile"),
        instruction_language=meta.get("instruction_language"),spoken_language=meta.get("spoken_language"))
    values={"character":item["speaker"],"requested_voice":item["voice"],"backend_voice":meta.get("voice"),
        "effective_instruction":meta.get("tts_instruction"),"voice_config_hash":hashes["voice_config_hash"],
        "config_hash":hashes["voice_config_hash"],"generation_config_hash":hashes["generation_config_hash"],
        "effective_generation_hash":hashes["effective_generation_hash"],"seed_origin":hashes["seed_origin"]}
    hash_source=["metadata generazione: voice/language/instruction/parameters/model/backend/text_hash/seed",
        "config/voices.yaml: seed predefinito","algoritmo canonical_hash SHA-256"]
    for key in ("voice_config_hash","config_hash","generation_config_hash","effective_generation_hash","seed_origin"): sources[key]=hash_source
    return values,sources

def migrate_metadata(root,plan,dry_run=True):
    results=[]
    for item in plan:
        path=item["metadata_path"]
        if not path.is_file(): raise StorycastError(f"Metadata mancanti: {path}")
        meta=json.loads(path.read_text(encoding="utf-8")); previous_schema=meta.get("schema_version",1)
        info=wav_info(item["wav_path"])
        if meta.get("wav_hash")!=info["wav_hash"]: raise StorycastError(f"Hash WAV incoerente nei metadata del segmento {item['index']}")
        if meta.get("speaker")!=item["speaker"] or meta.get("original_text")!=item["text"]:
            raise StorycastError(f"Identità metadata non ricostruibile con sicurezza per il segmento {item['index']}")
        values,sources=_reconstruct_metadata(item,meta); changes={}
        for key,value in values.items():
            if meta.get(key)!=value: changes[key]={"value":value,"source":sources[key]}
        if previous_schema<3: changes["schema_version"]={"value":3,"source":["migrazione metadata Storycast v3"]}
        result={"index":item["index"],"file":path.relative_to(root).as_posix(),"previous_schema_version":previous_schema,
            "new_schema_version":3,"fields":changes,"wav_hash_before":info["wav_hash"],"wav_hash_after":info["wav_hash"],
            "would_modify":bool(changes),"dry_run":dry_run}
        if not dry_run and changes:
            old_hashes={k:meta.get(k) for k in ("voice_config_hash","config_hash","generation_config_hash","effective_generation_hash") if meta.get(k)}
            meta.update(values); meta["schema_version"]=3; meta["metadata_migrated"]=previous_schema<3
            meta["metadata_normalized"]=True; meta["metadata_migrated_at"]=now()
            meta["metadata_previous_schema_version"]=previous_schema
            meta["metadata_migration_source"]=sorted({source for entry in sources.values() for source in entry})
            meta["metadata_migration_fields"]={key:value for key,value in changes.items() if key!="schema_version"}
            if old_hashes: meta["legacy_hashes"]=old_hashes
            write_json(path,meta)
            if file_hash(item["wav_path"])!=info["wav_hash"]: raise StorycastError("Migrazione metadata ha modificato un WAV")
        if not dry_run:
            # Il piano in memoria deve descrivere i metadata effettivi appena
            # ricostruiti, inclusi backend e seed storici, senza toccare il WAV.
            item.update(voice_config_hash=values["voice_config_hash"],
                        generation_config_hash=values["generation_config_hash"],
                        effective_generation_hash=values["effective_generation_hash"])
        results.append(result)
    return {"dry_run":dry_run,"segments":results,"files_to_modify":[x["file"] for x in results if x["would_modify"]],
        "wav_hashes_unchanged":all(x["wav_hash_before"]==x["wav_hash_after"] for x in results)}

def _metadata_qc(item,meta):
    errors=[]; warnings=[]
    if meta is None: return ["metadata_mancanti_o_illeggibili"],warnings
    for key,expected in (("index",item["index"]),("speaker",item["speaker"]),("original_text",item["text"]),
                         ("requested_voice",item["voice"]),("backend_voice",item["voice"]),("character",item["speaker"]),
                         ("effective_instruction",item["instruction"]),("backend","real")):
        if key not in meta: warnings.append(f"metadata_{key}_mancante_migrabile")
        elif meta.get(key)!=expected: errors.append(f"metadata_{key}_incoerente")
    seed=meta.get("seed")
    if not isinstance(seed,int) or isinstance(seed,bool): errors.append("metadata_seed_effettivo_mancante_o_non_numerico")
    else:
        try:
            values,_=_reconstruct_metadata(item,meta)
            for key in ("voice_config_hash","generation_config_hash","effective_generation_hash","seed_origin"):
                if key not in meta: warnings.append(f"metadata_{key}_mancante_migrabile")
                elif meta.get(key)!=values[key]: errors.append(f"metadata_{key}_incoerente")
            if meta.get("alternate_seed") is True and seed==item["seed"]: warnings.append("alternate_seed_uguale_al_default")
            if meta.get("alternate_seed") is not True and seed!=item["seed"]: errors.append("seed_non_default_senza_provenienza_alternativa")
        except StorycastError as exc: errors.append(str(exc))
    return errors,warnings

def qc(root,plan,config,strict=False,update_states=True):
    states=load_states(root,plan); rows=[]; ratios=[]
    for item in plan:
        try:
            info=wav_info(item["wav_path"]); ratios.append(info["duration"]/max(1,len(normalize_spoken_text(item["text"]).split())))
        except StorycastError: pass
    median=statistics.median(ratios) if ratios else 0; limits=config["verification"]
    for item in plan:
        audio_errors=[]; audio_warnings=[]; info=None
        try:
            info=wav_info(item["wav_path"]); words=max(1,len(normalize_spoken_text(item["text"]).split())); ratio=info["duration"]/words
            if (info["sample_rate"],info["channels"],info["sample_width"])!=(24000,1,2): audio_errors.append("formato_non_mono_pcm16_24khz")
            if info["complete_silence"] or info["rms"]==0: audio_errors.append("silenzio_completo")
            if info["duration"]<float(limits["min_duration_seconds"]): audio_errors.append("durata_troppo_breve")
            if info["duration"]>float(limits.get("max_duration_seconds",45)): audio_errors.append("segmento_eccessivamente_lungo")
            if not float(limits["min_seconds_per_word"])<=ratio<=float(limits["max_seconds_per_word"]): audio_warnings.append("durata_per_parola_anomala")
            if median and ratio>median*float(limits.get("duration_outlier_factor",1.8)): audio_warnings.append("durata_anomala_rispetto_agli_altri_segmenti")
            if info["silence_percent"]>float(limits["max_silence_percent"]): audio_errors.append("silenzio_eccessivo")
        except StorycastError as exc: audio_errors.append(str(exc))
        try: meta=json.loads(item["metadata_path"].read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError): meta=None
        metadata_errors,metadata_warnings=_metadata_qc(item,meta)
        if info and meta and meta.get("wav_hash")!=info["wav_hash"]: metadata_errors.append("metadata_wav_hash_incoerente")
        errors=sorted(set(audio_errors+metadata_errors)); warnings=sorted(set(audio_warnings+metadata_warnings))
        state="failed" if errors else ("warning" if warnings else "passed"); entry=states["segments"][str(item["index"])]
        if update_states: entry.update(qc_state=state,qc_errors=errors,qc_warnings=warnings,qc_checked_at=now(),wav_hash=info["wav_hash"] if info else None)
        rows.append({"index":item["index"],"speaker":item["speaker"],"qc_state":state,"review_state":entry["review_state"],
            "audio_errors":audio_errors,"audio_warnings":audio_warnings,"metadata_errors":metadata_errors,
            "metadata_warnings":metadata_warnings,"audio":info})
    if update_states: save_states(root,states)
    technical_failed=[x["index"] for x in rows if x["qc_state"]=="failed"]
    not_approved=[x["index"] for x in rows if x["review_state"]!="approved"]
    blocked=bool(technical_failed or (strict and not_approved))
    return {"status":"blocked" if blocked else "passed","strict":strict,"segments":rows,
        "technical_failed":technical_failed,"not_approved":not_approved,"states":states}
