"""
Phase 4/7 — Engineering Agent.

Safe Mode is enforced structurally, not by prompting: this agent is
only ever given READ tools from the MCP toolkit (get_file_contents,
search_code, get_issue, etc.). It cannot call create_branch,
create_or_update_file, or create_pull_request even if the LLM decides
to try — those tools are simply never in its toolset. It reasons about
a fix and emits a structured proposal; nothing touches GitHub until a
human approves it and app/core/approval_store.py executes the plan
with a separate, tool-explicit code path (see that file).

Phase 7 fix: same runaway-loop risk as the Knowledge Agent (see that
file's docstring for the real transcript that showed it) — a
recursion_limit and tighter prompt guidance keep proposal generation
from meandering through unbounded speculative tool calls. Unlike the
Knowledge Agent, there's no "graceful partial proposal" to fall back
to here — a proposal is either valid JSON that parses into a
ProposedChange or it isn't, so hitting the limit raises a clear error
instead of fabricating a fake plan.
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app.core.config import settings
from app.mcp.client import MCPToolkit

# Tools the Engineering Agent is allowed to see. Everything else the
# real GitHub MCP server exposes (create_branch, create_or_update_file,
# push_files, create_pull_request, merge_pull_request, delete_file,
# etc.) is deliberately withheld here — that's what makes this Safe
# Mode, not a config flag that could be forgotten.
READ_ONLY_TOOL_NAMES = {
    "get_file_contents",
    "search_code",
    "issue_read",
    "list_issues",
    "get_commit",
    "list_commits",
    "pull_request_read",
    "list_pull_requests",
    "search_issues",
    "search_repositories",
    "get_me",
}

# See knowledge_agent.py's MAX_STEPS comment — same rationale and
# same LangGraph internal-safety-margin quirk applies here.
MAX_STEPS = 20

SYSTEM_PROMPT = """You are the Engineering Agent in Forge, an AI software engineering team.

You have READ-ONLY tools to explore a GitHub repository (files, code
search, issues, commits, PRs). You do NOT have write access — you
cannot create branches, commit, or open pull requests directly. Your
job is to design a fix and describe it precisely enough that a
separate, deterministic step can apply it exactly as you specify, once
a human approves it.

Be decisive and efficient — you have a limited number of tool calls:
- If a code search returns zero results, do NOT retry with another
  guessed search term. Work from the files, commits, and PR diffs
  you've already read instead.
- Prefer reading the files most likely to need changing (referenced in
  the issue/task, or changed in the most recent relevant commit) over
  broad exploration of the whole repository.
- Once you have enough context to write a concrete fix, stop
  investigating and produce the proposal — do not keep exploring for
  more confirmation than you need.

Investigate what you need using your tools, then respond with ONLY a
JSON object in this exact shape (no prose before or after it):

{
  "branch_name": "fix/short-description",
  "base_branch": "main",
  "files": [
    {"path": "path/to/file.py", "content": "<FULL new file content>", "commit_message": "short message"}
  ],
  "pr_title": "Short PR title",
  "pr_body": "What changed and why, referencing the issue/root cause."
}

Rules:
- "content" must be the COMPLETE new contents of the file, not a diff.
- Only include files you are actually changing.
- Base your fix on what you actually read via tools — never invent file
  contents you haven't fetched.
