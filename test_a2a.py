"""
Phase 3 test: real A2A delegation from Triage Agent to Knowledge Agent.

Needs the Knowledge Agent A2A server running in a SEPARATE terminal:
    PYTHONPATH=. python -m app.a2a.knowledge_agent_server

Then, in this terminal:
    PYTHONPATH=. python test_a2a.py

This exercises: agent discovery (/.well-known/agent.json), a real
JSON-RPC message/send call, and — if GITHUB_PAT/OPENROUTER_API_KEY are
set — a full investigation delegated entirely over A2A.
"""
import asyncio

from app.agents.triage_agent import handle_request


async def main():
    result = await handle_request(
        "Investigate issue #14 in AbhishekSahukar/DreamCatcher and identify the root cause."
    )
    print(f"Triage decision: {result['triage_decision']}\n")
    print(result["report"])


if __name__ == "__main__":
    asyncio.run(main())