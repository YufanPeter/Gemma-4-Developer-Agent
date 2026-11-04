#!/usr/bin/env python3
"""
trace_viewer.py -- one-file HTML viewer for a Kaggle dev-eval run's ATIF traces.

For each task it shows the context window over time (prompt tokens of every LLM
call, coloured by sub-agent, with the 14,336-token compaction threshold and the
32,768 max_model_len drawn in, and detected compactions marked), the time each
step took, and every step in full: thinking, tool call arguments, tool output.
The patch and the tail of the phase-2 test log sit in their own tabs.

The page is self-contained (data embedded, no server, no CDN), so it can be
opened straight from disk or sent to someone. Stdlib only.

Usage:
    python eval/trace_viewer.py --results-dir exp_res/results_dev60_e5_pipeline
    python eval/trace_viewer.py --results-dir exp_res/results_X --out /tmp/x.html --no-open
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trace_platform import model  # noqa: E402

COMPACTION_THRESHOLD = 14_336  # docs/HARNESS_README.md 7.2 (ADK EventsCompaction token_threshold)
MAX_MODEL_LEN = 32_768         # vLLM max_model_len in the dev-eval notebook
MAX_TEXT = 20_000              # per-field cap so a run stays a few MB
MAX_LOG = 12_000               # tail of the test log kept


# Text helpers shared with the trace platform (trace_platform/model.py).
_clip = model.clip
_parse_observation = model.parse_observation
_fmt_args = model.fmt_args
_split_thought = model.split_thought


def load_task(trace_path: Path) -> dict:
    t = json.loads(trace_path.read_text())
    steps = []
    prev_t = 0.0
    last_prompt_by_author: dict[str, int] = {}
    for s in t.get("steps", []):
        ex = s.get("extra") or {}
        calls = s.get("tool_calls") or []
        author = ex.get("author")
        if author is None and calls:
            author = (calls[0].get("extra") or {}).get("author")
        m = s.get("metrics") or {}
        if not m and calls:
            m = (calls[0].get("extra") or {}).get("usage") or {}
        elapsed = float(ex.get("elapsed_s") or 0.0)
        thought, msg = _split_thought(s.get("message"))
        tool_name, obs = _parse_observation(s.get("observation"))
        pt = m.get("prompt_tokens")
        compaction = False
        if pt and author:
            prev = last_prompt_by_author.get(author)
            if prev and prev >= 8000 and pt < prev * 0.75:
                compaction = True
            last_prompt_by_author[author] = pt
        steps.append({
            "id": s.get("step_id"),
            "src": s.get("source"),
            "ev": ex.get("event_type") or ("tool" if calls else None),
            "author": author or s.get("source"),
            "t": round(elapsed, 2),
            "dt": round(max(0.0, elapsed - prev_t), 2),
            "pt": pt,
            "ct": m.get("completion_tokens"),
            "llm": bool(s.get("llm_call_count")),
            "compaction": compaction,
            "thought": _clip(thought),
            "msg": _clip(msg),
            "calls": [{"fn": c.get("function_name"), "args": _clip(_fmt_args(c.get("arguments")))}
                      for c in calls],
            "obs": _clip(obs),
            "obs_tool": tool_name,
        })
        prev_t = elapsed
    return {"steps": steps, "final_metrics": t.get("final_metrics") or {}}


def build(results_dir: Path) -> dict:
    rows = {}
    dr = results_dir / "dev_results.jsonl"
    if dr.exists():
        for line in dr.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r
    meta = {}
    if (results_dir / "run_meta.json").exists():
        rm = json.loads((results_dir / "run_meta.json").read_text())
        meta = {k: rm.get(k) for k in ("run_name", "submission_fingerprint", "model", "eval_config")}
    tasks = []
    for tp in sorted((results_dir / "traces").glob("trace_*.json")):
        tid = tp.stem[len("trace_"):]
        task = load_task(tp)
        r = rows.get(tid, {})
        patch = results_dir / "patches" / f"{tid}.patch"
        log = results_dir / "test_outputs" / f"{tid}.log"
        st = task["steps"]
        pts = [x["pt"] for x in st if x["pt"]]
        task.update({
            "id": tid,
            "repo": r.get("repo"),
            "resolved": r.get("resolved"),
            "error": r.get("error") or "",
            "tool_calls": r.get("tool_calls", sum(len(x["calls"]) for x in st)),
            "duration": r.get("duration_seconds") or (st[-1]["t"] if st else 0),
            "peak_ctx": max(pts) if pts else 0,
            "compactions": sum(x["compaction"] for x in st),
            "patch": _clip(patch.read_text(errors="replace")) if patch.exists() else "",
            "test_log": (lambda s: s if len(s) <= MAX_LOG else
                         f"... [{len(s) - MAX_LOG:,} chars clipped]\n" + s[-MAX_LOG:])(
                log.read_text(errors="replace")) if log.exists() else "",
        })
        tasks.append(task)
    return {"meta": meta, "results_dir": results_dir.name, "tasks": tasks,
            "limits": {"compaction": COMPACTION_THRESHOLD, "max_model_len": MAX_MODEL_LEN}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, help="default: <results-dir>/trace_viewer.html")
    ap.add_argument("--no-open", action="store_true", help="do not open the page in a browser")
    a = ap.parse_args()
    data = build(a.results_dir)
    if not data["tasks"]:
        raise SystemExit(f"no traces/trace_*.json under {a.results_dir}")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__TITLE__", data["meta"].get("run_name") or a.results_dir.name) \
                   .replace("__DATA__", payload)
    out = a.out or a.results_dir / "trace_viewer.html"
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(data['tasks'])} tasks)")
    if not a.no_open:
        webbrowser.open(out.resolve().as_uri())


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Traces · __TITLE__</title>
<style>
:root{--bg:#fafaf9;--panel:#fff;--fg:#1c1917;--muted:#78716c;--line:#e7e5e4;--hover:#f5f5f4;--sel:#eef2ff;
--ok:#15803d;--bad:#b91c1c;--warn:#b45309;--code:#f5f5f4;--accent:#4f46e5;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0c0a09;--panel:#1c1917;--fg:#e7e5e4;--muted:#a8a29e;
--line:#292524;--hover:#232120;--sel:#1e1b4b;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--code:#141210;--accent:#818cf8;}}
:root[data-theme=dark]{--bg:#0c0a09;--panel:#1c1917;--fg:#e7e5e4;--muted:#a8a29e;--line:#292524;--hover:#232120;--sel:#1e1b4b;
--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--code:#141210;--accent:#818cf8;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif;}
.app{display:grid;grid-template-columns:330px 1fr;height:100vh}
aside{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;background:var(--panel)}
aside header{padding:12px;border-bottom:1px solid var(--line)}
aside h1{font-size:14px;margin:0 0 2px}
.sub{color:var(--muted);font-size:12px}
.controls{display:flex;gap:6px;margin-top:8px}
input,select{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 6px;min-width:0}
input{flex:1}
.list{overflow:auto;flex:1}
.row{display:grid;grid-template-columns:14px 1fr auto;gap:6px;align-items:center;padding:6px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{background:var(--hover)} .row.sel{background:var(--sel)}
.dot{width:9px;height:9px;border-radius:50%}
.row .nm{font-family:ui-monospace,Menlo,monospace;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .st{color:var(--muted);font-size:11px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
main{overflow:auto;min-width:0}
.top{padding:14px 18px;border-bottom:1px solid var(--line);background:var(--panel)}
.top h2{margin:0;font:600 16px ui-monospace,Menlo,monospace}
.kpis{display:flex;flex-wrap:wrap;gap:18px;margin-top:8px}
.kpi b{display:block;font-size:15px;font-variant-numeric:tabular-nums} .kpi span{color:var(--muted);font-size:11px}
.err{margin-top:8px;color:var(--bad);font-size:12px}
.card{margin:14px 18px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
.card h3{margin:0 0 6px;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted);margin-bottom:4px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
svg{display:block;width:100%;overflow:visible}
svg text{fill:var(--muted);font-size:10px;font-family:ui-sans-serif,system-ui,sans-serif}
.grid line{stroke:var(--line)}
.pt{cursor:pointer}
#tip{position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;
font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none;max-width:360px;z-index:5}
.tabs{display:flex;gap:2px;margin:14px 18px 0;border-bottom:1px solid var(--line)}
.tabs button{font:inherit;background:none;border:0;border-bottom:2px solid transparent;padding:6px 10px;color:var(--muted);cursor:pointer}
.tabs button.on{color:var(--fg);border-color:var(--accent)}
.step{margin:8px 18px;background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--c,var(--line));border-radius:8px}
.step.flash{outline:2px solid var(--accent)}
.sh{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:6px 10px;cursor:pointer}
.sh .n{font:600 12px ui-monospace,Menlo,monospace;color:var(--muted);min-width:28px}
.chip{font-size:11px;padding:1px 6px;border-radius:10px;background:var(--hover);color:var(--fg)}
.chip.a{color:#fff}
.chip.comp{background:var(--warn);color:#000}
.fn{font:600 12px ui-monospace,Menlo,monospace}
.sum{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:120px}
.meta{margin-left:auto;color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums;white-space:nowrap}
.sb{display:none;padding:0 10px 10px}
.step.open .sb{display:block}
.lbl{font-size:11px;color:var(--muted);margin:8px 0 3px;text-transform:uppercase;letter-spacing:.04em}
pre{margin:0;background:var(--code);border:1px solid var(--line);border-radius:6px;padding:8px;white-space:pre-wrap;word-break:break-word;
font:12px/1.45 ui-monospace,Menlo,monospace;max-height:460px;overflow:auto}
pre.th{color:var(--muted);font-style:italic}
.diff .a{color:var(--ok)} .diff .d{color:var(--bad)} .diff .h{color:var(--accent)}
.empty{color:var(--muted);padding:20px 18px}
@media (max-width:800px){.app{grid-template-columns:1fr;height:auto}aside{max-height:40vh}}
</style></head>
<body><div class="app">
<aside><header><h1 id="runname"></h1><div class="sub" id="runsub"></div>
<div class="controls"><input id="q" placeholder="filter id / error / tool…">
<select id="f"><option value="all">all</option><option value="ok">resolved</option><option value="bad">failed</option><option value="to">timeout</option></select>
<select id="s"><option value="id">id</option><option value="ctx">peak ctx</option><option value="dur">duration</option><option value="calls">calls</option><option value="comp">compactions</option></select></div>
<div class="sub" id="agg" style="margin-top:6px"></div></header>
<div class="list" id="list"></div></aside>
<main id="main"></main></div><div id="tip"></div>
<script>
const D=__DATA__;
const PAL=['#6366f1','#0891b2','#d97706','#db2777','#16a34a','#9333ea','#dc2626','#65a30d','#0d9488'];
const authors=[...new Set(D.tasks.flatMap(t=>t.steps.map(s=>s.author)).filter(Boolean))];
const fixed={harness:'#a8a29e',system:'#a8a29e',user:'#a8a29e'};let pi=0;const AC={};
for(const a of authors)AC[a]=fixed[a]||PAL[pi++%PAL.length];
const $=s=>document.querySelector(s);
const esc=s=>(s??'').toString().replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const fmtS=s=>{s=Math.round(s||0);return s>=60?`${Math.floor(s/60)}m${String(s%60).padStart(2,'0')}s`:`${s}s`};
const fmtK=n=>n==null?'–':n>=1000?(n/1000).toFixed(1)+'k':String(n);
const timeout=t=>/timeout|exceeded/i.test(t.error);
let cur=null;
$('#runname').textContent=D.meta.run_name||D.results_dir;
$('#runsub').textContent=[D.meta.model,D.meta.submission_fingerprint&&('fp '+D.meta.submission_fingerprint),
 D.meta.eval_config&&(`${D.meta.eval_config.max_tool_calls} calls / ${D.meta.eval_config.max_time_minutes} min`)].filter(Boolean).join(' · ');
const nOk=D.tasks.filter(t=>t.resolved).length;
const med=a=>{a=[...a].sort((x,y)=>x-y);return a.length?a[Math.floor(a.length/2)]:0};
$('#agg').textContent=`${nOk}/${D.tasks.length} resolved · ${D.tasks.filter(timeout).length} timeouts · median peak ctx ${fmtK(med(D.tasks.map(t=>t.peak_ctx)))} · median compactions ${med(D.tasks.map(t=>t.compactions))}`;
function renderList(){
 const q=$('#q').value.toLowerCase(),f=$('#f').value,s=$('#s').value;
 let ts=D.tasks.filter(t=>(f=='all'||(f=='ok'&&t.resolved)||(f=='bad'&&!t.resolved)||(f=='to'&&timeout(t)))&&
  (!q||t.id.toLowerCase().includes(q)||t.error.toLowerCase().includes(q)||t.steps.some(x=>x.calls.some(c=>(c.fn||'').includes(q)))));
 const key={id:t=>t.id,ctx:t=>-t.peak_ctx,dur:t=>-t.duration,calls:t=>-t.tool_calls,comp:t=>-t.compactions}[s];
 ts.sort((a,b)=>key(a)<key(b)?-1:key(a)>key(b)?1:0);
 $('#list').innerHTML=ts.map(t=>`<div class="row${cur&&cur.id==t.id?' sel':''}" data-id="${t.id}">
 <span class="dot" style="background:${t.resolved?'var(--ok)':timeout(t)?'var(--warn)':'var(--bad)'}"></span>
 <span class="nm">${esc(t.id)}</span>
 <span class="st">${t.tool_calls}c · ${fmtS(t.duration)} · ${fmtK(t.peak_ctx)}${t.compactions?` · ⟲${t.compactions}`:''}</span></div>`).join('')||'<div class="empty">no match</div>';
}
$('#list').onclick=e=>{const r=e.target.closest('.row');if(r)show(r.dataset.id)};
for(const id of['#q','#f','#s'])$(id).oninput=renderList;
const tip=$('#tip');
function tipOn(e,h){tip.innerHTML=h;tip.style.display='block';tip.style.left=Math.min(e.clientX+12,innerWidth-370)+'px';tip.style.top=(e.clientY+12)+'px'}
function tipOff(){tip.style.display='none'}
function stepSummary(s){
 if(s.calls.length)return s.calls.map(c=>{let a='';try{const o=JSON.parse(c.args);a=o.command||o.filepath||o.path||(o.args&&o.args.join(' '))||o.query||''}catch(_){};
  return `<span class="fn">${esc(c.fn)}</span> <span>${esc(String(a).slice(0,140))}</span>`}).join(' ; ');
 return esc((s.msg||s.thought||'').slice(0,200));
}
function ctxChart(t){
 const W=Math.max(600,$('#main').clientWidth-72),H=230,P={l:44,r:10,t:10,b:24};
 const llm=t.steps.filter(s=>s.pt);const T=Math.max(1,...t.steps.map(s=>s.t));
 const Y=Math.max(D.limits.max_model_len,...llm.map(s=>s.pt+(s.ct||0)));
 const x=v=>P.l+v/T*(W-P.l-P.r),y=v=>H-P.b-v/Y*(H-P.t-P.b);
 let g='<g class="grid">';for(let v=0;v<=Y;v+=8192)g+=`<line x1="${P.l}" x2="${W-P.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${P.l-6}" y="${y(v)+3}" text-anchor="end">${v/1024}k</text>`;
 const step=T>600?120:T>240?60:30;for(let v=0;v<=T;v+=step)g+=`<text x="${x(v)}" y="${H-6}" text-anchor="middle">${fmtS(v)}</text>`;g+='</g>';
 g+=`<line x1="${P.l}" x2="${W-P.r}" y1="${y(D.limits.compaction)}" y2="${y(D.limits.compaction)}" stroke="var(--warn)" stroke-dasharray="4 3"/>
 <text x="${W-P.r}" y="${y(D.limits.compaction)-4}" text-anchor="end" style="fill:var(--warn)">compaction ${fmtK(D.limits.compaction)}</text>
 <line x1="${P.l}" x2="${W-P.r}" y1="${y(D.limits.max_model_len)}" y2="${y(D.limits.max_model_len)}" stroke="var(--bad)" stroke-dasharray="4 3"/>
 <text x="${W-P.r}" y="${y(D.limits.max_model_len)-4}" text-anchor="end" style="fill:var(--bad)">max_model_len ${fmtK(D.limits.max_model_len)}</text>`;
 const by={};for(const s of llm)(by[s.author]=by[s.author]||[]).push(s);
 for(const[a,ss]of Object.entries(by)){
  g+=`<polyline fill="none" stroke="${AC[a]}" stroke-width="1.5" stroke-opacity=".6" points="${ss.map(s=>`${x(s.t)},${y(s.pt)}`).join(' ')}"/>`;
 }
 for(const s of llm){
  if(s.ct)g+=`<line x1="${x(s.t)}" x2="${x(s.t)}" y1="${y(s.pt)}" y2="${y(s.pt+s.ct)}" stroke="${AC[s.author]}" stroke-width="3" stroke-opacity=".35"/>`;
  g+=`<circle class="pt" data-id="${s.id}" cx="${x(s.t)}" cy="${y(s.pt)}" r="${s.compaction?5.5:3.5}" fill="${s.compaction?'var(--warn)':AC[s.author]}" stroke="${s.compaction?AC[s.author]:'none'}" stroke-width="2"/>`;
 }
 return `<svg viewBox="0 0 ${W} ${H}" height="${H}" id="ctx">${g}</svg>`;
}
function dtChart(t){
 const W=Math.max(600,$('#main').clientWidth-72),H=90,P={l:44,r:10,t:6,b:16};
 const T=Math.max(1,...t.steps.map(s=>s.t)),M=Math.max(30,...t.steps.map(s=>s.dt));
 const x=v=>P.l+v/T*(W-P.l-P.r),y=v=>H-P.b-v/M*(H-P.t-P.b);
 let g=`<g class="grid"><line x1="${P.l}" x2="${W-P.r}" y1="${y(0)}" y2="${y(0)}"/><text x="${P.l-6}" y="${y(M)+8}" text-anchor="end">${fmtS(M)}</text></g>`;
 for(const s of t.steps){if(!s.dt)continue;const x0=x(s.t-s.dt),x1=x(s.t);
  g+=`<rect class="pt" data-id="${s.id}" x="${x0}" y="${y(s.dt)}" width="${Math.max(1,x1-x0-1)}" height="${y(0)-y(s.dt)}" fill="${AC[s.author]||'#999'}" fill-opacity="${s.dt>30?.9:.5}"/>`}
 return `<svg viewBox="0 0 ${W} ${H}" height="${H}" id="dt">${g}</svg>`;
}
function renderSteps(t){
 return t.steps.map(s=>{const c=AC[s.author]||'#999';
  const parts=[];
  if(s.thought)parts.push(`<div class="lbl">thinking</div><pre class="th">${esc(s.thought)}</pre>`);
  if(s.msg)parts.push(`<div class="lbl">${s.src=='agent'?'message':s.ev||s.src}</div><pre>${esc(s.msg)}</pre>`);
  for(const k of s.calls)parts.push(`<div class="lbl">call · ${esc(k.fn)}</div><pre>${esc(k.args)}</pre>`);
  if(s.obs!=null)parts.push(`<div class="lbl">observation${s.obs_tool?' · '+esc(s.obs_tool):''} · ${(s.obs||'').length.toLocaleString()} chars</div><pre>${esc(s.obs)}</pre>`);
  return `<div class="step" id="s${s.id}" style="--c:${c}"><div class="sh"><span class="n">#${s.id}</span>
  <span class="chip a" style="background:${c}">${esc(s.author||'?')}</span>${s.ev&&s.ev!='tool'?`<span class="chip">${esc(s.ev)}</span>`:''}
  ${s.compaction?'<span class="chip comp">compacted</span>':''}<span class="sum">${stepSummary(s)}</span>
  <span class="meta">${fmtS(s.t)} · +${s.dt.toFixed(1)}s${s.pt?` · in ${fmtK(s.pt)} / out ${fmtK(s.ct)}`:''}</span></div>
  <div class="sb">${parts.join('')||'<div class="sub">(empty)</div>'}</div></div>`}).join('');
}
function diffHtml(p){return p?`<pre class="diff">${p.split('\n').map(l=>`<span class="${l.startsWith('+++')||l.startsWith('---')||l.startsWith('@@')?'h':l[0]=='+'?'a':l[0]=='-'?'d':''}">${esc(l)}</span>`).join('\n')}</pre>`:'<div class="empty">no patch</div>'}
let tab='steps';
function show(id){
 cur=D.tasks.find(t=>t.id==id);if(!cur)return;history.replaceState(null,'','#'+id);renderList();
 const t=cur,llm=t.steps.filter(s=>s.llm);
 const byA={};for(const s of t.steps)if(s.author&&s.dt)byA[s.author]=(byA[s.author]||0)+s.dt;
 const slow=t.steps.filter(s=>s.dt>30).reduce((a,s)=>a+s.dt,0);
 $('#main').innerHTML=`<div class="top"><h2>${esc(t.id)} <span style="color:${t.resolved?'var(--ok)':'var(--bad)'};font-size:13px">${t.resolved?'resolved':'failed'}</span></h2>
 <div class="kpis">
 <div class="kpi"><b>${t.tool_calls}</b><span>tool calls</span></div>
 <div class="kpi"><b>${llm.length}</b><span>LLM calls</span></div>
 <div class="kpi"><b>${fmtS(t.duration)}</b><span>duration</span></div>
 <div class="kpi"><b>${fmtK(t.peak_ctx)}</b><span>peak prompt</span></div>
 <div class="kpi"><b>${t.compactions}</b><span>compactions</span></div>
 <div class="kpi"><b>${fmtK(t.final_metrics.total_prompt_tokens)} / ${fmtK(t.final_metrics.total_completion_tokens)}</b><span>total in / out</span></div>
 <div class="kpi"><b>${fmtS(slow)}</b><span>in steps &gt;30s</span></div>
 ${Object.entries(byA).map(([a,v])=>`<div class="kpi"><b style="color:${AC[a]}">${fmtS(v)}</b><span>${esc(a)}</span></div>`).join('')}
 </div>${t.error?`<div class="err">${esc(t.error)}</div>`:''}</div>
 <div class="card"><h3>Context window — prompt tokens per LLM call</h3>
 <div class="legend">${[...new Set(t.steps.map(s=>s.author))].filter(a=>a&&!fixed[a]).map(a=>`<span><i style="background:${AC[a]}"></i>${esc(a)}</span>`).join('')}
 <span><i style="background:var(--warn);border-radius:50%"></i>compaction (prompt dropped &gt;25%)</span><span>faint bar = completion tokens</span></div>${ctxChart(t)}
 <h3 style="margin-top:10px">Step duration</h3>${dtChart(t)}</div>
 <div class="tabs"><button data-t="steps">Steps (${t.steps.length})</button><button data-t="patch">Patch</button><button data-t="log">Test log</button>
 <button data-t="expand" style="margin-left:auto">expand all</button></div><div id="tabbody"></div>`;
 setTab(tab);
 $('.tabs').onclick=e=>{const b=e.target.closest('button');if(!b)return;
  if(b.dataset.t=='expand'){const all=[...document.querySelectorAll('.step')];const o=!all.every(x=>x.classList.contains('open'));all.forEach(x=>x.classList.toggle('open',o));return}
  setTab(b.dataset.t)};
 for(const sv of['#ctx','#dt']){const el=$(sv);
  el.onmousemove=e=>{const p=e.target.closest('.pt');if(!p)return tipOff();const s=t.steps.find(x=>x.id==p.dataset.id);
   tipOn(e,`<b>#${s.id}</b> ${esc(s.author)} · ${fmtS(s.t)} (+${s.dt.toFixed(1)}s)<br>${s.pt?`prompt ${s.pt.toLocaleString()} · completion ${(s.ct||0).toLocaleString()}<br>`:''}${s.compaction?'<b style="color:var(--warn)">compacted</b><br>':''}${stepSummary(s)}`)};
  el.onmouseleave=tipOff;
  el.onclick=e=>{const p=e.target.closest('.pt');if(!p)return;if(tab!='steps')setTab('steps');const st=$('#s'+p.dataset.id);
   st.classList.add('open','flash');st.scrollIntoView({behavior:'smooth',block:'center'});setTimeout(()=>st.classList.remove('flash'),1200)}}
}
function setTab(n){tab=n;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t==n));
 const b=$('#tabbody');
 if(n=='steps'){b.innerHTML=renderSteps(cur);b.onclick=e=>{const h=e.target.closest('.sh');if(h)h.parentElement.classList.toggle('open')}}
 else if(n=='patch'){b.innerHTML=`<div class="card">${diffHtml(cur.patch)}</div>`;b.onclick=null}
 else{b.innerHTML=`<div class="card">${cur.test_log?`<pre>${esc(cur.test_log)}</pre>`:'<div class="empty">no test log</div>'}</div>`;b.onclick=null}}
addEventListener('keydown',e=>{if(e.target.tagName=='INPUT')return;const rows=[...document.querySelectorAll('.row')];const i=rows.findIndex(r=>r.classList.contains('sel'));
 if(e.key=='j'||e.key=='ArrowDown'){rows[Math.min(rows.length-1,i+1)]?.click();e.preventDefault()}
 if(e.key=='k'||e.key=='ArrowUp'){rows[Math.max(0,i-1)]?.click();e.preventDefault()}});
addEventListener('resize',()=>cur&&show(cur.id));
renderList();show(decodeURIComponent(location.hash.slice(1))||D.tasks[0].id);
</script></body></html>
"""

if __name__ == "__main__":
    main()
