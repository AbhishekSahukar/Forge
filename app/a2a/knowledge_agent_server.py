"""
Phase 3/7 — Knowledge Agent, exposed as a real A2A server.

This runs as its OWN process on its own port. It's not imported and
called as a function anymore — the Triage Agent reaches it exclusively
over the A2A protocol (HTTP + JSON-RPC), the same way it would reach a
Knowledge Agent running on a different machine entirely. That's the
actual point of A2A: agents as independent services.

Phase 7 adds real streaming: capabilities.streaming=True, and the
executor uses TaskUpdater to emit a status event for every tool call
and tool result as the ReAct loop runs, not just one final message —
this is what a live AG-UI activity feed actually reads from.

Run standalone:
    python -m app.a2a.knowledge_agent_server
    -> serves on http://localhost:9001
    -> agent card discoverable at http://localhost:9001/.well-known/agent.json
"""

import uvicorn
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, TaskState
from a2a.utils import new_agent_text_message, new_task

from app.agents.knowledge_agent import investigate_streaming

KNOWLEDGE_AGENT_PORT = 9001
KNOWLEDGE_AGENT_URL = f"http://localhost:{KNOWLEDGE_AGENT_PORT}/"


class KnowledgeAgentExecutor(AgentExecutor):
    """
    Bridges the A2A protocol to the Knowledge Agent's streaming run.

    execute() pulls the delegated instruction out of the A2A message,
    then relays every event investigate_streaming() yields as a
    TaskUpdater status update (TaskState.working) in real time, and
    finishes with a single TaskState.completed carrying the final
    report. A client using send_message_streaming() sees each of these
    as they happen, not just the end result.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_description = context.get_user_input()
        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        final_text = None
        try:
            async for event in investigate_streaming(task_description):
                text = event["text"]
                if event["type"] == "final":
                    final_text = text
                    continue
                await updater.update_status(
                    TaskState.working,
                    message=new_agent_text_message(text, context_id=task.context_id, task_id=task.id),
                )

            if final_text is None:
                # The stream ended without a classified "final" event —
                # shouldn't normally happen, but don't leave the task
                # hanging if the model's last message didn't match the
                # expected shape.
                final_text = "Investigation finished, but no final report was produced. Check the streamed events above."

            await updater.complete(
                message=new_agent_text_message(final_text, context_id=task.context_id, task_id=task.id)
            )
        except Exception as exc:
            await updater.failed(
                message=new_agent_text_message(str(exc), context_id=task.context_id, task_id=task.id)
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Knowledge Agent tasks cannot be cancelled mid-run yet")


def build_agent_card() -> AgentCard:
    """
    The public description of this agent — what the Triage Agent (or
    any A2A-aware client) discovers at /.well-known/agent.json before
    ever sending it a task. This is the "agent discovery" and "agent
    capabilities" piece from the A2A spec.
    """
    return AgentCard(
        name="Knowledge Agent",
        description=(
            "Investigates GitHub issues and repositories: reads issues, "
            "comments, source files, commits, and pull requests to "
            "produce a structured root-cause investigation report."
        ),
        url=KNOWLEDGE_AGENT_URL,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="investigate_issue",
                name="Investigate GitHub Issue",
                description=(
                    "Given a natural-language task describing a GitHub issue "
                    "to investigate, reads the issue, related code, commits, "
                    "and PRs, and returns a structured root-cause report."
                ),
                tags=["github", "investigation", "root-cause-analysis"],
                examples=["Investigate issue #142 in octocat/hello-world"],
            )
        ],
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )


def build_app():
    request_handler = DefaultRequestHandler(
        agent_executor=KnowledgeAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AFastAPIApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )
    return a2a_app.build()


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=KNOWLEDGE_AGENT_PORT)