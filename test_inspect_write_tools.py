"""
Run this BEFORE approving any real proposal. It prints the actual
argument schema GitHub's MCP server reports for the three write tools
approval_store.py calls (create_branch, create_or_update_file,
create_pull_request), plus get_file_contents.

Why this matters: approval_store.py's field names (owner, repo, branch,
path, content, message, sha, base, head, title, body) were written from
the documented github-mcp-server conventions, but this couldn't be
verified against a live server from the sandbox this was built in (no
outbound access to api.githubcopilot.com there). If any printed field
name doesn't match what approval_store.py sends, edit the `arguments`
dicts in that file to match — the propose/read-only side of Phase 4 is
already confirmed working against your real server; only these three
write calls are unverified.

Run: PYTHONPATH=. python test_inspect_write_tools.py
"""
import asyncio
import json

from app.mcp.client import MCPToolkit

TOOLS_TO_CHECK = ["create_branch", "create_or_update_file", "create_pull_request", "get_file_contents"]


async def main():
    async with MCPToolkit() as toolkit:
        all_tools = await toolkit.discover_tools()
        by_name = {t["name"]: t for t in all_tools}

        for name in TOOLS_TO_CHECK:
            tool = by_name.get(name)
            if not tool:
                print(f"!! {name} not found on this server\n")
                continue
            print(f"=== {name} ===")
            print(json.dumps(tool["input_schema"], indent=2))
            print()


if __name__ == "__main__":
    asyncio.run(main())