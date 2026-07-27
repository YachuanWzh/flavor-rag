"""Cross-element reference injection.

Detects reference patterns in text chunks (e.g. "如表3所示", "见图5", "详见下表")
and injects the reference sentence into the target TABLE/IMAGE chunk's
embedding_content, so that vector search can associate the reference anchor
with the actual media element.

Usage:
    from app.ingestion.cross_reference import inject_cross_references
    chunks = inject_cross_references(chunks)
"""

from __future__ import annotations

import re

from app.config.logging_config import get_logger

_log = get_logger("flavorag.ingestion.cross_reference")

# ---------------------------------------------------------------------------
# Reference pattern detection
# ---------------------------------------------------------------------------

# Matches: 表1, 表 1, 表一, 表A, Table 3, table-2
_TABLE_REF = re.compile(
    r"(?:表|Table|table|TABLE)\s*[-_]?\s*(\d+|[一二三四五六七八九十]+|[A-Za-z])",
    re.IGNORECASE,
)

# Matches: 图1, 图 1, 图一, Figure 5, Fig.3, fig-2
_FIGURE_REF = re.compile(
    r"(?:图|Figure|figure|FIGURE|Fig\.?|fig\.?)\s*[-_]?\s*(\d+|[一二三四五六七八九十]+|[A-Za-z])",
    re.IGNORECASE,
)

# Positional references: 下表, 上表, 下图, 上图, 以下表格, 如下表
_POSITIONAL_TABLE = re.compile(
    r"(?:下|上|以下|如下的?|下列)(?:表|表格)"
)
_POSITIONAL_FIGURE = re.compile(
    r"(?:下|上|以下|如下的?|下列)(?:图|图片|示意图|流程图)"
)

# Generic forward references: 详见下表, 如下所示, 见下图
_GENERIC_TABLE_FORWARD = re.compile(
    r"(?:详见|参见|见|如|参考)\s*(?:下|以下)?\s*(?:表|表格)"
)
_GENERIC_FIGURE_FORWARD = re.compile(
    r"(?:详见|参见|见|如|参考)\s*(?:下|以下)?\s*(?:图|图片|示意图|流程图)"
)

# Sentence boundary: split text into sentences for extraction
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?\n])")

# Maximum character distance between reference chunk and target chunk
# (in chunk_index units) to consider them related
_MAX_CHUNK_DISTANCE = 3


def _extract_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    parts = _SENTENCE_SPLIT.split(text)
    return [s.strip() for s in parts if s.strip()]


def _find_reference_sentences(content: str) -> list[dict]:
    """Find sentences containing cross-element references.

    Returns list of dicts:
        {
            "sentence": str,       # the full sentence containing the reference
            "ref_type": "TABLE" | "IMAGE",
            "ref_label": str | None,  # e.g. "3", "一", "A" (None for positional)
            "positional": bool,    # True for "下表/下图" style
        }
    """
    refs: list[dict] = []
    sentences = _extract_sentences(content)

    for sentence in sentences:
        # Check numbered table references
        for m in _TABLE_REF.finditer(sentence):
            refs.append({
                "sentence": sentence,
                "ref_type": "TABLE",
                "ref_label": m.group(1),
                "positional": False,
            })
            break  # one ref per sentence per type is enough

        # Check numbered figure references
        for m in _FIGURE_REF.finditer(sentence):
            refs.append({
                "sentence": sentence,
                "ref_type": "IMAGE",
                "ref_label": m.group(1),
                "positional": False,
            })
            break

        # Check positional table references
        if _POSITIONAL_TABLE.search(sentence) or _GENERIC_TABLE_FORWARD.search(sentence):
            # Avoid duplicate if already caught by numbered pattern
            if not any(r["sentence"] == sentence and r["ref_type"] == "TABLE" for r in refs):
                refs.append({
                    "sentence": sentence,
                    "ref_type": "TABLE",
                    "ref_label": None,
                    "positional": True,
                })

        # Check positional figure references
        if _POSITIONAL_FIGURE.search(sentence) or _GENERIC_FIGURE_FORWARD.search(sentence):
            if not any(r["sentence"] == sentence and r["ref_type"] == "IMAGE" for r in refs):
                refs.append({
                    "sentence": sentence,
                    "ref_type": "IMAGE",
                    "ref_label": None,
                    "positional": True,
                })

    return refs


