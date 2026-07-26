from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class MCPToolTarget:
    name: str
    endpoint: str
    read_only: bool = True
    timeout_sec: float = 10.0
    max_output_chars: int = 12000


class ControlledMCPClient:
    """Minimal MCP JSON-RPC client with an exact tool allowlist."""

    def __init__(self, targets: dict[str, MCPToolTarget]):
        self.targets = dict(targets)

    async def invoke(self, name: str, arguments: dict, context: dict) -> dict:
        target = self.targets.get(name)
        if target is None:
            raise PermissionError("MCP tool is not allowlisted")
        if not target.read_only:
            raise PermissionError("side-effecting MCP tools are disabled")
        tenant_id = str(context.get("tenant_id", "")).strip()
        if not tenant_id:
            raise PermissionError("tenant context is required")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": {
                    **arguments,
                    "_security_context": {"tenant_id": tenant_id},
                },
            },
        }
        async with httpx.AsyncClient(timeout=target.timeout_sec) as client:
            response = await client.post(target.endpoint, json=request)
            response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise RuntimeError("MCP tool returned an error")
        result = payload.get("result", {})
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded) > target.max_output_chars:
            return {
                "truncated": True,
                "output": encoded[: target.max_output_chars],
            }
        return result
