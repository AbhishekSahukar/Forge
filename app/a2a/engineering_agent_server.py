"""
Phase 4/7 — Engineering Agent, exposed as its own A2A server.

Same pattern as the Knowledge Agent server: independent process, own
port, own agent card. What it returns over A2A is a proposal summary
plus a proposal_id — the actual GitHub write happens later, only after
a human calls the approve endpoint (see app/routers/agent.py), never
as a side effect of this A2A call.

Phase 7 adds streaming: every tool call/result during proposal
generation streams as a TaskUpdater status event, plus explicit status
lines around the Review Agent call ("Getting review agent's opinion...")
since that's a second network hop the person watching shouldn't be left
wondering about.

Run standalone:
    python -m app.a2a.engineering_agent_server
    -> serves on http://localhost:9002
"""

import json
import re

import uvicorn
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, TaskState
from a2a.utils import new_agent_text_message, new_task

from app.a2a.client_helper import send_a2a_task
from app.a2a.review_agent_server import REVIEW_AGENT_URL
from app.agents.engineering_agent import _extract_json, propose_fix_streaming
from app.agents.review_agent import format_change_for_review
from app.core.approval_store import set_review, store_proposal

ENGINEERING_AGENT_PORT = 9002
ENGINEERING_AGENT_URL = f"http://localhost:{ENGINEERING_AGENT_PORT}/"

# Expects tasks phrased like "... in owner/repo" — same convention
# already used in test_a2a.py / test_agent.py. Phase 6's structured
# task schema replaces this regex with an explicit field.
_REPO_PATTERN = re.compile(r"\bin\s+([\w.-]+)/([\w.-]+)")


class EngineeringAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_description = context.get_user_input()
        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        async def status(text: str):
            await updater.update_status(
                TaskState.working,
                message=new_agent_text_message(text, context_id=task.context_id, task_id=task.id),
            )

        match = _REPO_PATTERN.search(task_description)
        if not match:
            await updater.failed(
                message=new_agent_text_message(
                    'Could not determine target repository. Phrase the task as '
                    '"... in owner/repo", e.g. "Fix the bug in issue #142 in octocat/hello-world".',
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
            return
        owner, repo = match.group(1), match.group(2)

        # Stream the proposal-generation ReAct loop, capturing the final
        # raw JSON text the same way propose_fix() would.
        final_text = None
        try:
            async for event in propose_fix_streaming(task_description, owner=owner, repo=repo):
                if event["type"] == "final":
                    final_text = event["text"]
                    continue
                await status(event["text"])

            if final_text is None:
                raise ValueError("Engineering Agent's stream ended without a final proposal.")

            parsed = _extract_json(final_text)
            parsed["raw_task"] = task_description
            from app.agents.engineering_agent import ProposedChange

            change = ProposedChange(**parsed)
        except Exception as exc:
            await updater.failed(
                message=new_agent_text_message(
                    f"Failed to generate a proposal: {exc}", context_id=task.context_id, task_id=task.id
                )
            )
            return

        proposal = store_proposal(change, owner=owner, repo=repo)

        # Phase 5: get an automated second opinion from the Review Agent
        # before handing this to the human. Advisory only — if the
        # review call itself fails (agent not running, LLM hiccup), the
        # proposal still stands; it just shows as "not reviewed" rather
        # than blocking the human from deciding on their own.
        await status("Proposal drafted. Getting the Review Agent's opinion...")
        review_note = "Review Agent unavailable — proceed with manual review."
        try:
            change_text = format_change_for_review(
                raw_task=change.raw_task,
                branch_name=change.branch_name,
                files=[f.model_dump() for f in change.files],
                pr_title=change.pr_title,
                pr_body=change.pr_body,
            )
            review_response = await send_a2a_task(REVIEW_AGENT_URL, change_text)
            review_json = json.loads(review_response)
            verdict = review_json.get("verdict")
            reasoning = review_json.get("reasoning")
            concerns = review_json.get("concerns", [])
            # Persists to the shared SQLite store so main-api (a
            # different process) can see the review verdict too — see
            # approval_store.py's module docstring for why this can't
            # just be an in-memory attribute on `proposal` anymore.
            set_review(proposal.id, verdict, reasoning, concerns, review_response)
            review_note = f"Review Agent verdict: {verdict} — {reasoning}"
            await status(review_note)
        except Exception as exc:
            review_note = f"Review Agent call failed (proceeding without automated review): {exc}"
            await status(review_note)

        file_list = "\n".join(f"  - {f.path} ({f.commit_message})" for f in change.files)
        summary = (
            f"PROPOSAL {proposal.id} (status: {proposal.status.value})\n\n"
            f"Branch: {change.branch_name} (from {change.base_branch})\n"
            f"PR title: {change.pr_title}\n"
            f"Files changed:\n{file_list}\n\n"
            f"PR body:\n{change.pr_body}\n\n"
            f"{review_note}\n\n"
            f"This has NOT been written to GitHub. Approve it via:\n"
            f"  POST /agent/proposals/{proposal.id}/approve"
        )
        await updater.complete(
            message=new_agent_text_message(summary, context_id=task.context_id, task_id=task.id)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Engineering Agent proposal generation cannot be cancelled mid-run yet")


def build_agent_card() -> AgentCard:
    return AgentCard(
        name="Engineering Agent",
        description=(
            "Proposes code fixes for GitHub issues. Operates in Safe Mode: "
            "reads repository context and designs a fix, but never writes "
            "to GitHub directly — every proposal requires explicit human "
            "approval before a branch, commit, or PR is created."
        ),
        url=ENGINEERING_AGENT_URL,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="propose_fix",
                name="Propose Code Fix",
                description=(
                    "Given a task describing a bug or feature to implement, "
                    "reads relevant repository context and returns a structured, "
                    "unexecuted proposal (branch, file changes, PR description) "
                    "pending human approval."
                ),
                tags=["github", "code-generation", "safe-mode"],
                examples=["Fix the bug described in issue #142 in octocat/hello-world"],
            )
        ],
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )


def build_app():
    request_handler = DefaultRequestHandler(
        agent_executor=EngineeringAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AFastAPIApplication(agent_card=build_agent_card(), http_handler=request_handler)
    return a2a_app.build()


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=ENGINEERING_AGENT_PORT)