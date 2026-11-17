"""
quality_admin.py: /admin/quality, Althais staff only. Two jobs:

  1. CMS edit tables: upload CMS's quarterly Practitioner PTP (NCCI) and MUE files; claim checks use them from then on.
  2. The coding test: run the labeled cases (evals/coding_cases.json) through the claim builder and keep the scorecard,
     so Groq vs Gemini, or a prompt change, is a number.
"""
from __future__ import annotations

import time
from typing import List

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from auth import current_admin, get_db, require_admin
import cms_edits
import coding_eval

router = APIRouter()
MAX_UPLOAD = 120 * 1024 * 1024


@router.get("/admin/api/cms")
def cms_status(_=Depends(require_admin), db: Session = Depends(get_db)):
    return cms_edits.status(db)


@router.post("/admin/api/cms/{kind}")
async def cms_upload(kind: str, files: List[UploadFile] = File(...), _=Depends(require_admin)):
    if kind not in ("ptp", "mue"):
        return JSONResponse({"error": "Unknown table."}, status_code=404)
    uploads, total = [], 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > MAX_UPLOAD:
            return JSONResponse({"error": "Those files are larger than expected for CMS's tables."}, status_code=413)
        uploads.append((f.filename or "upload", data))
    if not uploads:
        return JSONResponse({"error": "Choose the file(s) first."}, status_code=400)
    if not cms_edits.load_in_background(kind, uploads):
        return JSONResponse({"error": "That table is already loading."}, status_code=409)
    return {"ok": True}


def _build(case: dict) -> dict:
    import main
    return main._claim_auto_work(case["note"], "", case["header"], case["visit"], [], time.time(), None)


