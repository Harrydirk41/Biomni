"""AWS Lambda handler for the Biomni A1 agent.

The agent is initialised once per cold start and reused across warm invocations.
Expects the data lake to be available via an EFS mount at BIOMNI_DATA_PATH.
"""

import json
import os

from biomni.agent import A1

_agent: A1 = None


def _get_agent() -> A1:
    global _agent
    if _agent is None:
        _agent = A1(
            path=os.environ.get("BIOMNI_DATA_PATH", "/mnt/efs/biomni-data"),
            llm=os.environ.get("BIOMNI_LLM", "anthropic.claude-sonnet-4-5"),
            source=os.environ.get("LLM_SOURCE", "Bedrock"),
            commercial_mode=os.environ.get("BIOMNI_COMMERCIAL_MODE", "false").lower()
            == "true",
        )
    return _agent


def handler(event: dict, context) -> dict:
    """Main Lambda entry point. Accepts API Gateway proxy events."""
    try:
        if isinstance(event.get("body"), str):
            body = json.loads(event["body"])
        else:
            body = event.get("body") or event

        prompt = body.get("prompt", "").strip()
        if not prompt:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "prompt is required"}),
            }

        result = _get_agent().go(prompt)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"result": str(result)}),
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(exc)}),
        }
