"""
Phase 5 — Review Agent, exposed as its own A2A server.

Same pattern as Knowledge/Engineering: independent process, own port,
own agent card. The Engineering Agent server calls this one over A2A
right after generating a proposal, and attaches the verdict to the
proposal before returning to Triage — the human reviewing a proposal
sees both the plan and an automated second opinion on it.

Run standalone:
    python -m app.a2a.review_agent_server
    -> serves on http://localhost:9003
"""

import uvicorn
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message

from app.agents.review_agent import review_change

REVIEW_AGENT_PORT = 9003
REVIEW_AGENT_URL = f"http://localhost:{REVIEW_AGENT_PORT}/"


class ReviewAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # The caller (Engineering Agent) sends the fully serialized
        # change text — see app/agents/review_agent.py's
        # format_change_for_review(). This agent doesn't know or care
        # who called it or how the change was produced.
        change_text = context.get_user_input()

        try:
            verdict = await review_change(change_text)
        except Exception as exc:
            await event_queue.enqueue_event(new_agent_text_message(f"Review failed: {exc}"))
            return

        await event_queue.enqueue_event(new_agent_text_message(verdict.model_dump_json()))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Review tasks cannot be cancelled mid-run yet")


def build_agent_card() -> AgentCard:
    return AgentCard(
        name="Review Agent",
        description=(
            "Independently reviews proposed code changes for whether they "
            "solve the stated task, code quality, regressions, test "
            "coverage, and security concerns. Returns APPROVED or "
            "CHANGES_REQUIRED with reasoning — advisory only, does not "
            "gate human approval."
        ),
        url=REVIEW_AGENT_URL,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="review_change",
                name="Review Proposed Change",
                description=(
                    "Given a serialized proposed change (task, branch, file "
                    "contents, PR description), returns a verdict with "
                    "reasoning and specific concerns."
                ),
                tags=["github", "code-review", "quality"],
                examples=["<serialized proposal text>"],
            )
        ],
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )


def build_app():
    request_handler = DefaultRequestHandler(
        agent_executor=ReviewAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AFastAPIApplication(agent_card=build_agent_card(), http_handler=request_handler)
    return a2a_app.build()


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=REVIEW_AGENT_PORT)