"""
Full Phase 1 test: Knowledge Agent investigating issue #142 end to end,
using the mock GitHub MCP server + your real OpenRouter LLM.

Run: PYTHONPATH=. python test_agent.py
(after copying .env.example to .env and filling in OPENROUTER_API_KEY)
"""
import asyncio

from app.agents.knowledge_agent import investigate


async def main():
    report = await investigate("Investigate issue #142 and identify the root cause.")
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