- Output valid JSON only. No markdown code fences, no commentary.
"""


# See knowledge_agent.py — same LangGraph internal-safety-margin quirk:
# create_react_agent substitutes this exact sentence instead of raising
# GraphRecursionError when its own internal step budget (tighter than
# recursion_limit) runs low, so it has to be detected explicitly.
_LANGGRAPH_STEP_LIMIT_SENTINEL = "Sorry, need more steps to process this request."


def _step_limit_message() -> str:
    return (
        f"Reached the step limit ({MAX_STEPS}) while exploring the repository "
        "without producing a proposal. Try narrowing the request — e.g. name "
        "a specific file or area of the codebase to focus on."
    )


class ProposedFile(BaseModel):
    path: str
    content: str
    commit_message: str


class ProposedChange(BaseModel):
    branch_name: str
    base_branch: str = "main"
    files: list[ProposedFile]
    pr_title: str
    pr_body: str
    raw_task: str = Field(description="The original task this proposal was generated for")


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=0,
    )


def _build_agent(llm, tools):
    try:
        return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    except TypeError:
        try:
            return create_react_agent(llm, tools, state_modifier=SYSTEM_PROMPT)
        except TypeError:
            return create_react_agent(llm, tools, messages_modifier=SYSTEM_PROMPT)


def _extract_json(text: str) -> dict:
    """
    Find the proposal JSON in the model's response, even if it wrapped
    it in prose or code fences despite being told not to.

    This does real balanced-brace scanning rather than a naive
    r"\\{.*\\}" regex — the naive version broke on a real response that
    contained a stray brace in prose before the actual JSON (e.g.
    mentioning "summarize({})" in an explanation), since a greedy
    first-{-to-last-} match swallows everything in between, producing
    invalid JSON. This scans every '{' as a candidate start, tracks
    brace depth (ignoring braces inside string literals), and returns
    the first balanced, valid JSON object that actually looks like a
    proposal (has "branch_name" and "files") — so a coincidental valid
    but irrelevant `{}` earlier in the text doesn't win by accident.
    """
    if text.strip() == _LANGGRAPH_STEP_LIMIT_SENTINEL:
        raise ValueError(_step_limit_message())

    required_keys = ("branch_name", "files")
    for start in (i for i, c in enumerate(text) if c == "{"):
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break  # unbalanced/invalid at this start — try the next '{'
                    if isinstance(parsed, dict) and all(k in parsed for k in required_keys):
                        return parsed
                    break  # valid JSON, but not proposal-shaped — try the next '{'

    raise ValueError(f"Engineering Agent did not return a parseable proposal:\n{text}")


async def propose_fix(task: str, owner: str, repo: str) -> ProposedChange:
    """
    Run the Engineering Agent against a task like:
    "Fix the token expiry bug described in issue #142" and return a
    structured, unexecuted proposal.
    """
    async with MCPToolkit() as toolkit:
        all_tools = await toolkit.as_langchain_tools()
        read_only_tools = [t for t in all_tools if t.name in READ_ONLY_TOOL_NAMES]

        llm = _build_llm()
        agent = _build_agent(llm, read_only_tools)
        contextual_task = f"Repository: {owner}/{repo}\n\nTask: {task}"

        try:
            result = await agent.ainvoke(
                {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=contextual_task)]},
                config={"recursion_limit": MAX_STEPS},
            )
        except GraphRecursionError:
            raise ValueError(_step_limit_message())

        final_text = result["messages"][-1].content
        parsed = _extract_json(final_text)
        parsed["raw_task"] = task
        return ProposedChange(**parsed)


async def propose_fix_streaming(task: str, owner: str, repo: str):
    """
    Streaming twin of propose_fix() — yields progress events during the
    ReAct loop (tool calls/results), then a final event whose "text" is
    the raw JSON the LLM produced. The caller is responsible for parsing
    that final event's text with _extract_json + ProposedChange the same
    way propose_fix() does — this function only handles streaming the
    reasoning, not the parsing, so both stay in sync with one
    _extract_json implementation.

    Raises ValueError (same as propose_fix) if the step limit is hit —
    there's no "final" event to yield in that case since no valid
    proposal was produced; the caller's existing try/except around
    this generator already handles that.
    """
    from app.agents.streaming_utils import stream_react_agent

    async with MCPToolkit() as toolkit:
        all_tools = await toolkit.as_langchain_tools()
        read_only_tools = [t for t in all_tools if t.name in READ_ONLY_TOOL_NAMES]

        llm = _build_llm()
        agent = _build_agent(llm, read_only_tools)
        contextual_task = f"Repository: {owner}/{repo}\n\nTask: {task}"

        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=contextual_task)]
        try:
            async for event in stream_react_agent(agent, messages, config={"recursion_limit": MAX_STEPS}):
                yield event
        except GraphRecursionError:
            raise ValueError(_step_limit_message())