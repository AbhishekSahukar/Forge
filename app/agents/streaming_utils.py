"""
Turns a LangGraph ReAct agent's execution into a stream of typed events
instead of one final blob. This is what makes real-time AG-UI possible —
without this, the A2A layer has nothing to stream, no matter how the
protocol is wired up.

Uses agent.astream(..., stream_mode="updates"), which yields one dict
per graph step: {"agent": {...}} when the LLM produces a message (a
tool call, or the final answer), {"tools": {...}} when a tool result
comes back. This function turns those into a flat stream of:

  {"type": "tool_call",   "text": "Calling get_issue(issue_number=14)"}
  {"type": "tool_result", "text": "Result: {...}"}
  {"type": "final",       "text": "<the agent's final answer>"}

Caveat: this parses langgraph's documented stream_mode="updates" shape,
but wasn't exercised against a real model response in the environment
this was built in (no LLM key available there) — the tool-call/
tool-result/final classification logic is straightforward and the
message attributes it reads (tool_calls, content, message type) are
stable LangChain message API, but if the live output doesn't match one
of these three shapes, it silently falls through rather than throwing.
If streamed output looks wrong or empty in practice, that's the first
place to look.
"""

import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any


async def stream_react_agent(agent, messages: Sequence, config: dict | None = None) -> AsyncGenerator[dict, None]:
    async for step in agent.astream({"messages": messages}, config=config, stream_mode="updates"):
        for _node_name, node_output in step.items():
            if not isinstance(node_output, dict):
                continue
            msgs = node_output.get("messages", [])
            for msg in msgs:
                event = _classify_message(msg)
                if event:
                    yield event


def _classify_message(msg: Any) -> dict | None:
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        parts = []
        for tc in tool_calls:
            name = tc.get("name", "unknown_tool") if isinstance(tc, dict) else getattr(tc, "name", "unknown_tool")
            args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            parts.append(f"{name}({json.dumps(args)})")
        return {"type": "tool_call", "text": "Calling " + ", ".join(parts)}

    msg_type = getattr(msg, "type", None) or msg.__class__.__name__.lower()
    if "tool" in msg_type:
        content = str(getattr(msg, "content", ""))
        preview = content[:400] + ("..." if len(content) > 400 else "")
        return {"type": "tool_result", "text": f"Result: {preview}"}

    content = getattr(msg, "content", None)
    if content and "ai" in msg_type:
        # An AI message with content and no tool_calls is the final answer
        return {"type": "final", "text": content}

    return None