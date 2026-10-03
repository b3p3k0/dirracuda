#!/usr/bin/env python3
"""Capture and measure custom dialogs on isolated Xvfb/KWin desktops.

Example: ./venv/bin/python scripts/run_dialog_audit.py --scenario scan-results-completed
Artifacts are diagnostic evidence, not pixel-perfect golden screenshots.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from gui.tests.dialog_audit.scenarios import CASES
from gui.tests.dialog_audit.inventory import construction_sites, EXCLUSIONS

PROFILES={"desktop":(1920,1080,96),"laptop":(1366,768,96),"large-text":(1920,1080,144)}


def report(output, selected, complete, profiles=None, themes=None):
    profiles = list(PROFILES) if profiles is None else profiles
    themes = ["light", "dark"] if themes is None else themes
    names = [c.name for c in CASES if selected == "all" or c.name in selected.split(",")]
    paths = [output / (profile + "-" + theme) / name / "result.json"
             for profile in profiles for theme in themes for name in names]
    missing = [str(path.relative_to(output)) for path in paths if not path.exists()]
    results = [json.loads(path.read_text()) for path in paths if path.exists()]
    covered={c.site for c in CASES}
    inventory=[{**s,"status":"scenario" if s["id"] in covered else "excluded" if s["id"] in EXCLUSIONS else "uncovered", "reason":EXCLUSIONS.get(s["id"])} for s in construction_sites()]
    uncovered=[s for s in inventory if s["status"]=="uncovered"]
    summary={"inventory":inventory,"native_exclusions":["OS/Tk file pickers", "standard Tk messageboxes"],
             "results":results,"missing_results":missing,"complete_inventory":not uncovered}
    (output/"report.json").write_text(json.dumps(summary,indent=2))
    parts=['<!doctype html><meta charset="utf-8"><title>Dirracuda dialog audit</title>',
           '<style>body{font:16px sans-serif;max-width:1200px;margin:2em auto}img{max-width:100%}pre{white-space:pre-wrap}.fail{color:#b22}.pass{color:#173}</style>',
           '<h1>Dirracuda dialog audit</h1>',f'<p>{len(results)} scenario/profile results. {len(uncovered)} uncovered construction sites.</p>',
           '<p>Measurements flag clipping; screenshots require visual review. Native file pickers and standard messageboxes are excluded.</p>']
    if uncovered: parts+=['<h2>Uncovered</h2>','<pre>'+html.escape('\n'.join(s['id'] for s in uncovered))+'</pre>']
    parts.append('<details><summary>Construction-site inventory and exclusions</summary><table><tr><th>Site</th><th>Status</th><th>Reason</th></tr>')
    for site in inventory:
        parts.append('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in
                     (site['id'], site['status'], site['reason'] or '')) + '</tr>')
    parts.append('</table></details>')
    for path in paths:
        if not path.exists(): continue
        r=json.loads(path.read_text()); label=str(path.parent.relative_to(output))
        parts.append(f'<details><summary class="{"pass" if r["passed"] else "fail"}">{html.escape(label)} — {"PASS" if r["passed"] else "FAIL"}</summary>')
        parts.append('<pre>'+html.escape('\n'.join(r['errors']))+'</pre>')
        parts.append('<pre>'+html.escape(json.dumps(r.get('reachability_findings', []), indent=2))+'</pre>')
        for capture in r['captures']:
            parts.append('<h3>'+html.escape(capture['state']+' '+capture['geometry'])+'</h3>')
            parts.append('<pre>'+html.escape(json.dumps(capture['findings'],indent=2))+'</pre>')
            image=path.parent.relative_to(output)/capture['screenshot']
            parts.append(f'<a href="{image}"><img loading="lazy" src="{image}"></a>')
        parts.append('</details>')
    if missing: parts.append("<h2>Missing results</h2><pre>" + html.escape("\n".join(missing)) + "</pre>")
    (output/"index.html").write_text("\n".join(parts))
    failed=sum(not r['passed'] for r in results)
    print(f"{len(results)} results; {failed} failed; {len(uncovered)} uncovered. Report: {output/'index.html'}",flush=True)
    return int(bool(failed or uncovered or missing or not results))


def session(args):
    output=Path(args.output)
    env={key: os.environ[key] for key in ("DISPLAY", "XAUTHORITY", "PATH", "HOME", "USER", "LANG", "XDG_RUNTIME_DIR") if key in os.environ}
    env.update(XDG_CURRENT_DESKTOP="Generic", XDG_CONFIG_HOME=str(output/"wm-config"), XDG_DATA_DIRS="/usr/local/share:/usr/share")
    env.update(QT_QPA_PLATFORMTHEME="",KWIN_COMPOSE="N", QT_NO_XDG_DESKTOP_PORTAL="1", GTK_USE_PORTAL="0")
    with (output/"kwin.log").open("w") as log:
        wm=subprocess.Popen(["dbus-run-session","--","kwin_x11","--replace"],env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                check=subprocess.run(["xprop","-root","_NET_SUPPORTING_WM_CHECK"],capture_output=True,text=True)
                if "window id # 0x" in check.stdout: break
                time.sleep(.1)
            else: raise RuntimeError("KWin did not initialize")
            for case in CASES:
                if args.scenario!="all" and case.name not in args.scenario.split(","): continue
                dest=output/case.name; dest.mkdir(parents=True,exist_ok=True)
                (dest / "result.json").unlink(missing_ok=True)
                command=[sys.executable,__file__,"--worker",case.name,"--profile",args.profile,"--theme",args.theme,"--output",str(dest)]
                try:
                    with (dest/"worker.log").open("w") as worker_log:
                        proc=subprocess.run(command,stdout=worker_log,stderr=subprocess.STDOUT,timeout=60)
                    if proc.returncode or not (dest/"result.json").exists():
                        raise RuntimeError(f"Worker exited {proc.returncode}; see worker.log")
                except Exception as exc:
                    (dest/"result.json").write_text(json.dumps({"scenario":case.name,"site":case.site,"passed":False,"captures":[],"errors":[str(exc)]}))
                result=json.loads((dest/"result.json").read_text())
                print(f'{output.name}/{case.name}: {"PASS" if result["passed"] else "FAIL"}',flush=True)
        finally:
            import signal
            try: os.killpg(wm.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            wm.wait(timeout=10)
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario",default="all",help="all or comma-separated scenario names")
    parser.add_argument("--profile",choices=["all",*PROFILES],default="all")
    parser.add_argument("--theme",choices=["both","light","dark"],default="both")
    parser.add_argument("--output",default="/tmp/dirracuda-dialog-audit/latest")
    parser.add_argument("--list",action="store_true")
    parser.add_argument("--session",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--worker",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.list:
        for case in CASES: print(case.name)
        return 0
    if args.worker:
        from gui.tests.dialog_audit.worker import run
        case=next(c for c in CASES if c.name==args.worker)
        run(case,Path(args.output),PROFILES[args.profile][2]/72,args.theme=="dark")
        return 0
    if args.session: return session(args)
    unknown=set(args.scenario.split(","))-{c.name for c in CASES}-{"all"}
    if unknown: parser.error("Unknown scenarios: "+", ".join(sorted(unknown)))
    for tool in ("xvfb-run","Xvfb","kwin_x11","dbus-run-session","xprop"):
        if not shutil.which(tool): parser.error(f"Missing prerequisite: {tool}")
    from PIL import features
    if not features.check_feature("xcb"): parser.error("Pillow requires XCB screenshot support")
    output=Path(args.output).resolve(); output.mkdir(parents=True,exist_ok=True)
    jobs=[]
    for profile in PROFILES if args.profile=="all" else [args.profile]:
        for theme in ("light","dark") if args.theme=="both" else [args.theme]:
            dest=output/(profile+"-"+theme); dest.mkdir(exist_ok=True)
            width,height,dpi=PROFILES[profile]
            jobs.append(["xvfb-run","-a","-s",f"-screen 0 {width}x{height}x24 -dpi {dpi} -nolisten tcp",sys.executable,__file__,"--session","--profile",profile,"--theme",theme,"--scenario",args.scenario,"--output",str(dest)])
    with ThreadPoolExecutor(max_workers=3) as pool:
        codes=list(pool.map(lambda cmd: subprocess.run(cmd).returncode,jobs))
    return max(report(output,args.scenario,args.scenario=="all", list(PROFILES) if args.profile=="all" else [args.profile], ["light","dark"] if args.theme=="both" else [args.theme]),int(any(codes)))


if __name__=="__main__": raise SystemExit(main())
