"""
Phase 3/4 — Triage Agent.

This is the entry point for the whole Forge system and Forge's A2A
CLIENT. It does not call specialist agents as Python functions — it
discovers each one as an independent service (agent card at
/.well-known/agent.json) and sends it a task over the A2A protocol,
exactly as it would if that agent were running on a different machine,
written in a different language, or owned by a different team.

Phase 4 adds real branching: Triage now distinguishes "go investigate"
requests (-> Knowledge Agent) from "go implement/fix" requests
(-> Engineering Agent) using a keyword heuristic. That's intentionally
simple for now — swapping it for an LLM classification call later is a
small, contained change, not an architecture change, since both paths
already go through the same _delegate_to() function.
"""

from app.a2a.client_helper import send_a2a_task, stream_a2a_task
from app.a2a.engineering_agent_server import ENGINEERING_AGENT_URL
from app.a2a.knowledge_agent_server import KNOWLEDGE_AGENT_URL

# Simple, deliberately transparent routing for Phase 4. Anything that
# reads like "fix / implement / resolve / patch" goes to Engineering;
# everything else defaults to Knowledge (investigate-only).
_IMPLEMENT_KEYWORDS = ("fix", "implement", "resolve", "patch", "create a pr", "open a pr")


def _classify(user_request: str) -> str:
    lowered = user_request.lower()
    if any(keyword in lowered for keyword in _IMPLEMENT_KEYWORDS):
        return "engineering"
    return "knowledge"


async def handle_request(user_request: str) -> dict:
    """
    Entry point Forge's API calls. Classifies the request, delegates
    to the appropriate specialist agent over A2A, and returns both the
    routing decision and the result so the caller can see why Triage
    chose the path it did.
    """
    decision = _classify(user_request)
    agent_url = ENGINEERING_AGENT_URL if decision == "engineering" else KNOWLEDGE_AGENT_URL

    report = await send_a2a_task(agent_url, user_request)
    return {
        "triage_decision": f"delegated to {'Engineering' if decision == 'engineering' else 'Knowledge'} Agent",
        "report": report,
    }


async def handle_request_streaming(user_request: str):
    """
    Streaming twin of handle_request(). Yields the routing decision
    first, then relays every event from the chosen agent's stream as
    it happens, ending with the final report. Used by the
    /agent/triage/stream SSE endpoint for live AG-UI updates.
    """
    decision = _classify(user_request)
    agent_name = "Engineering" if decision == "engineering" else "Knowledge"
    agent_url = ENGINEERING_AGENT_URL if decision == "engineering" else KNOWLEDGE_AGENT_URL

    yield {"kind": "status", "state": "routing", "text": f"Routing to {agent_name} Agent..."}

    async for event in stream_a2a_task(agent_url, user_request):
        yield event