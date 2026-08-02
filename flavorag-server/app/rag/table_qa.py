"""Table QA — detect table-oriented queries and extract precise cell answers.

Works on structured table dicts extracted during ingestion:
    {"headers": [...], "rows": [[...], ...], "source_page": N}
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TableAnswer:
    value: str
    aggregation: str  # "lookup" | "sum" | "max" | "min" | "avg" | "count"
    column: str
    source_table_index: int = 0


# Query patterns that indicate a table-oriented question
_TABLE_QUERY_PATTERNS = (
    re.compile(r"(多少|几[个条名位人])"),
    re.compile(r"(第.+[行列表])"),
    re.compile(r"(总计|总和|合计|总数)"),
    re.compile(r"(最大|最小|最高|最低|最多|最少)"),
    re.compile(r"(平均|均值)"),
    re.compile(r"(哪[个一].*[最少多高低大小])"),
)

# Aggregation keywords → operation
_AGG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(总[计额数和]?|合计|一共多少)"), "sum"),
    (re.compile(r"(最大|最高|最多)"), "max"),
    (re.compile(r"(最小|最低|最少)"), "min"),
    (re.compile(r"(平均|均值)"), "avg"),
    (re.compile(r"(多少[条个名位人]|一共[有几]多少|数量)"), "count"),
]

# Generic "how many" that maps to count when no numeric column is targeted
_COUNT_PATTERN = re.compile(r"(多少|几[个条名位人]|一共)")


class TableQAEnhancer:
    """Detect table queries and extract precise answers from structured tables."""

    def is_table_query(self, question: str) -> bool:
        return any(p.search(question) for p in _TABLE_QUERY_PATTERNS)

    def extract_answer(
        self, question: str, tables: list[dict]
    ) -> TableAnswer | None:
        if not tables:
            return None

        # Determine aggregation type
        aggregation = self._detect_aggregation(question)

        # Try each table
        for table_index, table in enumerate(tables):
            headers = table.get("headers", [])
            rows = table.get("rows", [])
            if not headers or not rows:
                continue

            answer = self._try_extract(question, headers, rows, aggregation, table_index)
            if answer is not None:
                return answer

        return None

    def _detect_aggregation(self, question: str) -> str:
        for pattern, op in _AGG_PATTERNS:
            if pattern.search(question):
                return op
        if _COUNT_PATTERN.search(question):
            return "count"
        return "lookup"

    def _find_column_index(self, question: str, headers: list[str]) -> int | None:
        """Find the best matching column by header name overlap.

        Two-pass: first try full header-name match, then partial char match.
        When the question contains count/quantity keywords (多少/几), prefer
        headers whose characters overlap with those keywords (e.g. 人数).
        """
        # Quantity keyword affinity: if "多少人" or "几个" is in the question,
        # prefer columns whose name shares chars with those keywords.
        qty_keywords = "人数数量金额额数"
        has_qty_intent = bool(re.search(r"(多少|几[个条名位人])", question))
        if has_qty_intent:
            for idx, header in enumerate(headers):
                if any(c in header for c in qty_keywords):
                    return idx

        # Exact full-header match
        for idx, header in enumerate(headers):
            if header and len(header) >= 2 and header in question:
                return idx
        # Partial match: most header chars appear in question
        best_idx: int | None = None
        best_overlap = 0
        for idx, header in enumerate(headers):
            if len(header) < 2:
                continue
            overlap = sum(1 for c in header if c in question)
            if overlap > best_overlap and overlap >= 2:
                best_overlap = overlap
                best_idx = idx
        return best_idx

    def _find_row_index(self, question: str, headers: list[str], rows: list[list[str]]) -> int | None:
        """Find a row by matching non-target cell values in the question."""
        for row_idx, row in enumerate(rows):
            for cell in row:
                if cell and len(cell) >= 2 and cell in question:
                    return row_idx
        return None

    def _try_extract(
        self,
        question: str,
        headers: list[str],
        rows: list[list[str]],
        aggregation: str,
        table_index: int,
    ) -> TableAnswer | None:
        col_idx = self._find_column_index(question, headers)

        # If a specific row is identifiable, prefer lookup over count
        row_idx = self._find_row_index(question, headers, rows)
        if aggregation == "count" and row_idx is not None and col_idx is not None:
            aggregation = "lookup"

        if aggregation == "count":
            return TableAnswer(
                value=str(len(rows)),
                aggregation="count",
                column=headers[col_idx] if col_idx is not None else "*",
                source_table_index=table_index,
            )

        if col_idx is None:
            return None

        # Collect numeric values from the target column
        numeric_values: list[float] = []
        for row in rows:
            if col_idx < len(row):
                cell = row[col_idx].replace(",", "").replace("，", "")
                try:
                    numeric_values.append(float(cell))
                except (ValueError, TypeError):
                    continue

        if aggregation == "lookup":
            row_idx = self._find_row_index(question, headers, rows)
            if row_idx is not None and col_idx < len(rows[row_idx]):
                return TableAnswer(
                    value=rows[row_idx][col_idx],
                    aggregation="lookup",
                    column=headers[col_idx],
                    source_table_index=table_index,
                )
            # Fallback: if only one numeric value found
            if len(numeric_values) == 1:
                return TableAnswer(
                    value=rows[0][col_idx],
                    aggregation="lookup",
                    column=headers[col_idx],
                    source_table_index=table_index,
                )
            return None

        if not numeric_values:
            return None

        if aggregation == "sum":
            result = sum(numeric_values)
        elif aggregation == "max":
            result = max(numeric_values)
        elif aggregation == "min":
            result = min(numeric_values)
        elif aggregation == "avg":
            result = sum(numeric_values) / len(numeric_values)
        else:
            return None

        # Format: drop trailing .0 for integers
        if result == int(result):
            value_str = str(int(result))
        else:
            value_str = f"{result:.2f}"

        return TableAnswer(
            value=value_str,
            aggregation=aggregation,
            column=headers[col_idx],
            source_table_index=table_index,
        )
