"""FastAPI wrapper for the Biomni A1 agent.

Used by ECS Fargate, ECS EC2, EKS, and Raw EC2 deployments.
Lambda uses lambda_handler.py instead.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
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
  #app { width: 100%; max-width: 800px; height: 90vh; display: flex; flex-direction: column; background: white; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); overflow: hidden; }
  header { padding: 16px 24px; background: #1a1a2e; color: white; font-size: 18px; font-weight: 600; }
  header span { font-size: 13px; font-weight: 400; opacity: 0.7; margin-left: 8px; }
  #messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
  .msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; word-break: break-word; }
  .user { align-self: flex-end; background: #1a1a2e; color: white; border-bottom-right-radius: 4px; }
  .agent { align-self: flex-start; background: #f0f2f5; color: #1a1a2e; border-bottom-left-radius: 4px; }
  .thinking { opacity: 0.5; font-style: italic; }
  #input-row { display: flex; gap: 8px; padding: 16px; border-top: 1px solid #eee; }
  #prompt { flex: 1; padding: 10px 14px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; resize: none; height: 44px; outline: none; }
  #prompt:focus { border-color: #1a1a2e; }
  button { padding: 10px 20px; background: #1a1a2e; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
</head>
<body>
<div id="app">
  <header>Biomni PKPD Agent <span>Powered by Claude on AWS Bedrock</span></header>
  <div id="messages">
    <div class="msg agent">Hello! I'm the Biomni PKPD Agent. Ask me anything about pharmacokinetics, drug metabolism, NCA, or population PK modeling.</div>
  </div>
  <div id="input-row">
    <textarea id="prompt" placeholder="Ask a pharmacokinetics question..." rows="1"></textarea>
    <button id="send" onclick="send()">Send</button>
  </div>
</div>
<script>
  const messages = document.getElementById('messages');
  const prompt = document.getElementById('prompt');
  const send_btn = document.getElementById('send');

  prompt.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  async function send() {
    const text = prompt.value.trim();
    if (!text) return;
    prompt.value = '';
    send_btn.disabled = true;

    messages.innerHTML += `<div class="msg user">${escHtml(text)}</div>`;
    const thinking = document.createElement('div');
    thinking.className = 'msg agent thinking';
    thinking.textContent = 'Thinking...';
    messages.appendChild(thinking);
    messages.scrollTop = messages.scrollHeight;

    try {
      const res = await fetch('/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt: text})
      });
      const data = await res.json();
      thinking.className = 'msg agent';
      thinking.textContent = extractSolution(data.result || data.detail || 'Error');
    } catch(e) {
      thinking.textContent = 'Error: ' + e.message;
    }
    send_btn.disabled = false;
    messages.scrollTop = messages.scrollHeight;
    prompt.focus();
  }

  function extractSolution(raw) {
    const m = raw.match(/<solution>([\\s\\S]*?)<\\/solution>/);
    return m ? m[1].trim() : raw.replace(/\\\\n/g, '\\n').replace(/^.*?===.*?===/gm, '').trim();
  }

  function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
</script>
</body>
</html>"""


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


@app.get("/stream")
async def stream_agent(prompt: str):
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    async def generate():
        for chunk in _agent.go_stream(prompt):
            yield f"data: {json.dumps({'chunk': str(chunk)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
