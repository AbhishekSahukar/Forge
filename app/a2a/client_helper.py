"""
Shared A2A client logic. Every agent that needs to delegate to another
agent over A2A (Triage -> Knowledge/Engineering, Engineering -> Review)
uses this same function — one place that does discovery + message/send
+ result unpacking, rather than duplicating it per caller.
"""

import uuid

import httpx
from a2a.client import A2ACardResolver, A2AClient, create_text_message_object
from a2a.types import MessageSendParams, Role, SendMessageRequest, SendStreamingMessageRequest


async def send_a2a_task(agent_url: str, task_text: str, timeout: float = 600.0) -> str:
    """
    Discover the agent at agent_url, send it task_text over real A2A
    (JSON-RPC message/send), and return its response text. Raises
    RuntimeError with the agent's own error detail if it returns one.

    timeout defaults to 10 minutes, not a "normal" HTTP timeout —
    the receiving agent may run several LLM calls plus several MCP
    tool round-trips (GitHub API + LLM provider) in sequence before it
    has an answer. 180s was too tight in practice and produced
    A2AClientTimeoutError on real multi-step investigations, especially
    against slower/free-tier model endpoints.
    """
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        resolver = A2ACardResolver(httpx_client=http_client, base_url=agent_url)
        agent_card = await resolver.get_agent_card()

        client = A2AClient(httpx_client=http_client, agent_card=agent_card)

        message = create_text_message_object(role=Role.user, content=task_text)
        request = SendMessageRequest(id=str(uuid.uuid4()), params=MessageSendParams(message=message))
        response = await client.send_message(request)

        root = response.root
        if hasattr(root, "error") and root.error is not None:
            raise RuntimeError(f"{agent_card.name} returned an A2A error: {root.error}")

        result = root.result
        parts_text = []
        for part in getattr(result, "parts", []):
            root_part = getattr(part, "root", part)
            if hasattr(root_part, "text"):
                parts_text.append(root_part.text)
        return "\n".join(parts_text) if parts_text else str(result)


async def stream_a2a_task(agent_url: str, task_text: str, timeout: float = 600.0):
    """
    Streaming twin of send_a2a_task(). Discovers the agent, sends the
    task over message/stream, and yields one dict per event as it
    arrives:
      {"kind": "status", "state": "working", "text": "Calling ..."}
      {"kind": "status", "state": "failed",  "text": "<error>"}
      {"kind": "final",  "text": "<the completed task's final message>"}

    If the target agent doesn't advertise streaming support, this
    still works — the A2A client falls back transparently, but you'll
    only get a single final event instead of incremental ones. Callers
    shouldn't need to know which happened; the event shape is the same.
    """
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        resolver = A2ACardResolver(httpx_client=http_client, base_url=agent_url)
        agent_card = await resolver.get_agent_card()
        client = A2AClient(httpx_client=http_client, agent_card=agent_card)

        message = create_text_message_object(role=Role.user, content=task_text)
        request = SendStreamingMessageRequest(id=str(uuid.uuid4()), params=MessageSendParams(message=message))

        async for response in client.send_message_streaming(request):
            root = response.root
            if hasattr(root, "error") and root.error is not None:
                yield {"kind": "status", "state": "failed", "text": str(root.error)}
                return

            result = root.result
            result_type = type(result).__name__

            if result_type == "Task":
                # Initial task envelope (state=submitted) — nothing worth
                # showing the user yet, skip it.
                continue

            if result_type == "TaskStatusUpdateEvent":
                state = result.status.state.value if hasattr(result.status.state, "value") else str(result.status.state)
                text = ""
                if result.status.message:
                    parts = []
                    for part in result.status.message.parts:
                        p = getattr(part, "root", part)
                        if hasattr(p, "text"):
                            parts.append(p.text)
                    text = "\n".join(parts)

                if state == "completed":
                    yield {"kind": "final", "text": text}
                elif state == "failed":
                    yield {"kind": "status", "state": "failed", "text": text}
                    return
                else:
                    yield {"kind": "status", "state": state, "text": text}
                continue

            # Message result (non-streaming agent's single response, or
            # a streaming agent's terminal Message instead of a
            # completed-status Task) — treat as final.
            parts_text = []
            for part in getattr(result, "parts", []):
                p = getattr(part, "root", part)
                if hasattr(p, "text"):
                    parts_text.append(p.text)
            if parts_text:
                yield {"kind": "final", "text": "\n".join(parts_text)}