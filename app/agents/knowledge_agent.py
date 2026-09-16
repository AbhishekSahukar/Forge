"""
Phase 1/2/7 — Knowledge Agent.

Single agent, single MCP server, read-only tools.

Phase 7 fix: a real investigation against DreamCatcher showed the
agent doing 12+ rounds of tool calls, several of them blind guesses at
search_code queries that returned zero results, and never converging
on a final report — a genuinely unbounded ReAct loop, not a hang.
Two changes address this:
  1. MAX_STEPS caps the graph's recursion_limit so a run can't run
     forever — it fails fast (and gracefully, see below) instead of
     taking 10+ minutes of real API calls that don't lead anywhere.
  2. The system prompt now explicitly tells the model to stop
     guessing after a failed search and conclude with what it already
     has, rather than trying another speculative query.
If the step limit is hit anyway, that's caught and turned into an
honest "investigation incomplete" report using whatever was already
streamed, instead of a bare technical error.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from app.core.config import settings
from app.mcp.client import MCPToolkit

# Each LLM turn and each tool-call batch counts as one step, AND
# create_react_agent has its own internal safety margin (see the
# sentinel-detection code below) that triggers when only ~2 steps
# remain — so the effective usable budget is a bit less than this
# number. 12 was too tight in practice (a real multi-file DreamCatcher
# investigation hit the internal safety message after ~8 real rounds);
# 20 gives genuine multi-hop investigations real room while still
# bounding worst-case wait time to something finite.
MAX_STEPS = 20

SYSTEM_PROMPT = """You are the Knowledge Agent in Forge, an AI software engineering team.

Your job is to investigate a GitHub issue and produce a structured
investigation report. You have read-only tools to search the repo,
read files, inspect issues/comments, and inspect commits/PRs.

Always ground your findings in what the tools actually return — never
guess at file contents or issue details you haven't fetched. Work like
a careful engineer: read the issue, look at what it references (PRs,
commits, files), pull the relevant source, and trace the root cause.

Be decisive and efficient — you have a limited number of tool calls:
- If a code search returns zero results, do NOT retry with another
  guessed search term. Rely on the files, commits, and PR diffs you've
  already read instead.
- Prefer reading the files most likely to be relevant (referenced in
  the issue, or changed in the most recent commit/PR) over broad
  exploration of the whole repository.
- If you cannot pin down an exact root cause within a handful of tool
  calls, give your best-supported hypothesis rather than continuing to
  search — a strong hypothesis grounded in what you read is more
  useful than an exhaustive but unfinished investigation.

When you're done, respond with a structured report in this shape:

## Investigation Report

**Issue:** <number and title>
**Root cause:** <your finding, or best-supported hypothesis>
**Relevant file(s):** <paths>
**Relevant commit(s)/PR(s):** <ids>
**Suggested next step:** <what the Engineering Agent should do>

This is a one-shot report with no follow-up turn — end with the report
itself. Do not ask the user a question or offer to investigate further;
state your findings and your recommended next step as conclusions, not
as an offer.
"""


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=0,
    )


def _build_agent(llm, tools):
    # LangGraph has renamed this kwarg across versions
    # (prompt -> state_modifier -> messages_modifier). Try the
    # current name first and fall back for older installs.
    try:
        return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    except TypeError:
        try:
            return create_react_agent(llm, tools, state_modifier=SYSTEM_PROMPT)
        except TypeError:
            return create_react_agent(llm, tools, messages_modifier=SYSTEM_PROMPT)


async def investigate(task: str) -> str:
    """
    Run the Knowledge Agent on a task like "Investigate issue #142".

    MCP tool discovery + connection happens fresh per call (the toolkit
    is an async context manager) — fine for Phase 1's single-shot CLI
    testing. Phase 6+ will move this to a long-lived connection managed
    by the FastAPI app lifespan instead of reconnecting every call.
    """
    async with MCPToolkit() as toolkit:
        tools = await toolkit.as_langchain_tools()
        llm = _build_llm()
        agent = _build_agent(llm, tools)

        try:
            result = await agent.ainvoke(
                {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=task)]},
                config={"recursion_limit": MAX_STEPS},
            )
            return _final_text_or_fallback(result["messages"][-1].content, task)
        except GraphRecursionError:
            return _step_limit_report(task)


async def investigate_streaming(task: str):
    """
    Streaming twin of investigate() — yields progress events (tool
    calls, tool results, final answer) instead of returning one blob.
    Used by the A2A server's streaming executor for live AG-UI updates.

    On hitting MAX_STEPS, yields a graceful "final" event summarizing
    the situation instead of letting GraphRecursionError propagate as
    a raw technical failure — the caller (knowledge_agent_server.py)
    still sees a clean {"type": "final", ...} event either way.
    """
    from app.agents.streaming_utils import stream_react_agent

    async with MCPToolkit() as toolkit:
        tools = await toolkit.as_langchain_tools()
        llm = _build_llm()
        agent = _build_agent(llm, tools)

        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=task)]
        try:
            async for event in stream_react_agent(agent, messages, config={"recursion_limit": MAX_STEPS}):
                if event["type"] == "final":
                    yield {"type": "final", "text": _final_text_or_fallback(event["text"], task)}
                else:
                    yield event
        except GraphRecursionError:
            yield {"type": "final", "text": _step_limit_report(task)}


# LangGraph's create_react_agent has its OWN internal step-budget check
# (remaining_steps < 2), separate from and tighter than recursion_limit.
# When it trips, the graph returns a normal-looking final AI message
# with this exact canned content instead of raising GraphRecursionError
# — so the except block above never catches it, and without this check
# the person would just see LangGraph's generic sentence with no
# useful next step. Detected here and swapped for the same helpful
# message the hard-recursion-limit path already produces.
_LANGGRAPH_STEP_LIMIT_SENTINEL = "Sorry, need more steps to process this request."


def _final_text_or_fallback(text: str, task: str) -> str:
    if text.strip() == _LANGGRAPH_STEP_LIMIT_SENTINEL:
        return _step_limit_report(task)
    return text


def _step_limit_report(task: str) -> str:
    return (
        "## Investigation Report\n\n"
        f"**Issue:** {task}\n\n"
        f"**Root cause:** Investigation stopped after reaching its step limit "
        f"({MAX_STEPS} steps) without converging on a definitive answer.\n\n"
        "**Suggested next step:** Try narrowing the request — e.g. name a "
        "specific file or area of the codebase you suspect — so the agent "
        "has less ground to cover per investigation."
    )