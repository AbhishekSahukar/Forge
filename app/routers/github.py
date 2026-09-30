"""
v2 — GitHub browsing endpoints.

These back the frontend's account -> repo -> issue picker. Deliberately
NOT agentic: no LLM, no LangGraph, just a direct MCP tool call per
request, parsed into a shape the frontend can render. This is the
right layer for these — picking a repo from a list isn't a reasoning
task, so there's no reason to pay for an LLM call to do it.

IMPORTANT — verify before trusting blindly:
search_repositories is being used with a "user:{owner}" query qualifier
to approximate "list repos for this account", since the discovered
GitHub MCP tool list didn't include an explicit list-repos-for-user
tool. This wasn't verified against a live server in the environment
this was built in (no GitHub PAT there). If it returns nothing or
errors for a real account, the fix is almost certainly just the query
syntax (GitHub's search API uses "user:X" for personal accounts and
"org:X" for organizations — a real user might need to try both, or the
endpoint below could be extended to try org: as a fallback if user:
returns empty).
"""

import json

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.mcp.client import MCPToolkit

router = APIRouter(prefix="/github", tags=["github"])


async def _call_and_parse(tool_name: str, arguments: dict) -> dict | list:
    async with MCPToolkit() as toolkit:
        result = await toolkit._call_tool(tool_name, arguments)
    try:
        return json.loads(result)
    except ValueError:
        raise HTTPException(status_code=502, detail=f"Unexpected response from {tool_name}: {result[:300]}")


@router.get("/search-accounts")
async def search_accounts(q: str, user=Depends(get_current_user)):
    """Search GitHub users/orgs by name, for the first picker step."""
    if not q.strip():
        return []
    data = await _call_and_parse("search_users", {"query": q})
    items = data.get("items", data if isinstance(data, list) else [])
    return [
        {
            "login": item.get("login"),
            "avatar_url": item.get("avatar_url"),
            "html_url": item.get("html_url"),
        }
        for item in items[:10]
    ]


@router.get("/{owner}/repos")
async def list_repos(owner: str, user=Depends(get_current_user)):
    """List repos for the selected account, for the second picker step."""
    data = await _call_and_parse("search_repositories", {"query": f"user:{owner}"})
    items = data.get("repositories", data.get("items", []))
    return [
        {
            "name": item.get("name"),
            "full_name": item.get("full_name"),
            "description": item.get("description"),
            "private": item.get("private", False),
        }
        for item in items
    ]


@router.get("/{owner}/{repo}/issues")
async def list_issues(owner: str, repo: str, user=Depends(get_current_user)):
    """List open issues for the selected repo, for the third picker step."""
    data = await _call_and_parse("list_issues", {"owner": owner, "repo": repo, "state": "open"})
    items = data if isinstance(data, list) else data.get("issues", data.get("items", []))
    return [
        {
            "number": item.get("number"),
            "title": item.get("title"),
            "state": item.get("state"),
            "html_url": item.get("html_url"),
        }
        for item in items
    ]