"""FastAPI wrapper for the Biomni A1 agent.

Used by ECS Fargate, ECS EC2, EKS, and Raw EC2 deployments.
Lambda uses lambda_handler.py instead.
"""

import asyncio
import io
import json
import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from biomni.agent import A1

_agent: A1 = None

_CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Biomni PKPD Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f0f2f5; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  #app { width: 100%; max-width: 860px; height: 92vh; display: flex; flex-direction: column; background: white; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.12); overflow: hidden; }
  header { padding: 14px 24px; background: #1a1a2e; color: white; font-size: 17px; font-weight: 600; display: flex; align-items: center; gap: 10px; }
  header span { font-size: 12px; font-weight: 400; opacity: 0.65; }
  #messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 14px; }
  .msg { max-width: 88%; padding: 11px 15px; border-radius: 12px; line-height: 1.55; font-size: 14px; white-space: pre-wrap; word-break: break-word; }
  .user { align-self: flex-end; background: #1a1a2e; color: white; border-bottom-right-radius: 4px; }
  .agent { align-self: flex-start; background: #f0f2f5; color: #1a1a2e; border-bottom-left-radius: 4px; }
  .thinking { opacity: 0.45; font-style: italic; }
  .file-badge { display: inline-block; background: rgba(255,255,255,0.2); border-radius: 4px; padding: 2px 7px; font-size: 12px; margin-right: 4px; margin-bottom: 4px; }
  .file-badge-agent { background: #dde; color: #334; border-radius: 4px; padding: 2px 7px; font-size: 12px; margin-right: 4px; }
  #file-preview { padding: 6px 16px; background: #f8f8ff; border-top: 1px solid #eee; display: none; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 13px; color: #555; }
  .fp-chip { background: #e8eaf6; border-radius: 20px; padding: 3px 10px; display: flex; align-items: center; gap: 5px; }
  .fp-chip button { background: none; border: none; cursor: pointer; color: #888; font-size: 14px; padding: 0; line-height: 1; }
  #input-row { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #eee; align-items: flex-end; }
  #prompt { flex: 1; padding: 10px 14px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; resize: none; min-height: 44px; max-height: 120px; outline: none; font-family: inherit; }
  #prompt:focus { border-color: #1a1a2e; }
  .icon-btn { background: none; border: 1px solid #ddd; border-radius: 8px; padding: 9px 12px; cursor: pointer; font-size: 16px; color: #555; display: flex; align-items: center; }
  .icon-btn:hover { background: #f5f5f5; }
  #send { padding: 10px 20px; background: #1a1a2e; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; height: 44px; }
  #send:disabled { opacity: 0.45; cursor: not-allowed; }
  #file-input { display: none; }
</style>
</head>
<body>
<div id="app">
  <header>
    🧬 Biomni PKPD Agent
    <span>Pharmacokinetics · DMPK · PopPK · NCA · CDISC</span>
  </header>
  <div id="messages">
    <div class="msg agent">Hello! I'm the Biomni PKPD Agent. I can help with pharmacokinetic calculations, NCA, population PK modeling, DMPK analysis, and CDISC datasets.<br><br>You can type a question or <strong>upload files</strong> (CSV, NONMEM .lst/.ctl, Excel) and ask me to analyze them.</div>
  </div>
  <div id="file-preview"></div>
  <div id="input-row">
    <button class="icon-btn" onclick="document.getElementById('file-input').click()" title="Attach files">📎</button>
    <input type="file" id="file-input" multiple accept=".csv,.lst,.ctl,.mod,.ext,.cov,.cor,.txt,.xlsx,.xls,.json" onchange="onFiles(this)">
    <textarea id="prompt" placeholder="Ask a PKPD question, or attach a file and describe what you need..." rows="1" oninput="autoResize(this)"></textarea>
    <button id="send" onclick="send()">Send</button>
  </div>
</div>
<script>
  let attachedFiles = [];
  const messages = document.getElementById('messages');
  const prompt = document.getElementById('prompt');
  const sendBtn = document.getElementById('send');
  const filePreview = document.getElementById('file-preview');

  prompt.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }

  function onFiles(input) {
    for (const f of input.files) attachedFiles.push(f);
    input.value = '';
    renderFilePreview();
  }

  function renderFilePreview() {
    if (attachedFiles.length === 0) { filePreview.style.display = 'none'; return; }
    filePreview.style.display = 'flex';
    filePreview.innerHTML = '📎 ' + attachedFiles.map((f, i) =>
      `<span class="fp-chip">${escHtml(f.name)} <button onclick="removeFile(${i})">×</button></span>`
    ).join('');
  }

  function removeFile(i) {
    attachedFiles.splice(i, 1);
    renderFilePreview();
  }

  async function send() {
    const text = prompt.value.trim();
    if (!text && attachedFiles.length === 0) return;
    sendBtn.disabled = true;

    // Show user message
    let userHtml = '';
    if (attachedFiles.length) userHtml += attachedFiles.map(f => `<span class="file-badge">📄 ${escHtml(f.name)}</span>`).join('') + '<br>';
    if (text) userHtml += escHtml(text);
    messages.innerHTML += `<div class="msg user">${userHtml}</div>`;

    const files = [...attachedFiles];
    attachedFiles = [];
    renderFilePreview();
    prompt.value = '';
    prompt.style.height = 'auto';

    const thinking = document.createElement('div');
    thinking.className = 'msg agent thinking';
    thinking.textContent = 'Analyzing' + (files.length ? ' files' : '') + '...';
    messages.appendChild(thinking);
    messages.scrollTop = messages.scrollHeight;

    try {
      let result;
      if (files.length > 0) {
        const fd = new FormData();
        fd.append('prompt', text || 'Please analyze the attached file(s) and provide insights relevant to pharmacokinetics or drug metabolism.');
        for (const f of files) fd.append('files', f);
        const res = await fetch('/upload', { method: 'POST', body: fd });
        const data = await res.json();
        result = data.result || data.detail || 'Error';
      } else {
        const res = await fetch('/run', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prompt: text})
        });
        const data = await res.json();
        result = data.result || data.detail || 'Error';
      }
      thinking.className = 'msg agent';
      thinking.textContent = extractSolution(result);
    } catch(e) {
      thinking.className = 'msg agent';
      thinking.textContent = 'Error: ' + e.message;
    }
    sendBtn.disabled = false;
    messages.scrollTop = messages.scrollHeight;
    prompt.focus();
  }

  function extractSolution(raw) {
    const m = raw.match(/<solution>([\\s\\S]*?)<\\/solution>/);
    if (m) return m[1].trim();
    return raw.replace(/\\\\n/g, '\\n').trim();
  }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
</script>
</body>
</html>"""


def _parse_file(filename: str, content: bytes) -> str:
    """Parse uploaded file into a text representation for the agent."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("csv",):
        try:
            import pandas as pd
            df = pd.read_csv(io.BytesIO(content))
            lines = [
                f"File: {filename}",
                f"Shape: {df.shape[0]} rows × {df.shape[1]} columns",
                f"Columns: {list(df.columns)}",
                f"Data types:\n{df.dtypes.to_string()}",
                f"\nFirst 20 rows:\n{df.head(20).to_string(index=False)}",
            ]
            if df.shape[0] > 20:
                lines.append(f"\n... ({df.shape[0] - 20} more rows)")
            return "\n".join(lines)
        except Exception as e:
            return f"File: {filename}\n[Could not parse CSV: {e}]\nRaw content:\n{content.decode('utf-8', errors='replace')[:3000]}"

    elif ext in ("xlsx", "xls"):
        try:
            import pandas as pd
            xl = pd.ExcelFile(io.BytesIO(content))
            parts = [f"File: {filename}", f"Sheets: {xl.sheet_names}"]
            for sheet in xl.sheet_names[:3]:
                df = xl.parse(sheet)
                parts.append(f"\nSheet '{sheet}': {df.shape[0]} rows × {df.shape[1]} columns")
                parts.append(df.head(15).to_string(index=False))
            return "\n".join(parts)
        except Exception as e:
            return f"File: {filename}\n[Could not parse Excel: {e}]"

    elif ext in ("lst", "ctl", "mod", "ext", "cov", "cor", "txt", "json"):
        text = content.decode("utf-8", errors="replace")
        if len(text) > 8000:
            text = text[:8000] + f"\n\n... [truncated, total {len(text)} chars]"
        return f"File: {filename}\n\n{text}"

    else:
        try:
            text = content.decode("utf-8", errors="replace")
            if len(text) > 4000:
                text = text[:4000] + f"\n... [truncated]"
            return f"File: {filename}\n\n{text}"
        except Exception:
            return f"File: {filename}\n[Binary file — cannot display content]"


def _build_agent() -> A1:
    skip_datalake = os.environ.get("BIOMNI_SKIP_DATALAKE", "false").lower() == "true"
    return A1(
        path=os.environ.get("BIOMNI_DATA_PATH", "./data"),
        llm=os.environ.get("BIOMNI_LLM", "claude-sonnet-4-20250514"),
        source=os.environ.get("LLM_SOURCE", "Anthropic"),
        base_url=os.environ.get("BIOMNI_CUSTOM_BASE_URL") or None,
        api_key=os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or None,
        commercial_mode=os.environ.get("BIOMNI_COMMERCIAL_MODE", "false").lower()
        == "true",
        expected_data_lake_files=[] if skip_datalake else None,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    _agent = _build_agent()
    yield
    _agent = None


app = FastAPI(title="Biomni Agent API", version="0.0.8", lifespan=lifespan)


class PromptRequest(BaseModel):
    prompt: str


class PromptResponse(BaseModel):
    result: str


@app.get("/", response_class=HTMLResponse)
def chat_ui():
    return HTMLResponse(content=_CHAT_HTML)


@app.get("/health")
def health():
    return {"status": "ok", "agent_ready": _agent is not None}


@app.post("/run", response_model=PromptResponse)
async def run_agent(req: PromptRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _agent.go, req.prompt)
    return PromptResponse(result=str(result))


@app.post("/upload", response_model=PromptResponse)
async def run_agent_with_files(
    prompt: str = Form(...),
    files: List[UploadFile] = File(default=[]),
):
    parts = []
    for f in files:
        content = await f.read()
        parts.append(_parse_file(f.filename, content))

    file_context = "\n\n---\n\n".join(parts)
    full_prompt = f"{prompt}\n\n=== UPLOADED FILES ===\n\n{file_context}" if file_context else prompt

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _agent.go, full_prompt)
    return PromptResponse(result=str(result))


@app.get("/stream")
async def stream_agent(prompt: str):
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    async def generate():
        for chunk in _agent.go_stream(prompt):
            yield f"data: {json.dumps({'chunk': str(chunk)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
