from __future__ import annotations

import json
import re

from app.agent.controlled import AgentAction
from app.config.logging_config import get_logger
from app.llm.client import MockLLMClient, get_llm_client

_planner_log = get_logger("flavorag.agent.planner")

_SYSTEM_PROMPT = """\
You are a retrieval planning agent. Your job is to decide the NEXT best action \
to gather sufficient evidence for answering the user's question.

## Decision Process (think step by step)
1. **Diagnose**: Why did the previous retrieval fail or fall short?
   - "insufficient_relevance" → the query was too broad or used wrong terms → refine query
   - source_count == 0 → try a completely different angle or decompose the question
   - low max_score (< 0.3) → the knowledge base may lack this info → consider finish
   - channels timeout/error → retry may help (transient failure)
2. **Strategy**: Choose ONE action:
   - `retrieve` with a REFINED query (shorter, more specific, different keywords)
   - `sql` if the question needs structured data and previous retrieval was weak
   - `finish` if evidence is sufficient OR the knowledge base clearly lacks the answer
3. **Avoid**: Never repeat an earlier call. Never invent tools outside the allowlist.

## Output Format
Return a single JSON object (no markdown fences):
{"reasoning": "<one sentence explaining your diagnosis>", "tool": "<name|finish>", "arguments": {...}}

## Examples

Example 1 — retrieval rejected, refine query:
Input: question="K8s Pod CrashLoopBackOff 怎么排查", observations=[{"tool":"retrieve","observation":{"answerable":false,"rejection_reason":"insufficient_relevance","source_count":0,"max_score":0.12}}]
Output: {"reasoning":"Query too broad, narrowing to specific troubleshooting steps","tool":"retrieve","arguments":{"query":"Kubernetes Pod CrashLoopBackOff 排查步骤 logs"}}

Example 2 — evidence sufficient, finish:
Input: question="如何配置 Ingress", observations=[{"tool":"retrieve","observation":{"answerable":true,"source_count":4,"max_score":0.87}}]
Output: {"reasoning":"High-confidence sources found, evidence is sufficient","tool":"finish","arguments":{"answer":"evidence_ready"}}

Example 3 — low scores after retry, give up gracefully:
Input: question="公司2024年Q3营收", observations=[{"tool":"retrieve","observation":{"answerable":false,"rejection_reason":"insufficient_relevance","max_score":0.15}},{"tool":"retrieve","observation":{"answerable":false,"rejection_reason":"insufficient_relevance","max_score":0.11}}]
Output: {"reasoning":"Two attempts yielded very low scores, KB likely lacks financial data","tool":"finish","arguments":{"answer":"knowledge_gap"}}

Example 4 — use SQL tool for structured data:
Input: question="最近一周新增了多少用户", observations=[{"tool":"retrieve","observation":{"answerable":false,"rejection_reason":"insufficient_relevance","source_count":0,"max_score":0.08}}]
Output: {"reasoning":"Question asks for aggregate data, better suited for SQL query","tool":"sql","arguments":{"sql":"SELECT COUNT(*) as cnt FROM users WHERE tenant_id = :tenant_id AND created_at > NOW() - INTERVAL '7 days'"}}
"""


def _build_user_message(question: str, observations: list[dict]) -> str:
    """Build a compact user message with question and observation history."""
    # Slim down observations: remove verbose channel details for planner context
    slim_obs = []
    for obs in observations:
        entry = {"tool": obs["tool"]}
        observation = obs.get("observation", {})
        entry["observation"] = {
            k: observation[k]
            for k in (
                "query", "answerable", "source_count", "rejection_reason",
                "max_score", "avg_score", "subqueries_used",
            )
            if k in observation
        }
        # Include channel errors only (not full status)
        channels = observation.get("channels", {})
        if channels:
            errors = {
                name: status.get("error")
                for name, status in channels.items()
                if isinstance(status, dict) and status.get("error")
            }
            if errors:
                entry["observation"]["channel_errors"] = errors
        slim_obs.append(entry)
    return json.dumps(
        {"question": question, "observations": slim_obs},
        ensure_ascii=False,
    )


async def plan_next_action(
    *,
    question: str,
    steps: list,
    allowed_tools: list[str],
) -> AgentAction:
    """Ask the configured model for one structured, allowlisted next action.

    Uses Chain-of-Thought prompting with few-shot examples for better
    decision quality. Includes one retry on parse failure.
    """
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
    system_content = _SYSTEM_PROMPT + f"\nAllowed tools: {allowed_tools}. Never invent another tool."
    user_content = _build_user_message(question, observations)

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    # Attempt up to 2 times (retry with stricter format hint on failure)
    for attempt in range(2):
        action = await _call_llm_and_parse(client, messages, allowed_tools)
        if action is not None:
            return action
        if attempt == 0:
            _planner_log.warning("planner_parse_retry", question=question[:60])
            messages.append({"role": "assistant", "content": "I need to return valid JSON."})
            messages.append({
                "role": "user",
                "content": (
                    "Return ONLY a JSON object like: "
                    '{"reasoning":"...","tool":"finish","arguments":{"answer":"..."}}'
                ),
            })

    _planner_log.warning("planner_parse_failed_final", question=question[:60])
    return AgentAction("finish", {"answer": "planner_parse_failed"})


async def _call_llm_and_parse(
    client,
    messages: list[dict],
    allowed_tools: list[str],
) -> AgentAction | None:
    """Call LLM and attempt to parse a valid AgentAction. Returns None on failure."""
    tokens: list[str] = []
    async for token in client.chat_stream(messages, temperature=0):
        if not token.startswith("__THINK__"):
            tokens.append(token)
    raw = "".join(tokens).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    tool = str(payload.get("tool", "finish"))
    if tool != "finish" and tool not in allowed_tools:
        _planner_log.warning("planner_tool_rejected", tool=tool, allowed=allowed_tools)
        return AgentAction("finish", {"answer": "planner_tool_rejected"})
    arguments = payload.get("arguments")
    reasoning = str(payload.get("reasoning", ""))[:200]
    if reasoning:
        _planner_log.info("planner_decision", tool=tool, reasoning=reasoning)
    return AgentAction(tool, arguments if isinstance(arguments, dict) else {})