def _find_target_chunk(
    chunks: list[dict],
    source_index: int,
    ref_type: str,
    positional: bool,
) -> int | None:
    """Find the target chunk index for a reference.

    Strategy:
    - For positional references ("下表", "下图"): find the NEXT chunk of
      matching type within _MAX_CHUNK_DISTANCE.
    - For numbered references: also prefer the next matching chunk (since
      documents typically place the element right after the reference).
    """
    target_block_type = ref_type  # "TABLE" or "IMAGE"

    # Search forward first (most common: "如下表所示" → table comes after)
    for offset in range(1, _MAX_CHUNK_DISTANCE + 1):
        idx = source_index + offset
        if idx >= len(chunks):
            break
        if chunks[idx].get("block_type", "") == target_block_type:
            return idx

    # Search backward (less common: "上表" or reference after the element)
    for offset in range(1, _MAX_CHUNK_DISTANCE + 1):
        idx = source_index - offset
        if idx < 0:
            break
        if chunks[idx].get("block_type", "") == target_block_type:
            return idx

    return None


def inject_cross_references(chunks: list[dict]) -> list[dict]:
    """Post-process chunks to inject reference sentences into target elements.

    For each text chunk containing a reference like "如表3所示，...", the
    sentence is prepended to the target TABLE/IMAGE chunk's embedding_content.
    This allows vector search to associate "表3" with the actual table chunk.

    Args:
        chunks: List of chunk dicts (mutated in place and returned).

    Returns:
        The same list (mutated in place) for chaining convenience.
    """
    if not chunks or len(chunks) < 2:
        return chunks

    # Collect all injections: target_chunk_index → list of sentences to inject
    injections: dict[int, list[str]] = {}
    injected_count = 0

    for i, chunk in enumerate(chunks):
        block_type = chunk.get("block_type", "")
        # Only scan text-bearing chunks for references
        if block_type in ("TABLE", "IMAGE", "CODE"):
            continue

        content = chunk.get("content", "")
        if not content:
            continue

        refs = _find_reference_sentences(content)
        if not refs:
            continue

        for ref in refs:
            target_idx = _find_target_chunk(
                chunks, i, ref["ref_type"], ref["positional"]
            )
            if target_idx is None:
                continue

            sentence = ref["sentence"]
            # Avoid injecting the same sentence twice
            if target_idx not in injections:
                injections[target_idx] = []
            if sentence not in injections[target_idx]:
                injections[target_idx].append(sentence)
                injected_count += 1

    # Apply injections
    for target_idx, sentences in injections.items():
        chunk = chunks[target_idx]
        # Build reference prefix
        ref_prefix = "引用上下文: " + " ".join(sentences)

        # Prepend to embedding_content
        existing_embedding = chunk.get("embedding_content", "")
        if existing_embedding:
            chunk["embedding_content"] = f"{ref_prefix}\n{existing_embedding}"
        else:
            # Fall back to content if no embedding_content
            chunk["embedding_content"] = f"{ref_prefix}\n{chunk.get('content', '')}"

        # Record in metadata for traceability
        metadata = chunk.get("metadata_json")
        if metadata is None:
            metadata = {}
            chunk["metadata_json"] = metadata
        metadata["cross_references"] = sentences

    if injected_count > 0:
        _log.info(
            "cross_reference_injection",
            total_chunks=len(chunks),
            injections=injected_count,
            targets=len(injections),
        )

    return chunks
