from __future__ import annotations

import json
import re

from app.agent.controlled import AgentAction
from app.llm.client import MockLLMClient, get_llm_client


async def plan_next_action(
    *,
    question: str,
    steps: list,
    allowed_tools: list[str],
) -> AgentAction:
    """Ask the configured model for one structured, allowlisted next action."""
    client = get_llm_client()
    if isinstance(client, MockLLMClient):
        return AgentAction("finish", {"answer": "no_additional_tool"})
    observations = [
        {
            "tool": step.action.tool,
            "observation": step.observation,
        }
        for step in steps[-3:]
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "Choose one read-only tool action that can improve the evidence. "
                "You may call retrieve again with a shorter or more precise query. "
                "Do not repeat an earlier call. Return JSON only: "
                '{"tool":"name|finish","arguments":{}}. '
                f"Allowed tools: {allowed_tools}. Never invent another tool."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"question": question, "observations": observations},
                ensure_ascii=False,
            ),
        },
    ]
    tokens: list[str] = []
    async for token in client.chat_stream(messages, temperature=0):
        if not token.startswith("__THINK__"):
            tokens.append(token)
    raw = "".join(tokens).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return AgentAction("finish", {"answer": "planner_parse_failed"})
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return AgentAction("finish", {"answer": "planner_parse_failed"})
    tool = str(payload.get("tool", "finish"))
    if tool != "finish" and tool not in allowed_tools:
        return AgentAction("finish", {"answer": "planner_tool_rejected"})
    arguments = payload.get("arguments")
    return AgentAction(tool, arguments if isinstance(arguments, dict) else {})
