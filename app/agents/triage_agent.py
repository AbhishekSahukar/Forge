"""
Phase 3/4/v2 — Triage Agent.

This is the entry point for the whole Forge system and Forge's A2A
CLIENT. It does not call specialist agents as Python functions — it
discovers each one as an independent service (agent card at
/.well-known/agent.json) and sends it a task over the A2A protocol.

v2 change: routing is now decided by an LLM reading the REAL, LIVE
agent cards discovered via A2A — not a hardcoded keyword list. Adding
a third agent later (or changing what Engineering's skill description
says it can do) requires zero changes here: Triage re-discovers both
cards on every request and routes based on what they currently
advertise. This is what AgentSkill descriptions in the A2A spec are
actually for — Forge just wasn't using them for anything before.

Trade-off worth knowing: this adds one extra LLM call plus two agent
card fetches before delegation even starts (maybe 3-8 seconds), on
top of whatever the chosen agent itself takes. The old keyword version
was instant; this version is slower but no longer needs a code change
every time you want to add or describe a new agent.
"""

import json
import re

import httpx
from a2a.client import A2ACardResolver
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.a2a.client_helper import send_a2a_task, stream_a2a_task
from app.a2a.engineering_agent_server import ENGINEERING_AGENT_URL
from app.a2a.knowledge_agent_server import KNOWLEDGE_AGENT_URL
from app.core.config import settings

AGENT_REGISTRY = {
    "knowledge": KNOWLEDGE_AGENT_URL,
    "engineering": ENGINEERING_AGENT_URL,
}

SYSTEM_PROMPT = """You are the routing brain for Forge's Triage Agent.

You are given a user's request and the REAL, currently-live capability
descriptions of the available specialist agents (discovered just now
via A2A, not hardcoded). Decide which ONE agent should handle this
request, based only on what each agent's own description and skills
say it can actually do.

Respond with ONLY a JSON object, no other text:
{"agent": "<the exact key of the agent you picked>", "reasoning": "<one short sentence>"}
"""


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=0,
    )


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Triage routing LLM did not return parseable JSON:\n{text}")
    return json.loads(match.group(0))


async def _discover_agents() -> dict[str, dict]:
    """
    Fetches every registered agent's REAL card via A2A discovery —
    name, description, and skills, straight from /.well-known/agent.json.
    This is the entire reason routing can be LLM-driven instead of
    keyword-matched: the LLM decides based on what agents actually say
    about themselves right now, not a list a human wrote once and
    forgot to update.
    """
    agents = {}
    async with httpx.AsyncClient(timeout=15.0) as http_client:
        for key, url in AGENT_REGISTRY.items():
            resolver = A2ACardResolver(httpx_client=http_client, base_url=url)
            card = await resolver.get_agent_card()
            skills_text = "; ".join(f"{s.name} — {s.description}" for s in card.skills)
            agents[key] = {
                "url": url,
                "name": card.name,
                "description": card.description,
                "skills": skills_text,
            }
    return agents


async def _classify_with_llm(user_request: str) -> tuple[str, str, dict[str, dict]]:
    """
    Returns (agent_key, agent_url, discovered_agents) so callers can
    report which agent was picked and why, using live-fetched names
    rather than hardcoded labels.
    """
    agents = await _discover_agents()

    agents_description = "\n\n".join(
        f'Key: "{key}"\nName: {info["name"]}\nDescription: {info["description"]}\nSkills: {info["skills"]}'
        for key, info in agents.items()
    )
    prompt = f"{SYSTEM_PROMPT}\n\nAvailable agents (discovered live via A2A):\n\n{agents_description}"

    llm = _build_llm()
    result = await llm.ainvoke([SystemMessage(content=prompt), HumanMessage(content=user_request)])

    try:
        parsed = _extract_json(result.content)
        agent_key = parsed.get("agent")
        reasoning = parsed.get("reasoning", "")
    except (ValueError, json.JSONDecodeError):
        agent_key, reasoning = None, ""

    if agent_key not in agents:
        # Fail safe, not silent: if the LLM's routing call is garbled or
        # names an agent that doesn't exist, default to Knowledge — the
        # read-only agent — rather than risking an unintended route into
        # Engineering's proposal-generation path on a parsing failure.
        agent_key, reasoning = "knowledge", "Fallback: routing response was unclear, defaulted to read-only agent"

    return agent_key, agents[agent_key]["url"], agents


async def handle_request(user_request: str) -> dict:
    """
    Entry point Forge's API calls. Classifies the request via LLM +
    live agent discovery, delegates over A2A, and returns both the
    routing decision and the result.
    """
    agent_key, agent_url, agents = await _classify_with_llm(user_request)
    report = await send_a2a_task(agent_url, user_request)
    return {
        "triage_decision": f"delegated to {agents[agent_key]['name']} (LLM-routed)",
        "report": report,
    }


async def handle_request_streaming(user_request: str):
    """
    Streaming twin of handle_request() for the SSE endpoint. Yields a
    routing status first (discovery + LLM classification happen here,
    which is the added latency vs. the old keyword version), then
    relays every event from the chosen agent's own stream.
    """
    yield {"kind": "status", "state": "routing", "text": "Discovering agents and choosing a route..."}

    agent_key, agent_url, agents = await _classify_with_llm(user_request)
    yield {
        "kind": "status",
        "state": "routing",
        "text": f"Routed to {agents[agent_key]['name']}",
    }

    async for event in stream_a2a_task(agent_url, user_request):
        yield event