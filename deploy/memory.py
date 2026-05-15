"""Session and long-term memory management for Biomni using DynamoDB."""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3

SESSIONS_TABLE = os.getenv("BIOMNI_SESSIONS_TABLE", "biomni-sessions")
MEMORY_TABLE = os.getenv("BIOMNI_MEMORY_TABLE", "biomni-memory")
REGION = os.getenv("AWS_REGION", "us-east-1")

# Max messages to include as conversation history in prompt
MAX_HISTORY_MESSAGES = 30
# Max chars per message when injecting into prompt
MAX_MSG_CHARS = 800


def _table(name: str):
    return boto3.resource("dynamodb", region_name=REGION).Table(name)


# ─────────────────────────────────────────────────────────────
# Session operations (Level 1 + 2)
# ─────────────────────────────────────────────────────────────

def create_session(title: str = "") -> str:
    session_id = str(uuid.uuid4())
    _table(SESSIONS_TABLE).put_item(Item={
        "session_id": session_id,
        "title": title or "New conversation",
        "messages": [],
        "created_at": _now(),
        "updated_at": _now(),
    })
    return session_id


def get_session(session_id: str) -> dict:
    resp = _table(SESSIONS_TABLE).get_item(Key={"session_id": session_id})
    return resp.get("Item", {})


def list_sessions(limit: int = 20) -> list:
    resp = _table(SESSIONS_TABLE).scan(
        ProjectionExpression="session_id, title, created_at, updated_at",
    )
    items = resp.get("Items", [])
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return items[:limit]


def add_message(session_id: str, role: str, content: str, title_hint: str = ""):
    updates = [
        "messages = list_append(if_not_exists(messages, :empty), :msg)",
        "updated_at = :now",
    ]
    values = {
        ":msg": [{"role": role, "content": content[:2000], "ts": _now()}],
        ":empty": [],
        ":now": _now(),
    }
    # Set title from first user message
    if role == "user" and title_hint:
        updates.append("title = if_not_exists(title, :title)")
        values[":title"] = title_hint[:80]

    _table(SESSIONS_TABLE).update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET " + ", ".join(updates),
        ExpressionAttributeValues=values,
    )


# ─────────────────────────────────────────────────────────────
# Long-term memory operations (Level 3)
# ─────────────────────────────────────────────────────────────

def get_memories(limit: int = 20) -> list:
    resp = _table(MEMORY_TABLE).scan()
    items = resp.get("Items", [])
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return items[:limit]


def store_memory(fact: str, session_id: str):
    _table(MEMORY_TABLE).put_item(Item={
        "memory_id": str(uuid.uuid4()),
        "session_id": session_id,
        "memory_text": fact[:500],
        "timestamp": _now(),
    })


def extract_and_store_memory(user_prompt: str, agent_response: str, session_id: str, llm) -> None:
    """Call LLM to extract key PKPD facts and store as long-term memory. Never raises."""
    extraction_prompt = (
        "From this PKPD conversation turn, extract any facts worth remembering in future sessions.\n"
        "Include: drug names, PK/PD parameters (CL, Vd, t½, IC50, etc.), dataset descriptions, "
        "model types, key findings, patient populations.\n"
        "If nothing important, reply exactly: NONE\n"
        "Otherwise write one concise bullet (max 80 words).\n\n"
        f"User: {user_prompt[:400]}\n"
        f"Agent: {agent_response[:800]}\n\n"
        "Key fact (or NONE):"
    )
    try:
        from langchain_core.messages import HumanMessage
        result = llm.invoke([HumanMessage(content=extraction_prompt)])
        fact = result.content.strip()
        if fact and fact.upper() != "NONE" and len(fact) > 10:
            store_memory(fact, session_id)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Prompt construction
# ─────────────────────────────────────────────────────────────

def build_prompt(current_prompt: str, session_id: Optional[str]) -> str:
    parts = []

    # Level 3: inject long-term memories
    memories = get_memories()
    if memories:
        lines = "\n".join(f"- {m['memory_text']}" for m in memories)
        parts.append(f"=== LONG-TERM MEMORY (facts from previous sessions) ===\n{lines}")

    # Level 1+2: inject conversation history
    if session_id:
        session = get_session(session_id)
        messages = session.get("messages", [])
        if messages:
            recent = messages[-MAX_HISTORY_MESSAGES:]
            history = []
            for msg in recent:
                role = "User" if msg["role"] == "user" else "Agent"
                content = msg["content"][:MAX_MSG_CHARS]
                if len(msg["content"]) > MAX_MSG_CHARS:
                    content += " [...]"
                history.append(f"{role}: {content}")
            parts.append("=== CONVERSATION HISTORY ===\n" + "\n\n".join(history))

    parts.append(f"=== CURRENT MESSAGE ===\n{current_prompt}")
    return "\n\n".join(parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