@router.post("/admin/api/coding-eval")
async def eval_start(request: Request, _=Depends(require_admin)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not coding_eval.start(_build, str((body or {}).get("note") or "")):
        return JSONResponse({"error": "A test is already running."}, status_code=409)
    return {"ok": True}


@router.get("/admin/api/coding-eval")
def eval_runs(_=Depends(require_admin), db: Session = Depends(get_db)):
    return {"job": coding_eval.job(), "runs": coding_eval.runs(db), "ai": coding_eval.ai_label(),
            "cases": len(coding_eval.load_cases()["cases"])}


@router.get("/admin/quality", response_class=HTMLResponse)
def quality_page(request: Request):
    if not current_admin(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coding quality · Althais Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--brand:#1d5bb8;--ink:#1b2430;--mut:#5b6573;--line:#dde3ea;--bg:#f5f7fa;--card:#fff;--good:#1f7a4d;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 'IBM Plex Sans',system-ui,sans-serif;color:var(--ink);background:var(--bg)}
header{background:#14365f;color:#fff;padding:12px 24px;display:flex;gap:24px;align-items:center}header a{color:#fff;opacity:.8;text-decoration:none}header a.on{opacity:1;font-weight:600}
main{max-width:1100px;margin:24px auto;padding:0 20px}h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:3px;padding:18px 20px;margin-bottom:18px}
.mut{color:var(--mut)}.row{display:flex;gap:18px;flex-wrap:wrap}.row>div{flex:1;min-width:280px}
button{background:var(--brand);color:#fff;border:0;border-radius:3px;padding:8px 14px;font:inherit;font-weight:500;cursor:pointer}button:disabled{opacity:.5}
input[type=file]{margin:8px 0}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--mut)}.ok{color:var(--good)}.no{color:var(--bad)}.pill{display:inline-block;padding:1px 7px;border-radius:9px;background:#eef3fb;font-size:12px}
details summary{cursor:pointer;color:var(--brand)}code{font-size:12px}
</style></head><body>
<header><strong>Althais Admin</strong><a href="/admin">Admin Console</a><a href="/admin/lost-money">Lost Money Report</a><a class="on" href="/admin/quality">Coding Quality</a></header>
<main>
<h1>Coding quality</h1><p class="mut">CMS's official edits, checked in code, and a fixed test that scores the AI coder.</p>

<div class="card"><h2>CMS edit tables</h2>
<p class="mut">Each quarter: cms.gov &rsaquo; National Correct Coding Initiative Edits. Accept CMS's license, download, then upload here. Until a table is loaded, claim checks use a short built-in list.</p>
<div class="row">
 <div><strong>NCCI procedure-to-procedure (Practitioner)</strong><div class="mut">The quarter's 4 zip files, all at once.</div>
  <input type="file" id="ptp" multiple accept=".zip,.txt,.csv,.xlsx"><br><button onclick="up('ptp')">Upload PTP</button><div id="ptp-s" class="mut"></div></div>
 <div><strong>MUEs (Practitioner Services)</strong><div class="mut">The quarter's zip file.</div>
  <input type="file" id="mue" multiple accept=".zip,.txt,.csv,.xlsx"><br><button onclick="up('mue')">Upload MUE</button><div id="mue-s" class="mut"></div></div>
</div></div>

<div class="card"><h2>Coding test</h2>
<p class="mut" id="ai"></p>
<p class="mut" id="labels"></p>
<button id="go" onclick="run()">Run the coding test</button> <span id="job" class="mut"></span>
<div id="runs" style="margin-top:14px"></div></div>
</main>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
async function j(u,o){var r=await fetch(u,Object.assign({credentials:'same-origin'},o||{}));if(r.status===401){location.href='/admin/login';throw 0}return r.json()}
function cmsLine(t){if(!t)return 'Not loaded';var s='';if(t.rows)s+=Number(t.rows).toLocaleString()+' rows, loaded '+esc((t.loadedAt||'').replace('T',' ').replace('Z',' UTC'));
 if(t.job){if(t.job.state==='loading')s='Loading… '+Number(t.job.rows||0).toLocaleString()+' rows so far';else if(t.job.state==='error')s+=' <span class="no">'+esc(t.job.error)+'</span>'}return s||'Not loaded'}
async function cms(){var s=await j('/admin/api/cms');document.getElementById('ptp-s').innerHTML=cmsLine(s.ptp);document.getElementById('mue-s').innerHTML=cmsLine(s.mue);
 if((s.ptp&&s.ptp.job&&s.ptp.job.state==='loading')||(s.mue&&s.mue.job&&s.mue.job.state==='loading'))setTimeout(cms,3000)}
async function up(k){var f=document.getElementById(k).files;if(!f.length){alert('Choose the file(s) first.');return}var fd=new FormData();for(var i=0;i<f.length;i++)fd.append('files',f[i]);
 document.getElementById(k+'-s').textContent='Uploading…';var r=await j('/admin/api/cms/'+k,{method:'POST',body:fd});if(r.error)document.getElementById(k+'-s').innerHTML='<span class="no">'+esc(r.error)+'</span>';else setTimeout(cms,1500)}
function pct(v){return v==null?'':v+'%'}
function runRow(r){var s=r.summary||{};return '<tr><td>'+esc((r.at||'').replace('T',' ').replace('Z',''))+'<div class="mut">'+esc(r.ai)+'</div>'+(r.note?'<div class="mut">'+esc(r.note)+'</div>':'')+'</td><td><strong>'+pct(s.fully_correct)+'</strong></td><td>'+pct(s.cpt_exact)+'</td><td>'+pct(s.em_right)+' <span class="mut">('+pct(s.em_best)+' exact)</span></td><td>'+pct(s.dx_recall)+'</td><td>'+pct(s.modifiers)+'</td><td>'+esc(s.avg_check_score)+(s.ai_answered!=null&&s.ai_answered<100?'<div class="mut">AI answered '+s.ai_answered+'%</div>':'')+'</td><td>'+esc(s.avg_seconds)+'s</td><td>'+esc(s.errors)+'</td></tr>'
 +'<tr><td colspan="9"><details><summary>Each case</summary><table><tr><th>Case</th><th>Right?</th><th>CPT given</th><th>ICD-10 given</th><th>Misses</th></tr>'+(r.results||[]).map(function(c){if(c.error)return '<tr><td>'+esc(c.title)+'</td><td class="no">error</td><td colspan="3">'+esc(c.error)+'</td></tr>';var x=c.score,m=[];
  if(x.cpt_hit<x.cpt_slots)m.push((x.cpt_slots-x.cpt_hit)+' CPT missing');if(x.cpt_extra.length)m.push('extra '+x.cpt_extra.join(', '));if(x.em==='wrong')m.push('E/M level');if(x.dx_hit<x.dx_slots)m.push((x.dx_slots-x.dx_hit)+' ICD-10 missing');if(!x.mods_ok)m.push('modifiers'+(x.stray_mods.length?' ('+x.stray_mods.join(', ')+' not needed)':''));if(c.source&&c.source!=='ai')m.push(c.source==='ai_fast'?'answered by the small model':'AI timed out: codes guessed from the note');
  return '<tr><td>'+esc(c.title)+'</td><td class="'+(x.correct?'ok':'no')+'">'+(x.correct?'Yes':'No')+'</td><td><code>'+esc(x.got.cpt.join(' '))+'</code></td><td><code>'+esc(x.got.icd10.join(' '))+'</code></td><td>'+esc(m.join('; '))+'</td></tr>'}).join('')+'</table></details></td></tr>'}
async function evals(){var d=await j('/admin/api/coding-eval');document.getElementById('ai').textContent='This deployment codes with: '+d.ai+' · '+d.cases+' cases';
 var jb=d.job||{};document.getElementById('go').disabled=jb.state==='running';document.getElementById('job').textContent=jb.state==='running'?('Running… '+jb.done+' of '+jb.total):(jb.state==='error'?('Last run failed: '+jb.error):'');
 if(d.runs.length)document.getElementById('labels').textContent=d.runs[0].labels||'';
 document.getElementById('runs').innerHTML=d.runs.length?'<table><tr><th>Run</th><th>Fully right</th><th>CPT exact</th><th>E/M level</th><th>ICD-10 found</th><th>Modifiers</th><th>Check score</th><th>Time/case</th><th>Errors</th></tr>'+d.runs.map(runRow).join('')+'</table>':'<p class="mut">No runs yet.</p>';
 if(jb.state==='running')setTimeout(evals,4000)}
async function run(){var note=prompt('Optional note for this run (e.g. "Gemini, thinking LOW"):','')||'';var r=await j('/admin/api/coding-eval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:note})});if(r.error)alert(r.error);evals()}
cms();evals();
</script></body></html>"""
