from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Callable

from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class AgentAction:
    tool: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentStep:
    action: AgentAction
    observation: dict


@dataclass(frozen=True)
class AgentResult:
    status: str
    answer: str = ""
    steps: list[AgentStep] = field(default_factory=list)


class ControlledAgent:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        planner: Callable,
        max_steps: int = 4,
    ):
        self.registry = registry
        self.planner = planner
        self.max_steps = max(1, max_steps)

    async def run(self, question: str, context: dict) -> AgentResult:
        steps: list[AgentStep] = []
        seen_calls: set[tuple[str, str]] = set()
        for _ in range(self.max_steps):
            state = {"question": question, "steps": steps}
            action = self.planner(state)
            if inspect.isawaitable(action):
                action = await action
            if action.tool == "finish":
                return AgentResult("completed", str(action.arguments.get("answer", "")), steps)
            signature = (action.tool, repr(sorted(action.arguments.items())))
            if signature in seen_calls:
                return AgentResult("repeated_call", steps=steps)
            seen_calls.add(signature)
            observation = await self.registry.invoke(action.tool, action.arguments, context)
            steps.append(AgentStep(action, observation))
        return AgentResult("budget_exhausted", steps=steps)

