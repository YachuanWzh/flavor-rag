from __future__ import annotations

import re
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class SQLPolicyError(ValueError):
    pass


_RELATION_PATTERN = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_MUTATION_PATTERN = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|CALL|EXECUTE)\b",
    re.IGNORECASE,
)


def validate_readonly_sql(sql: str, *, allowed_relations: set[str]) -> str:
    normalized = " ".join(sql.strip().split())
    if not normalized:
        raise SQLPolicyError("SQL is empty")
    if ";" in normalized or "--" in normalized or "/*" in normalized:
        raise SQLPolicyError("multiple statements and comments are not allowed")
    if not re.match(r"^(SELECT|WITH)\b", normalized, re.IGNORECASE):
        raise SQLPolicyError("only SELECT or WITH queries are allowed")
    if _MUTATION_PATTERN.search(normalized):
        raise SQLPolicyError("mutating SQL is not allowed")
    relations = {match.split(".")[-1] for match in _RELATION_PATTERN.findall(normalized)}
    if not relations or not relations.issubset(allowed_relations):
        raise SQLPolicyError("query references a non-allowlisted relation")
    if not re.search(r"\btenant_id\s*=\s*:tenant_id\b", normalized, re.IGNORECASE):
        raise SQLPolicyError("tenant_id predicate is required")
    return normalized


class ReadOnlySQLTool:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        allowed_relations: set[str],
        max_rows: int = 100,
    ):
        self.engine = engine
        self.allowed_relations = allowed_relations
        self.max_rows = max(1, min(max_rows, 1000))

    async def __call__(self, arguments: dict, context: dict) -> dict:
        tenant_id = str(context.get("tenant_id", "")).strip()
        if not tenant_id:
            raise PermissionError("tenant context is required")
        sql = validate_readonly_sql(
            str(arguments.get("sql", "")),
            allowed_relations=self.allowed_relations,
        )
        if not re.search(r"\bLIMIT\s+\d+\b", sql, re.IGNORECASE):
            sql = f"{sql} LIMIT {self.max_rows}"
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(sql),
                {"tenant_id": tenant_id},
            )
            rows = [dict(row) for row in result.mappings().fetchmany(self.max_rows)]
        return {"rows": rows, "row_count": len(rows)}
