"""Debug page + endpoints to choose which activity incoming windows are saved under.
Only active when CAPTURE_ENABLED=1; otherwise every route here returns 404."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core import capture

router = APIRouter(prefix="/capture", tags=["capture"])


class LabelBody(BaseModel):
    label: str | None = None   # null / "off" stops saving


def _require_enabled():
    if not capture.ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/status")
def capture_status():
    _require_enabled()
    return capture.status()


@router.post("/label")
def capture_label(body: LabelBody):
    _require_enabled()
    label = None if body.label in (None, "", "off") else body.label.strip().lower()
    try:
        capture.set_label(label)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return capture.status()


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Capture activity</title>
<style>
 :root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--card:#f3f3f3;--on:#0a7d3b;--off:#b3261e}
 @media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eee;--mut:#9a9a9a;--card:#262626;--on:#4cc27a;--off:#ff8a80}}
 body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:560px;margin:0 auto;padding:16px}
 h1{font-size:1.2rem} .now{padding:12px;border-radius:10px;background:var(--card);margin:12px 0}
 .now b{font-size:1.3rem} .on{color:var(--on)} .off{color:var(--off)}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
 button{padding:14px;border-radius:10px;border:1px solid #8884;background:var(--card);color:var(--fg);font-size:1rem}
 button.sel{outline:3px solid var(--on)} .count{display:block;font-size:.8rem;color:var(--mut)}
 .row{display:flex;gap:8px;margin-top:12px} input{flex:1;padding:12px;border-radius:10px;border:1px solid #8884;background:var(--bg);color:var(--fg)}
 .small{color:var(--mut);font-size:.85rem;margin-top:12px}
</style></head><body>
<h1>Capture activity</h1>
<div class="now">Saving as: <b id="cur" class="off">off</b><div class="small" id="last"></div></div>
<div class="grid" id="grid"></div>
<div class="row"><input id="custom" placeholder="custom name, e.g. stairs" maxlength="30">
<button onclick="setLabel(document.getElementById('custom').value)">Use</button></div>
<div class="row"><button style="flex:1" onclick="setLabel('off')">Stop saving (off)</button></div>
<p class="small" id="folder"></p>
<p class="small">Tap an activity, then do it. The first window after you tap is skipped so the moment you get into position is not saved. Tap Stop before you change position.</p>
<script>
async function load(){
  const r = await fetch('/capture/status'); const s = await r.json();
  const cur = document.getElementById('cur');
  cur.textContent = s.label || 'off'; cur.className = s.label ? 'on' : 'off';
  document.getElementById('last').textContent = s.last;
  document.getElementById('folder').textContent = 'Files go to: ' + s.folder;
  const g = document.getElementById('grid'); g.innerHTML = '';
  Object.keys(s.counts).forEach(l => {
    const b = document.createElement('button');
    b.innerHTML = l + '<span class="count">' + s.counts[l] + ' windows saved</span>';
    if (l === s.label) b.className = 'sel';
    b.onclick = () => setLabel(l); g.appendChild(b);
  });
}
async function setLabel(l){
  const r = await fetch('/capture/label', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({label:l})});
  if(!r.ok){ const e = await r.json(); alert(e.detail); }
  load();
}
load(); setInterval(load, 1500);
</script></body></html>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def capture_page():
    _require_enabled()
    return PAGE
