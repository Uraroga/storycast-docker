from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from .core import StorycastError
from .visual import ASSET_MANIFEST, FINAL_VIDEO, SHOT_PLAN, build_assets, load_visual, plan_shots, render_video, verify_video
from .visual_library import (FINAL_VIDEO as LIBRARY_VIDEO, MANIFEST as LIBRARY_MANIFEST,
                             SHOT_PLAN as LIBRARY_PLAN, build_library, clean_candidates,
                             inspect_library, plan_library, render_library,
                             verify_library_video)
from . import episode

ROOT=Path(os.environ.get("STORYCAST_ROOT",Path(__file__).resolve().parents[1])).resolve()
COMMANDS=("visual-status","visual-check","visual-plan","visual-assets","visual-verify","render-test","render-status","render-check","visual-clean","episode-01-visual","episode-01-render","episode-01-check",
          "visual-library-status","visual-library-check","visual-library-plan","visual-library-build","visual-library-clean","episode-01-render-library")
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("command",choices=COMMANDS); p.add_argument("--dry-run",action="store_true"); p.add_argument("--yes",action="store_true"); a=p.parse_args(argv)
 try:
  if a.command=="episode-01-visual": episode.visual(ROOT,a.dry_run)
  elif a.command=="episode-01-render": print(json.dumps(episode.render(ROOT,a.dry_run),ensure_ascii=False,indent=2))
  elif a.command=="episode-01-check": print(json.dumps(episode.check(ROOT),ensure_ascii=False,indent=2))
  elif a.command=="visual-library-status":
   status=inspect_library(ROOT); status.update({"manifest":"presente" if (ROOT/LIBRARY_MANIFEST).is_file() else "assente","plan":"presente" if (ROOT/LIBRARY_PLAN).is_file() else "assente","video":"presente" if (ROOT/LIBRARY_VIDEO).is_file() else "assente"}); print(json.dumps(status,ensure_ascii=False,indent=2))
  elif a.command=="visual-library-check": print(json.dumps(inspect_library(ROOT),ensure_ascii=False,indent=2))
  elif a.command=="visual-library-build": print(json.dumps(build_library(ROOT,a.dry_run),ensure_ascii=False,indent=2))
  elif a.command=="visual-library-plan": print(json.dumps(plan_library(ROOT,a.dry_run),ensure_ascii=False,indent=2))
  elif a.command=="episode-01-render-library":
   result=render_library(ROOT,a.dry_run)
   if not a.dry_run: result["verification"]=verify_library_video(ROOT)
   print(json.dumps(result,ensure_ascii=False,indent=2))
  elif a.command=="visual-library-clean":
   files=clean_candidates(ROOT)
   for path in files: print(path.relative_to(ROOT))
   if a.dry_run: print(f"Dry-run: {len(files)} file; nessuna eliminazione.")
   elif not a.yes: print("Operazione annullata: usare --yes.",file=sys.stderr); return 2
   else:
    for path in files: path.unlink()
    print(f"Eliminati {len(files)} file della libreria visiva.")
  elif a.command=="visual-status": print(f"config=ok manifest={'presente' if (ROOT/ASSET_MANIFEST).is_file() else 'assente'} plan={'presente' if (ROOT/SHOT_PLAN).is_file() else 'assente'}"); load_visual(ROOT)
  elif a.command=="visual-check":
   v,r=load_visual(ROOT); print(f"Check visivo superato: {len(v['characters'])} personaggi, {r['video']['width']}x{r['video']['height']}.")
  elif a.command=="visual-plan": plan_shots(ROOT,a.dry_run)
  elif a.command=="visual-assets": build_assets(ROOT,a.dry_run)
  elif a.command=="visual-verify":
   m=build_assets(ROOT,False); assert all(x["validation_status"]=="valid" for x in m["assets"]); print(f"Catalogo verificato: {len(m['assets'])} asset validi.")
  elif a.command=="render-test": print(json.dumps(render_video(ROOT,a.dry_run),ensure_ascii=False,indent=2))
  elif a.command=="render-status": print(f"video={'presente' if (ROOT/FINAL_VIDEO).is_file() else 'assente'}")
  elif a.command=="render-check": print(json.dumps(verify_video(ROOT),ensure_ascii=False,indent=2))
  elif a.command=="visual-clean":
   files=[p for p in (ROOT/"work/visual").rglob("*") if p.is_file()]
   for p in files: print(p.relative_to(ROOT))
   if a.dry_run: print(f"Dry-run: {len(files)} file; nessuna eliminazione.")
   elif not a.yes: print("Operazione annullata: usare --yes.",file=sys.stderr); return 2
   else:
    for p in files:p.unlink()
    print(f"Eliminati {len(files)} file sotto work/visual.")
  return 0
 except (StorycastError,OSError,ValueError,KeyError,AssertionError) as e: print(f"ERRORE: {e}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
