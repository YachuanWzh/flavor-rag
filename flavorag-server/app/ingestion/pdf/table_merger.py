"""Conservative reconstruction of tables split across PDF page boundaries."""

from __future__ import annotations

import copy
import re
from collections import Counter
from difflib import SequenceMatcher

from app.ingestion.pdf.models import PdfBlock, PdfBlockType


class CrossPageTableMerger:
    """Merge only high-confidence consecutive table fragments.

    Geometry establishes that fragments occupy a page boundary. Repeated
    headers are strong evidence. Headerless continuation additionally requires
    non-text column type evidence, avoiding accidental merges of unrelated
    text-only tables.
    """

    def __init__(
        self,
        *,
        bottom_zone_start: float = 0.78,
        top_zone_end: float = 0.22,
        horizontal_overlap_min: float = 0.94,
        header_similarity_min: float = 0.90,
        headerless_type_match_min: float = 0.80,
    ):
        self.bottom_zone_start = bottom_zone_start
        self.top_zone_end = top_zone_end
        self.horizontal_overlap_min = horizontal_overlap_min
        self.header_similarity_min = header_similarity_min
        self.headerless_type_match_min = headerless_type_match_min

    def merge(self, blocks: list[PdfBlock]) -> list[PdfBlock]:
        ordered = sorted(
            blocks,
            key=lambda b: (
                b.page_start,
                b.first_bbox.top if b.first_bbox else 0.0,
                b.first_bbox.x0 if b.first_bbox else 0.0,
            ),
        )
        output: list[PdfBlock] = []
        for candidate in ordered:
            if (
                output
                and output[-1].block_type == PdfBlockType.TABLE
                and candidate.block_type == PdfBlockType.TABLE
            ):
                mode, confidence = self._continuation_mode(output[-1], candidate)
                if mode:
                    output[-1] = self._merge_pair(output[-1], candidate, mode, confidence)
                    continue
            output.append(copy.deepcopy(candidate))
        return output

    def _continuation_mode(self, previous: PdfBlock, candidate: PdfBlock) -> tuple[str, float]:
        if previous.page_end + 1 != candidate.page_start:
            return "", 0.0
        if _looks_like_new_table_caption(candidate.metadata.get("preceding_text", "")):
            return "", 0.0
        prev_box = previous.last_bbox
        next_box = candidate.first_bbox
        if not prev_box or not next_box:
            return "", 0.0
        if prev_box.normalized_bottom < self.bottom_zone_start:
            return "", 0.0
        if next_box.normalized_top > self.top_zone_end:
            return "", 0.0
        if len(previous.table_headers) != len(candidate.table_headers):
            return "", 0.0
        if not previous.table_headers:
            return "", 0.0

        geometry = _horizontal_overlap(previous.last_bbox, candidate.first_bbox)
        if geometry < self.horizontal_overlap_min:
            return "", 0.0

        header_similarity = _row_similarity(previous.table_headers, candidate.table_headers)
        if header_similarity >= self.header_similarity_min:
            return "repeated", min(1.0, (geometry + header_similarity) / 2)

        type_match, has_non_text_signal = _headerless_type_match(previous, candidate)
        if has_non_text_signal and type_match >= self.headerless_type_match_min:
            return "headerless", min(0.99, (geometry + type_match) / 2)
        return "", 0.0

    def _merge_pair(
        self,
        previous: PdfBlock,
        candidate: PdfBlock,
        mode: str,
        confidence: float,
    ) -> PdfBlock:
        merged = copy.deepcopy(previous)
        candidate_rows = [list(row) for row in candidate.table_rows]
        candidate_pages = list(candidate.table_row_pages)
        if mode == "headerless":
            candidate_rows.insert(0, list(candidate.table_headers))
            candidate_pages.insert(0, candidate.page_start)

        merged.table_rows.extend(candidate_rows)
        merged.table_row_pages.extend(candidate_pages)
        merged.page_end = candidate.page_end
        merged.bboxes.extend(candidate.bboxes)

        pages = list(dict.fromkeys(
            list(merged.metadata.get("continuation_pages", [merged.page_start]))
            + list(candidate.metadata.get("continuation_pages", [candidate.page_start]))
        ))
        merged.metadata.update({
            "cross_page": True,
            "continuation_pages": pages,
            "header_mode": mode,
            "merge_confidence": round(confidence, 4),
            "source_table_ids": list(dict.fromkeys(
                list(merged.metadata.get("source_table_ids", [merged.block_id]))
                + list(candidate.metadata.get("source_table_ids", [candidate.block_id]))
            )),
        })
        return merged


def _horizontal_overlap(left, right) -> float:
    if not left or not right:
        return 0.0
    overlap = max(
        0.0,
        min(left.normalized_x1, right.normalized_x1)
        - max(left.normalized_x0, right.normalized_x0),
    )
    union = max(left.normalized_x1, right.normalized_x1) - min(
        left.normalized_x0, right.normalized_x0
    )
    return overlap / union if union else 0.0


def _row_similarity(left: list[str], right: list[str]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    scores = [
        SequenceMatcher(None, _normalize_cell(a), _normalize_cell(b)).ratio()
        for a, b in zip(left, right)
    ]
    return sum(scores) / len(scores)


def _normalize_cell(value: str) -> str:
    return re.sub(r"[\W_]+", "", (value or "").casefold())


def _headerless_type_match(previous: PdfBlock, candidate: PdfBlock) -> tuple[float, bool]:
    column_count = len(previous.table_headers)
    if column_count == 0 or len(candidate.table_headers) != column_count:
        return 0.0, False

    history = previous.table_rows[-20:]
    signatures: list[str] = []
    for column in range(column_count):
        values = [row[column] for row in history if column < len(row) and row[column].strip()]
        types = [_value_type(value) for value in values]
        signatures.append(Counter(types).most_common(1)[0][0] if types else "empty")

    candidate_types = [_value_type(value) for value in candidate.table_headers]
    comparable = [
        (expected, actual)
        for expected, actual in zip(signatures, candidate_types)
        if expected != "empty"
    ]
    if not comparable:
        return 0.0, False

    matches = sum(expected == actual for expected, actual in comparable)
    has_non_text = any(expected not in ("text", "empty") for expected, _ in comparable)
    return matches / len(comparable), has_non_text


_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_DATE = re.compile(
    r"^(?:\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})$"
)


def _value_type(value: str) -> str:
    normalized = " ".join((value or "").split())
    if not normalized:
        return "empty"
    if _NUMBER.fullmatch(normalized):
        return "number"
    if _DATE.fullmatch(normalized):
        return "date"
    if normalized.casefold() in {"true", "false", "yes", "no", "是", "否"}:
        return "boolean"
    return "text"


_NEW_TABLE_CAPTION = re.compile(
    r"^\s*(?:表|附表|table)\s*[\d一二三四五六七八九十]+(?:[.：:\-\s]|$)",
    re.IGNORECASE,
)


def _looks_like_new_table_caption(value: str) -> bool:
    return bool(_NEW_TABLE_CAPTION.search(value or ""))
