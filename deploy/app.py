"""FastAPI wrapper for the Biomni A1 agent.

Used by ECS Fargate, ECS EC2, EKS, and Raw EC2 deployments.
Lambda uses lambda_handler.py instead.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from biomni.agent import A1

_agent: A1 = None


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


@app.post("/gradio")
async def launch_gradio():
    """Start Gradio UI in background thread (port 7860)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: _agent.launch_gradio_demo(
            server_name="0.0.0.0",
            server_port=7860,
            share=False,
        ),
    )
    return {"status": "gradio started", "port": 7860}
