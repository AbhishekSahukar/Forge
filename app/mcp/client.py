"""
MCP client wrapper — connects to GitHub's real, hosted MCP server.

This talks to https://api.githubcopilot.com/mcp/ over the streamable
HTTP transport (GitHub's remote MCP endpoint), authenticated with a
GitHub Personal Access Token. There is no local server process to spawn
here — GitHub runs the server, this client just connects to it.

Tool discovery still works exactly the same way it would for a local
stdio server: call list_tools(), build LangChain tools from whatever
schemas come back. Nothing in agents/knowledge_agent.py needs to know
or care that the transport changed from stdio to HTTP.
"""

import json
from contextlib import AsyncExitStack
from typing import Any

from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, Field, create_model

from app.core.config import settings

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _schema_to_pydantic_model(tool_name: str, input_schema: dict) -> type[BaseModel]:
    """Turn an MCP tool's JSON schema into a pydantic model for LangChain."""
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    fields: dict[str, Any] = {}

    for field_name, field_schema in properties.items():
        py_type = _JSON_TYPE_MAP.get(field_schema.get("type"), str)
        description = field_schema.get("description", "")
        if field_name in required:
            fields[field_name] = (py_type, Field(description=description))
        else:
            fields[field_name] = (py_type | None, Field(default=None, description=description))

    return create_model(f"{tool_name}_input", **fields)


class MCPToolkit:
    """
    Discovers tools from the remote GitHub MCP server and exposes them
    as LangChain tools.

    Usage:
        async with MCPToolkit() as toolkit:
            tools = await toolkit.as_langchain_tools()
            agent = create_react_agent(llm, tools)
    """

    def __init__(self, url: str = GITHUB_MCP_URL, github_pat: str | None = None):
        self._url = url
        self._pat = github_pat or settings.github_pat
        if not self._pat:
            raise ValueError(
                "GITHUB_PAT is not set. Add it to your .env file — "
                "generate one at https://github.com/settings/tokens "
                "(fine-grained token, read-only repo scopes are enough for Phase 1)."
            )
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPToolkit":
        headers = {"Authorization": f"Bearer {self._pat}"}
        read, write, _get_session_id = await self._stack.enter_async_context(
            streamablehttp_client(self._url, headers=headers)
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc_info):
        await self._stack.aclose()

    async def discover_tools(self) -> list[dict]:
        """Real MCP tool discovery: ask the server what it can do."""
        result = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in result.tools
        ]

    async def _call_tool(self, name: str, arguments: dict) -> str:
        result = await self._session.call_tool(name, arguments=arguments)
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else json.dumps({"result": "empty"})

    async def as_langchain_tools(self) -> list[StructuredTool]:
        """
        Build LangChain StructuredTools dynamically from MCP tool schemas.
        The real GitHub MCP server exposes 40+ tools (issues, PRs, code
        search, actions, etc.) — none of them are hand-written here, they
        come straight from the server's own schema.
        """
        mcp_tools = await self.discover_tools()
        langchain_tools = []

        for tool_info in mcp_tools:
            args_model = _schema_to_pydantic_model(tool_info["name"], tool_info["input_schema"])

            def make_coroutine(tool_name: str):
                async def _run(**kwargs) -> str:
                    clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
                    return await self._call_tool(tool_name, clean_kwargs)
                return _run

            langchain_tools.append(
                StructuredTool(
                    name=tool_info["name"],
                    description=tool_info["description"] or tool_info["name"],
                    args_schema=args_model,
                    coroutine=make_coroutine(tool_info["name"]),
                )
            )

        return langchain_tools
