"""FastAPI wrapper for the Biomni A1 agent with session + long-term memory."""

import asyncio
import io
import json
import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from biomni.agent import A1

_agent: A1 = None

_MEMORY_ENABLED = os.getenv("BIOMNI_MEMORY", "true").lower() == "true"

_CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Biomni PKPD Agent</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f0f2f5;height:100vh;display:flex;align-items:center;justify-content:center}
  #app{width:100%;max-width:1100px;height:92vh;display:flex;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.12);overflow:hidden;background:white}
  /* Sidebar */
  #sidebar{width:240px;min-width:240px;background:#1a1a2e;display:flex;flex-direction:column;color:white}
  #sidebar header{padding:16px;font-size:15px;font-weight:700;border-bottom:1px solid rgba(255,255,255,0.1)}
  #sidebar header small{display:block;font-size:11px;font-weight:400;opacity:0.6;margin-top:2px}
  #new-chat{margin:10px;padding:9px;background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.2);border-radius:8px;color:white;cursor:pointer;font-size:13px;text-align:left}
  #new-chat:hover{background:rgba(255,255,255,0.2)}
  #sessions{flex:1;overflow-y:auto;padding:6px}
  .sess-item{padding:8px 10px;border-radius:6px;cursor:pointer;font-size:12px;color:rgba(255,255,255,0.75);margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sess-item:hover{background:rgba(255,255,255,0.1)}
  .sess-item.active{background:rgba(255,255,255,0.18);color:white}
  #memory-badge{margin:8px 10px;padding:6px 10px;background:rgba(255,200,0,0.15);border:1px solid rgba(255,200,0,0.3);border-radius:6px;font-size:11px;color:rgba(255,220,100,0.9)}
  /* Main chat */
  #main{flex:1;display:flex;flex-direction:column;min-width:0}
  #messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:14px}
  .msg{max-width:88%;padding:11px 15px;border-radius:12px;line-height:1.55;font-size:14px;white-space:pre-wrap;word-break:break-word}
  .user{align-self:flex-end;background:#1a1a2e;color:white;border-bottom-right-radius:4px}
  .agent{align-self:flex-start;background:#f0f2f5;color:#1a1a2e;border-bottom-left-radius:4px}
  .thinking{opacity:0.45;font-style:italic}
  .file-badge{display:inline-block;background:rgba(255,255,255,0.2);border-radius:4px;padding:2px 7px;font-size:12px;margin-right:4px;margin-bottom:4px}
  #file-preview{padding:6px 16px;background:#f8f8ff;border-top:1px solid #eee;display:none;flex-wrap:wrap;gap:6px;align-items:center;font-size:13px;color:#555}
  .fp-chip{background:#e8eaf6;border-radius:20px;padding:3px 10px;display:flex;align-items:center;gap:5px}
  .fp-chip button{background:none;border:none;cursor:pointer;color:#888;font-size:14px;padding:0;line-height:1}
  #input-row{display:flex;gap:8px;padding:12px 16px;border-top:1px solid #eee;align-items:flex-end}
  #prompt{flex:1;padding:10px 14px;border:1px solid #ddd;border-radius:8px;font-size:14px;resize:none;min-height:44px;max-height:120px;outline:none;font-family:inherit}
  #prompt:focus{border-color:#1a1a2e}
  .icon-btn{background:none;border:1px solid #ddd;border-radius:8px;padding:9px 12px;cursor:pointer;font-size:16px;color:#555;display:flex;align-items:center}
  .icon-btn:hover{background:#f5f5f5}
  #send{padding:10px 20px;background:#1a1a2e;color:white;border:none;border-radius:8px;cursor:pointer;font-size:14px;height:44px}
  #send:disabled{opacity:0.45;cursor:not-allowed}
  #file-input{display:none}
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <header>🧬 Biomni PKPD<small>Pharmacokinetics AI Agent</small></header>
    <button id="new-chat" onclick="newChat()">+ New Conversation</button>
    <div id="memory-badge">🧠 Long-term memory active</div>
    <div id="sessions"></div>
  </div>
  <div id="main">
    <div id="messages">
      <div class="msg agent">Hello! I'm the Biomni PKPD Agent with memory. I remember facts from previous sessions and maintain context within our conversation.<br><br>Ask me anything about pharmacokinetics, NCA, PopPK, DMPK, or CDISC — or attach a file (CSV, NONMEM, Excel, PDF).</div>
    </div>
    <div id="file-preview"></div>
    <div id="input-row">
      <button class="icon-btn" onclick="document.getElementById('file-input').click()" title="Attach files">📎</button>
      <input type="file" id="file-input" multiple accept=".csv,.lst,.ctl,.mod,.ext,.cov,.cor,.txt,.xlsx,.xls,.json,.pdf" onchange="onFiles(this)">
      <textarea id="prompt" placeholder="Ask a PKPD question..." rows="1" oninput="autoResize(this)"></textarea>
      <button id="send" onclick="send()">Send</button>
    </div>
  </div>
</div>
<script>
  let sessionId = null;
  let attachedFiles = [];
  const messages = document.getElementById('messages');
  const prompt = document.getElementById('prompt');
  const sendBtn = document.getElementById('send');
  const filePreview = document.getElementById('file-preview');

  // ── Init ──
  window.onload = async () => {
    sessionId = localStorage.getItem('biomni_session');
    try {
      if (sessionId) await loadSession(sessionId);
      else await newChat();
      await loadSessionList();
    } catch(e) {
      console.warn('Memory unavailable, running without sessions:', e);
      sessionId = 'local-' + Date.now();
    }
  };

  async function newChat() {
    try {
      const res = await fetch('/sessions', {method:'POST'});
      const data = await res.json();
      sessionId = data.session_id || ('local-' + Date.now());
    } catch(e) {
      sessionId = 'local-' + Date.now();
    }
    localStorage.setItem('biomni_session', sessionId);
    messages.innerHTML = '<div class="msg agent">Hello! I\'m the Biomni PKPD Agent. I remember facts from previous sessions. Ask me anything about pharmacokinetics, NCA, PopPK, DMPK, or attach a file.</div>';
    try { await loadSessionList(); } catch(e) {}
  }

  async function loadSession(id) {
    const res = await fetch('/sessions/' + id);
    if (!res.ok) { await newChat(); return; }
    const data = await res.json();
    const msgs = data.messages || [];
    if (msgs.length === 0) return;
    messages.innerHTML = '';
    for (const msg of msgs) {
      appendMessage(msg.role === 'user' ? 'user' : 'agent', msg.content);
    }
    messages.scrollTop = messages.scrollHeight;
  }

  async function switchSession(id) {
    sessionId = id;
    localStorage.setItem('biomni_session', sessionId);
    await loadSession(id);
    await loadSessionList();
  }

  async function loadSessionList() {
    try {
      const res = await fetch('/sessions');
      const data = await res.json();
      const container = document.getElementById('sessions');
      container.innerHTML = data.map(s =>
        `<div class="sess-item ${s.session_id === sessionId ? 'active' : ''}"
              onclick="switchSession('${s.session_id}')"
              title="${escHtml(s.title || 'Conversation')}">${escHtml(s.title || 'Conversation')}</div>`
      ).join('');
    } catch(e) {}
  }

  // ── Input ──
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
    if (!attachedFiles.length) { filePreview.style.display='none'; return; }
    filePreview.style.display = 'flex';
    filePreview.innerHTML = '📎 ' + attachedFiles.map((f,i) =>
      `<span class="fp-chip">${escHtml(f.name)} <button onclick="removeFile(${i})">×</button></span>`
    ).join('');
  }

  function removeFile(i) { attachedFiles.splice(i,1); renderFilePreview(); }

  // ── Send ──
  async function send() {
    const text = prompt.value.trim();
    if (!text && !attachedFiles.length) return;
    sendBtn.disabled = true;

    let userHtml = '';
    if (attachedFiles.length) userHtml += attachedFiles.map(f=>`<span class="file-badge">📄 ${escHtml(f.name)}</span>`).join('') + '<br>';
    if (text) userHtml += escHtml(text);
    appendMessage('user', null, userHtml);

    const files = [...attachedFiles];
    attachedFiles = []; renderFilePreview();
    prompt.value = ''; prompt.style.height = 'auto';

    const thinking = appendMessage('agent', 'Analyzing' + (files.length ? ' files' : '') + '...', null, true);
    messages.scrollTop = messages.scrollHeight;

    try {
      let result;
      if (files.length > 0) {
        const fd = new FormData();
        fd.append('prompt', text || 'Please analyze the attached file(s) and provide PKPD insights.');
        fd.append('session_id', sessionId || '');
        for (const f of files) fd.append('files', f);
        const res = await fetch('/upload', {method:'POST', body:fd});
        result = (await res.json()).result;
      } else {
        const res = await fetch('/run', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({prompt: text, session_id: sessionId})
        });
        result = (await res.json()).result;
      }
      thinking.className = 'msg agent';
      thinking.textContent = extractSolution(result || 'No response');
    } catch(e) {
      thinking.className = 'msg agent';
      thinking.textContent = 'Error: ' + e.message;
    }
    sendBtn.disabled = false;
    messages.scrollTop = messages.scrollHeight;
    await loadSessionList();
    prompt.focus();
  }

  function appendMessage(role, text, html, isThinking=false) {
    const el = document.createElement('div');
    el.className = 'msg ' + role + (isThinking ? ' thinking' : '');
    if (html) el.innerHTML = html;
    else el.textContent = text || '';
    messages.appendChild(el);
    return el;
  }

  function extractSolution(raw) {
    const m = raw.match(/<solution>([\s\S]*?)<\/solution>/);
    if (m) return m[1].trim();
    return raw.replace(/\\n/g, '\n').trim();
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# File parsing
# ─────────────────────────────────────────────────────────────

def _parse_file(filename: str, content: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "csv":
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
            return f"File: {filename}\n[CSV parse error: {e}]\n{content.decode('utf-8', errors='replace')[:3000]}"

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
            return f"File: {filename}\n[Excel parse error: {e}]"

    elif ext == "pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            pages = [f"[Page {i+1}]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages)]
            text = "\n\n".join(pages)
            if len(text) > 80000:
                text = text[:80000] + f"\n\n... [truncated — {len(reader.pages)} pages total]"
            return f"File: {filename} ({len(reader.pages)} pages)\n\n{text}"
        except Exception as e:
            return f"File: {filename}\n[PDF parse error: {e}]"

    elif ext in ("lst", "ctl", "mod", "ext", "cov", "cor", "txt", "json"):
        text = content.decode("utf-8", errors="replace")
        if len(text) > 40000:
            text = text[:40000] + f"\n\n... [truncated — {len(text)} chars total]"
        return f"File: {filename}\n\n{text}"

    else:
        try:
            text = content.decode("utf-8", errors="replace")
            if len(text) > 20000:
                text = text[:20000] + "\n... [truncated]"
            return f"File: {filename}\n\n{text}"
        except Exception:
            return f"File: {filename}\n[Binary file]"


# ─────────────────────────────────────────────────────────────
# Agent + app setup
# ─────────────────────────────────────────────────────────────

def _build_agent() -> A1:
    skip_datalake = os.environ.get("BIOMNI_SKIP_DATALAKE", "false").lower() == "true"
    return A1(
        path=os.environ.get("BIOMNI_DATA_PATH", "./data"),
        llm=os.environ.get("BIOMNI_LLM", "claude-sonnet-4-20250514"),
        source=os.environ.get("LLM_SOURCE", "Anthropic"),
        base_url=os.environ.get("BIOMNI_CUSTOM_BASE_URL") or None,
        api_key=os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or None,
        commercial_mode=os.environ.get("BIOMNI_COMMERCIAL_MODE", "false").lower() == "true",
        expected_data_lake_files=[] if skip_datalake else None,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    _agent = _build_agent()
    yield
    _agent = None


app = FastAPI(title="Biomni Agent API", version="0.1.0", lifespan=lifespan)


# ─────────────────────────────────────────────────────────────
# Session endpoints
# ─────────────────────────────────────────────────────────────

@app.post("/sessions")
def new_session():
    if not _MEMORY_ENABLED:
        return {"session_id": None}
    from memory import create_session
    return {"session_id": create_session()}


@app.get("/sessions")
def list_sessions():
    if not _MEMORY_ENABLED:
        return []
    from memory import list_sessions as _list
    return _list()


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    if not _MEMORY_ENABLED:
        return {"messages": []}
    from memory import get_session as _get
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# ─────────────────────────────────────────────────────────────
# Core endpoints
# ─────────────────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str
    session_id: Optional[str] = None


class PromptResponse(BaseModel):
    result: str


@app.get("/", response_class=HTMLResponse)
def chat_ui():
    return HTMLResponse(content=_CHAT_HTML)


@app.get("/health")
def health():
    return {"status": "ok", "agent_ready": _agent is not None, "memory": _MEMORY_ENABLED}


@app.post("/run", response_model=PromptResponse)
async def run_agent(req: PromptRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    full_prompt = req.prompt
    if _MEMORY_ENABLED and req.session_id:
        from memory import build_prompt, add_message
        full_prompt = build_prompt(req.prompt, req.session_id)
        add_message(req.session_id, "user", req.prompt, title_hint=req.prompt)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _agent.go, full_prompt)
    result_str = str(result)

    if _MEMORY_ENABLED and req.session_id:
        from memory import add_message, extract_and_store_memory
        add_message(req.session_id, "agent", result_str)
        loop.run_in_executor(None, extract_and_store_memory, req.prompt, result_str, req.session_id, _agent.llm)

    return PromptResponse(result=result_str)


@app.post("/upload", response_model=PromptResponse)
async def run_agent_with_files(
    prompt: str = Form(...),
    session_id: str = Form(default=""),
    files: List[UploadFile] = File(default=[]),
):
    parts = []
    for f in files:
        parts.append(_parse_file(f.filename, await f.read()))

    file_context = "\n\n---\n\n".join(parts)
    base_prompt = f"{prompt}\n\n=== UPLOADED FILES ===\n\n{file_context}" if file_context else prompt

    full_prompt = base_prompt
    if _MEMORY_ENABLED and session_id:
        from memory import build_prompt, add_message
        full_prompt = build_prompt(base_prompt, session_id)
        add_message(session_id, "user", prompt or "Uploaded file(s)", title_hint=prompt or f.filename if files else "File upload")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _agent.go, full_prompt)
    result_str = str(result)

    if _MEMORY_ENABLED and session_id:
        from memory import add_message, extract_and_store_memory
        add_message(session_id, "agent", result_str)
        loop.run_in_executor(None, extract_and_store_memory, base_prompt[:400], result_str, session_id, _agent.llm)

    return PromptResponse(result=result_str)


@app.get("/stream")
async def stream_agent(prompt: str):
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    async def generate():
        for chunk in _agent.go_stream(prompt):
            yield f"data: {json.dumps({'chunk': str(chunk)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
