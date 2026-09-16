"""
Phase 5 — Review Agent.

Reviews an Engineering Agent proposal BEFORE it's written to GitHub —
in our Safe Mode design, the branch/PR don't exist yet at review time
(that's a deliberate adaptation of the original spec, which imagined
reviewing a live PR; here there's nothing live to inspect until a
human approves, so this agent reviews the plan itself instead). It's
an automated second opinion presented alongside the proposal — the
human approving it still makes the final call, this never auto-blocks
an approval.

No MCP tools here on purpose: the Engineering Agent already gathered
and cited its context; Review's job is to critically read the proposed
diff and PR description, not re-fetch the repo from scratch.
"""

import json
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import settings

SYSTEM_PROMPT = """You are the Review Agent in Forge, an AI software engineering team.

You are given a proposed code change (not yet written to GitHub) — the
original task it's meant to solve, the branch name, the full new
contents of each changed file, and the pull request title/body the
Engineering Agent wrote.

Check:
- Does the change plausibly solve the described task/issue?
- Code quality: is it reasonable, idiomatic, free of obvious bugs?
- Potential regressions: could this break other things given what you
  can see?
- Test coverage: are there tests, or should there be and aren't?
- Security concerns: secrets, injection risks, unsafe patterns?
- Does the PR description accurately describe the actual change?

Respond with ONLY a JSON object in this exact shape (no prose outside it):

{
  "verdict": "APPROVED" or "CHANGES_REQUIRED",
  "reasoning": "2-4 sentences explaining the verdict",
  "concerns": ["specific concern 1", "specific concern 2"]
}

"concerns" may be an empty list if verdict is APPROVED. Output valid
JSON only — no markdown fences, no commentary before or after.
"""


class ReviewVerdict(BaseModel):
    verdict: Literal["APPROVED", "CHANGES_REQUIRED"]
    reasoning: str
    concerns: list[str] = []


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
        raise ValueError(f"Review Agent did not return parseable JSON:\n{text}")
    return json.loads(match.group(0))


def format_change_for_review(
    raw_task: str, branch_name: str, files: list[dict], pr_title: str, pr_body: str
) -> str:
    """Serializes a proposed change into the text block the Review Agent reads."""
    files_block = "\n\n".join(
        f"--- {f['path']} ---\ncommit message: {f['commit_message']}\n{f['content']}"
        for f in files
    )
    return (
        f"Original task: {raw_task}\n\n"
        f"Proposed branch: {branch_name}\n\n"
        f"PR title: {pr_title}\n"
        f"PR body: {pr_body}\n\n"
        f"Changed files:\n{files_block}"
    )


async def review_change(change_text: str) -> ReviewVerdict:
    """
    Pure LLM review — no tools, no A2A here (this module is called BY
    the Review Agent's A2A executor, not the other way around).
    """
    llm = _build_llm()
    result = await llm.ainvoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=change_text)]
    )
    parsed = _extract_json(result.content)
    return ReviewVerdict(**parsed)