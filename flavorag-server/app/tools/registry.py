from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    handler: Callable[[dict, dict], Awaitable[dict]]
    read_only: bool
    timeout_sec: float = 10.0
    max_output_chars: int = 12000


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        handler: Callable[[dict, dict], Awaitable[dict]],
        *,
        read_only: bool,
        timeout_sec: float = 10.0,
        max_output_chars: int = 12000,
    ) -> None:
        if not name or name in self._tools:
            raise ValueError("tool name must be unique")
        self._tools[name] = RegisteredTool(
            name, handler, read_only, timeout_sec, max_output_chars
        )

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    async def invoke(self, name: str, arguments: dict, context: dict) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            raise PermissionError("tool is not allowlisted")
        if not tool.read_only:
            raise PermissionError("side-effecting tools are disabled")
        result = await asyncio.wait_for(
            tool.handler(dict(arguments), dict(context)),
            timeout=tool.timeout_sec,
        )
        serialized = json.dumps(result, ensure_ascii=False, default=str)
        if len(serialized) > tool.max_output_chars:
            return {
                "truncated": True,
                "output": serialized[: tool.max_output_chars],
            }
        return result
