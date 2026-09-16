"""
Sanity check for the connection to GitHub's real, hosted MCP server.
No LLM/OpenRouter key needed for this — just GITHUB_PAT in .env.

Run: PYTHONPATH=. python test_mcp_connection.py

Note: this can't be verified from a sandboxed environment without
outbound access to api.githubcopilot.com — run it locally.
"""
import asyncio

from app.mcp.client import MCPToolkit


async def main():
    async with MCPToolkit() as toolkit:
        print("=== Connected to https://api.githubcopilot.com/mcp/ ===")
        print("=== Tool discovery ===")
        tools = await toolkit.discover_tools()
        print(f"{len(tools)} tools available:\n")
        for t in tools:
            print(f"  - {t['name']}: {t['description']}")

        print("\n=== LangChain tools generated from schema ===")
        lc_tools = await toolkit.as_langchain_tools()
        print(f"{len(lc_tools)} LangChain tools built successfully.")


if __name__ == "__main__":
    asyncio.run(main())
