"""Standalone LLM connection test — run before any other testing.

Usage:
    python -m llm.test_connection

Must print PASS before testing anything else.
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from llm.openai_client import call_openai


async def test() -> None:
    try:
        result = await call_openai(
            system_prompt="Return valid JSON only.",
            user_prompt='Return {"status": "ok", "message": "connected"}',
        )
        print("PASS — LLM connection test:", result)
    except Exception as e:
        print("FAIL — LLM connection test:", str(e))


if __name__ == "__main__":
    asyncio.run(test())
