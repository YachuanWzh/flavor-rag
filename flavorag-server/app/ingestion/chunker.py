"""Document chunker — splits text into chunks using configurable strategies.

Direct Python translation of ragent Java chunking subsystem:
  - FIXED_WINDOW (fixed_size): 固定窗口切分，按 chunkSize 滑动，overlapSize 重叠
  - SEMANTIC (structure_aware): 语义感知切分，保留 Markdown 结构（标题/代码/图片/段落）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


# ============================================================================
# Strategy / Options (Java: ChunkingMode, ChunkingOptions)
# ============================================================================

class ChunkStrategy(Enum):
    FIXED_WINDOW = ("fixed_size", "固定窗口")
    SEMANTIC = ("structure_aware", "语义切分")
    BLOCK_AWARE = ("block_aware", "块感知切分")

    def __init__(self, value: str, label: str):
        self._value_ = value
        self.label = label

    @classmethod
    def from_value(cls, value) -> "ChunkStrategy":
        if isinstance(value, cls):
            return value
        if not value:
            raise ValueError("Chunk strategy value must not be empty")
        normalized = value.strip().lower().replace("-", "_")
        # Direct match
        for s in cls:
            if s.value == normalized:
                return s
        # User-facing names: FIXED_WINDOW, SEMANTIC, BLOCK_AWARE
        name_map = {
            "fixed_window": cls.FIXED_WINDOW,
            "semantic": cls.SEMANTIC,
            "block_aware": cls.BLOCK_AWARE,
        }
        if normalized in name_map:
            return name_map[normalized]
        # Legacy names
        upper = normalized.upper()
        legacy_map = {
            "FIXED_SIZE": cls.FIXED_WINDOW,
            "FIXED_WINDOW": cls.FIXED_WINDOW,
            "STRUCTURE_AWARE": cls.SEMANTIC,
            "SEMANTIC": cls.SEMANTIC,
            "BLOCK_AWARE": cls.BLOCK_AWARE,
        }
        if upper in legacy_map:
            return legacy_map[upper]
        raise ValueError(f"Unknown chunk strategy: {value!r}")


# ---------------------------------------------------------------------------
# FixedSizeOptions (Java record: chunkSize / overlapSize)
# ---------------------------------------------------------------------------
@dataclass
class FixedSizeOptions:
    chunk_size: int = 512
    overlap_size: int = 128

    def to_config_map(self) -> dict:
        return {"chunkSize": self.chunk_size, "overlapSize": self.overlap_size}


# ---------------------------------------------------------------------------
# TextBoundaryOptions (Java record: targetChars / overlapChars / maxChars / minChars)
# ---------------------------------------------------------------------------
@dataclass
class TextBoundaryOptions:
    target_chars: int = 1400
    overlap_chars: int = 0
    max_chars: int = 1800
    min_chars: int = 600

    def to_config_map(self) -> dict:
        return {
            "targetChars": self.target_chars,
            "overlapChars": self.overlap_chars,
            "maxChars": self.max_chars,
            "minChars": self.min_chars,
        }


# ---------------------------------------------------------------------------
# BlockAwareOptions — per-block-type sizing budgets
# ---------------------------------------------------------------------------
@dataclass
class BlockAwareOptions:
    table_max_rows: int = 20         # rows per table chunk
    code_max_lines: int = 80         # lines per code chunk
    heading_as_path: bool = True     # treat headings as path prefix, not standalone
    list_max_items: int = 30         # items per list chunk
    target_chars: int = 800          # target chars for paragraph chunks
    max_chars: int = 1200            # max chars for paragraph chunks
    min_chars: int = 300             # min chars before merging
    overlap_chars: int = 50          # character overlap between chunks

    def to_config_map(self) -> dict:
        return {
            "tableMaxRows": self.table_max_rows,
            "codeMaxLines": self.code_max_lines,
            "headingAsPath": self.heading_as_path,
            "listMaxItems": self.list_max_items,
            "targetChars": self.target_chars,
            "maxChars": self.max_chars,
            "minChars": self.min_chars,
            "overlapChars": self.overlap_chars,
        }


# ============================================================================
# ChunkConfig (user-facing, resolves strategy + options)
# ============================================================================
@dataclass
class ChunkConfig:
    """User-facing chunk configuration.

    Attributes:
        strategy: "FIXED_WINDOW" | "SEMANTIC" (or legacy FIXED_SIZE / STRUCTURE_AWARE)
        chunk_size: 通用块大小 (fixed_size: chunkSize; structure_aware: targetChars)
        overlap: 通用重叠大小 (fixed_size: overlapSize; structure_aware: overlapChars)
    """
    strategy: str = "FIXED_WINDOW"
    chunk_size: int = 512
    overlap: int = 128

    def __post_init__(self):
        pass

    def resolve_strategy(self) -> ChunkStrategy:
        return ChunkStrategy.from_value(self.strategy)

    def resolve_options(self):
        s = self.resolve_strategy()
        if s == ChunkStrategy.FIXED_WINDOW:
            return FixedSizeOptions(
                chunk_size=self.chunk_size,
                overlap_size=self.overlap,
            )
        elif s == ChunkStrategy.BLOCK_AWARE:
            return BlockAwareOptions(
                target_chars=self.chunk_size if self.chunk_size else 800,
                overlap_chars=self.overlap,
            )
        else:  # SEMANTIC
            return TextBoundaryOptions(
                target_chars=self.chunk_size if self.chunk_size else 1400,
                overlap_chars=self.overlap,
                max_chars=1800,
                min_chars=600,
            )


# ============================================================================
# Helper: chunk output record (Java: VectorChunk)
# ============================================================================
@dataclass
class ChunkRecord:
    """Chunk output aligned with ragent VectorChunk:

    - content: human-readable text (stored / shown / fed to LLM)
    - embedding_text: search-optimized text (embedded / BM25); empty means
      content doubles as the embedding text
    - block_type: source block type (PARAGRAPH/TABLE/CODE/LIST/IMAGE/HEADING)
    - outline_path: heading path from document root to this chunk
    """
    content: str
    chunk_index: int
    block_type: str = ""
    embedding_text: str = ""
    outline_path: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.content)

    def to_dict(self) -> dict:
        d = {
            "content": self.content,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
        }
        if self.block_type:
            d["block_type"] = self.block_type
        if self.embedding_text:
            d["embedding_content"] = self.embedding_text
        if self.outline_path:
            d["metadata_json"] = {"outline_path": list(self.outline_path)}
        return d


# ============================================================================
# DocumentChunker
# ============================================================================
class DocumentChunker:
    """Splits document text into chunks for embedding and indexing."""

    def chunk(self, text: str, config: ChunkConfig | None = None) -> list[dict]:
        if config is None:
            config = ChunkConfig()
        if not text:
            return []

        strategy = config.resolve_strategy()
        options = config.resolve_options()

        if strategy == ChunkStrategy.FIXED_WINDOW:
            records = _FixedSizeChunker().chunk(text, options)
        elif strategy == ChunkStrategy.BLOCK_AWARE:
            records = _BlockAwareChunker().chunk(text, options)
        else:
            records = _StructureAwareChunker().chunk(text, options)

        return [r.to_dict() for r in records]

    async def chunk_semantic(
        self,
        text: str,
        config: ChunkConfig,
        *,
        embedder=None,
        similarity_threshold: float = 0.55,
    ) -> list[dict]:
        """Embedding-based semantic boundary detection for plain text."""
        import math
        import re

        if not text.strip():
            return []
        paragraphs = [
            part.strip()
            for part in re.split(r"\n\s*\n+", text)
            if part.strip()
        ]
        if len(paragraphs) <= 1:
            return self.chunk(text, config)
        if embedder is None:
            from app.llm.embedding import get_embedding_client

            embedder = get_embedding_client()
        vectors = await embedder.embed_documents(paragraphs)

        def cosine(left: list[float], right: list[float]) -> float:
            numerator = sum(a * b for a, b in zip(left, right))
            left_norm = math.sqrt(sum(value * value for value in left))
            right_norm = math.sqrt(sum(value * value for value in right))
            if not left_norm or not right_norm:
                return 0.0
            return numerator / (left_norm * right_norm)

        groups: list[list[str]] = []
        current = [paragraphs[0]]
        current_chars = len(paragraphs[0])
        target = max(128, config.chunk_size)
        maximum = max(target, int(target * 1.5))
        for index in range(1, len(paragraphs)):
            paragraph = paragraphs[index]
            similarity = cosine(vectors[index - 1], vectors[index])
            boundary = (
                current_chars >= target and similarity < similarity_threshold
            ) or current_chars + len(paragraph) > maximum
            if boundary:
                groups.append(current)
                current = []
                current_chars = 0
            current.append(paragraph)
            current_chars += len(paragraph) + 2
        if current:
            groups.append(current)
        return [
            {
                "content": "\n\n".join(group),
                "embedding_content": "\n\n".join(group),
                "chunk_index": index,
                "char_count": len("\n\n".join(group)),
                "block_type": "SEMANTIC",
                "metadata_json": {
                    "semantic_split": True,
                    "similarity_threshold": similarity_threshold,
                },
            }
            for index, group in enumerate(groups)
        ]


# ============================================================================
# FIXED_WINDOW (Java: FixedSizeTextChunker)
# ============================================================================
class _FixedSizeChunker:

    def chunk(self, text: str, options: FixedSizeOptions) -> list[ChunkRecord]:
        if not text or not text.strip():
            return []

        # 1) normalize: fix broken URLs, CJK mid-word line breaks
        normalized = self._normalize_text(text)

        chunk_size = options.chunk_size
        if chunk_size == -1:
            return [ChunkRecord(content=normalized, chunk_index=0)]

        chunk_size = max(1, chunk_size)
        overlap = max(0, options.overlap_size)
        if chunk_size > 1:
            overlap = min(overlap, chunk_size - 1)
        else:
            overlap = 0

        text_len = len(normalized)
        result: list[ChunkRecord] = []
        index = 0
        start = 0
        last_end = -1

        while start < text_len:
            target_end = min(start + chunk_size, text_len)
            end = self._adjust_to_boundary(normalized, start, target_end, overlap)

            # Force advance to avoid looping
            if end <= start or end <= last_end:
                end = target_end

            content = normalized[start:end]
            if content.strip():
                result.append(ChunkRecord(content=content, chunk_index=index))
                index += 1

            last_end = end
            if end >= text_len:
                break

            next_start = max(0, end - overlap)
            if next_start <= start:
                next_start = end
            start = next_start

        return result

    # ------------------------------------------------------------------
    # Boundary adjustment (Java: adjustToBoundary)
    # ------------------------------------------------------------------
    def _adjust_to_boundary(self, text: str, start: int, target_end: int, overlap: int) -> int:
        if target_end <= start:
            return target_end

        max_lookback = min(overlap, target_end - start)
        if max_lookback <= 0:
            return target_end

        # 1) Newline
        for i in range(max_lookback):
            pos = target_end - i - 1
            if pos <= start:
                break
            if text[pos] == '\n':
                return pos + 1

        # 2) Chinese sentence-ending punctuation
        for i in range(max_lookback):
            pos = target_end - i - 1
            if pos <= start:
                break
            if text[pos] in ('。', '！', '？'):
                return pos + 1

        # 3) English sentence-ending punctuation (only if followed by whitespace/end)
        for i in range(max_lookback):
            pos = target_end - i - 1
            if pos <= start:
                break
            if text[pos] in ('.', '!', '?'):
                nxt = pos + 1
                if nxt >= len(text) or text[nxt].isspace():
                    return nxt

        return target_end

    # ------------------------------------------------------------------
    # Text normalization (Java: normalizeText)
    # ------------------------------------------------------------------
    def _normalize_text(self, text: str) -> str:
        if not text:
            return text

        src = text.replace('\r', '')
        out: list[str] = []
        n = len(src)
        i = 0
        in_url = False

        while i < n:
            if not in_url and self._looks_like_url_start(src, i):
                in_url = True

            c = src[i]

            if in_url:
                if c.isspace():
                    j = i
                    newline_count = 0
                    while j < n and src[j].isspace():
                        if src[j] == '\n':
                            newline_count += 1
                        j += 1

                    saw_newline = newline_count > 0
                    blank_line = newline_count >= 2  # >= 2 newlines = paragraph break

                    prev_ch = src[i - 1] if i > 0 else ''
                    next_ch = src[j] if j < n else ''

                    if saw_newline and not blank_line and next_ch and self._should_join_broken_url(prev_ch, next_ch, src, j):
                        i = j - 1
                        continue

                    # URL ends: keep original whitespace
                    out.append(src[i:j])
                    in_url = False
                    i = j - 1
                    continue

                out.append(c)

                if not self._is_url_char(c) and not self._is_common_url_punct(c):
                    in_url = False
                i += 1
                continue

            # Non-URL: fix CJK mid-word line break
            if c == '\n':
                prev_ch = src[i - 1] if i > 0 else ''
                next_ch = src[i + 1] if i + 1 < n else ''

                if self._is_cjk_word_char(prev_ch) and self._is_cjk_word_char(next_ch):
                    i += 1
                    continue

                out.append('\n')
                i += 1
                continue

            out.append(c)
            i += 1

        return ''.join(out)

    # ------------------------------------------------------------------
    # URL detection helpers
    # ------------------------------------------------------------------
    def _looks_like_url_start(self, s: str, i: int) -> bool:
        if i < 0 or i >= len(s):
            return False
        return s[i:].startswith('http://') or s[i:].startswith('https://')

    def _should_join_broken_url(self, prev: str, nxt: str, s: str, next_idx: int) -> bool:
        # If next line looks like a list item start, never merge
        if self._is_list_item_start(s, next_idx):
            return False

        # Typical URL break patterns
        if prev == '.' and nxt.isalpha():
            return True
        if prev in ('/', '?', '&', '=', '#', '%', '-', '_', ':'):
            return True
        if nxt in ('/', '?', '&', '=', '#'):
            return True

        return False

    def _is_list_item_start(self, s: str, i: int) -> bool:
        p = i
        while p < len(s) and s[p] in (' ', '\t'):
            p += 1

        start = p
        while p < len(s) and s[p].isdigit():
            p += 1
        if p == start:
            return False

        if p < len(s) and s[p] in ('.', '）', ')'):
            return True
        return False

    def _is_url_char(self, c: str) -> bool:
        if c.isascii() and c.isalpha():
            return True
        if c.isascii() and c.isdigit():
            return True
        return c in '-._~:/?#[]@!$&\'()*+,;=%'

    def _is_common_url_punct(self, c: str) -> bool:
        return c in './?&=-_%'

    # ------------------------------------------------------------------
    # CJK helpers
    # ------------------------------------------------------------------
    def _is_cjk_word_char(self, c: str) -> bool:
        if not c or c.isspace():
            return False
        if not self._is_cjk_or_fullwidth(c):
            return False
        return not self._is_cjk_punctuation(c)

    def _is_cjk_or_fullwidth(self, c: str) -> bool:
        cp = ord(c)
        return (
            (0x4E00 <= cp <= 0x9FFF) or      # CJK Unified Ideographs
            (0x3400 <= cp <= 0x4DBF) or      # CJK Unified Ideographs Extension A
            (0x20000 <= cp <= 0x2A6DF) or    # CJK Unified Ideographs Extension B
            (0xF900 <= cp <= 0xFAFF) or      # CJK Compatibility Ideographs
            (0xFF00 <= cp <= 0xFFEF)         # Halfwidth and Fullwidth Forms
        )

    def _is_cjk_punctuation(self, c: str) -> bool:
        cp = ord(c)
        if (0x3000 <= cp <= 0x303F):  # CJK Symbols and Punctuation
            return True
        if (0x2000 <= cp <= 0x206F):  # General Punctuation
            return True
        return c in '。，、；：！？（）【】《》""''·'


# ============================================================================
# SEMANTIC (Java: StructureAwareTextChunker)
# ============================================================================
class _StructureAwareChunker:

    _HEADING = re.compile(r'^#{1,6}\s+.*$')
    _CODE_FENCE = re.compile(r'^```.*$')
    _ATOMIC_IMAGE = re.compile(r'^!\[[^\]]*\]\([^)]+\)(?:\s*"[^"]*")?\s*$')
    _ATOMIC_LINK = re.compile(r'^\[[^\]]+\]\([^)]+\)\s*$')

    def chunk(self, text: str, options: TextBoundaryOptions) -> list[ChunkRecord]:
        if not text or not text.strip():
            return []

        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        target = options.target_chars
        max_chars = options.max_chars
        min_chars = options.min_chars
        overlap = options.overlap_chars

        # 1) Segment into blocks
        blocks = self._segment_to_blocks(text)
        if not blocks:
            return [ChunkRecord(content=text, chunk_index=0)]

        # 2) Pack blocks → chunk ranges
        ranges = self._pack_blocks_to_chunks(blocks, len(text), min_chars, target, max_chars)

        # 3) Materialize with overlap
        records = self._materialize(text, ranges, overlap)

        # Re-index
        for i, r in enumerate(records):
            r.chunk_index = i
        return records

    # ------------------------------------------------------------------
    # Block model
    # ------------------------------------------------------------------
    class _Block:
        __slots__ = ('kind', 'start', 'end')

        def __init__(self, kind: str, start: int, end: int):
            self.kind = kind    # HEADING / CODE / ATOMIC / PARA
            self.start = start
            self.end = end

    # ------------------------------------------------------------------
    # 1) Linear scan → blocks
    # ------------------------------------------------------------------
    def _segment_to_blocks(self, text: str) -> list:
        blocks: list = []
        n = len(text)
        pos = 0

        in_fence = False
        fence_start = -1
        in_para = False
        para_start = -1

        while pos < n:
            line_end = text.find('\n', pos)
            if line_end == -1:
                line_end = n
            # [pos, line_end) is the line content (without newline)
            line_end_nl = line_end + 1 if line_end < n and text[line_end] == '\n' else line_end
            line = text[pos:line_end]
            trimmed = self._trim_right_keep_left(line)

            # Fence start
            if not in_fence and self._CODE_FENCE.match(trimmed):
                if in_para:
                    blocks.append(self._Block("PARA", para_start, pos))
                    in_para = False
                in_fence = True
                fence_start = pos
                pos = line_end_nl
                continue

            # Inside fence
            if in_fence:
                if self._CODE_FENCE.match(trimmed):
                    blocks.append(self._Block("CODE", fence_start, line_end_nl))
                    in_fence = False
                pos = line_end_nl
                continue

            # Blank line → paragraph boundary
            if not trimmed:
                if in_para:
                    blocks.append(self._Block("PARA", para_start, pos))
                    in_para = False
                pos = line_end_nl
                continue

            # Heading
            if self._HEADING.match(trimmed):
                if in_para:
                    blocks.append(self._Block("PARA", para_start, pos))
                    in_para = False
                blocks.append(self._Block("HEADING", pos, line_end_nl))
                pos = line_end_nl
                continue

            # Atomic image / link
            if self._ATOMIC_IMAGE.match(trimmed) or self._ATOMIC_LINK.match(trimmed):
                if in_para:
                    blocks.append(self._Block("PARA", para_start, pos))
                    in_para = False
                blocks.append(self._Block("ATOMIC", pos, line_end_nl))
                pos = line_end_nl
                continue

            # Regular text → accumulate paragraph
            if not in_para:
                in_para = True
                para_start = pos
            pos = line_end_nl

        # Flush
        if in_fence:
            blocks.append(self._Block("CODE", fence_start, n))
        elif in_para:
            blocks.append(self._Block("PARA", para_start, n))

        return self._coalesce_trailing_blanks(blocks, text)

    def _coalesce_trailing_blanks(self, blocks: list, text: str) -> list:
        """Merge blank regions between blocks into the preceding block."""
        if not blocks:
            return blocks

        out: list = []
        prev = blocks[0]
        for i in range(1, len(blocks)):
            cur = blocks[i]
            if self._is_all_blank(text, prev.end, cur.start):
                prev = self._Block(prev.kind, prev.start, cur.start)
            else:
                out.append(prev)
                prev = cur
        out.append(prev)
        return out

    # ------------------------------------------------------------------
    # 2) Pack blocks → chunk ranges [start, end)
    # ------------------------------------------------------------------
    def _pack_blocks_to_chunks(
        self, blocks: list, text_len: int, min_chars: int, target: int, max_chars: int
    ) -> list:
        ranges: list = []
        i = 0
        while i < len(blocks):
            chunk_start = blocks[i].start
            chunk_end = blocks[i].end
            size = chunk_end - chunk_start

            j = i + 1
            while j < len(blocks):
                b = blocks[j]
                after_add = b.end - chunk_start

                if after_add <= max_chars:
                    chunk_end = b.end
                    size = after_add
                    j += 1
                else:
                    # If current is too small, absorb one more even if it exceeds max
                    if size < min_chars:
                        chunk_end = b.end
                        size = after_add
                        j += 1
                    break

            ranges.append([chunk_start, chunk_end])
            i = j

        # Merge last small chunk into previous
        if len(ranges) >= 2:
            last = ranges[-1]
            if last[1] - last[0] < min(min_chars, target // 2):
                prev = ranges[-2]
                if last[1] - prev[0] <= max_chars * 2:
                    prev[1] = last[1]
                    ranges.pop()

        return ranges

    # ------------------------------------------------------------------
    # 3) Materialize
    # ------------------------------------------------------------------
    def _materialize(self, text: str, ranges: list, overlap: int) -> list[ChunkRecord]:
        if not ranges:
            return []

        result: list[ChunkRecord] = []
        prev_tail = None

        for k, (s, e) in enumerate(ranges):
            body = text[s:e]
            if overlap > 0 and prev_tail and prev_tail.strip():
                body = prev_tail + body

            result.append(ChunkRecord(content=body, chunk_index=k))

            if overlap > 0:
                prev_tail = self._tail_by_chars(text[s:e], overlap)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _trim_right_keep_left(self, s: str) -> str:
        """Trim right-side whitespace except newlines."""
        r = len(s)
        while r > 0 and s[r - 1].isspace() and s[r - 1] not in ('\n', '\r'):
            r -= 1
        return s[:r]

    def _is_all_blank(self, s: str, start: int, end: int) -> bool:
        for i in range(start, end):
            if s[i] not in (' ', '\t', '\r', '\n'):
                return False
        return True

    def _tail_by_chars(self, s: str, n: int) -> str:
        if n <= 0:
            return ""
        return s if len(s) <= n else s[-n:]


# ============================================================================
# BLOCK_AWARE (Block-Aware Chunker with per-block-type strategies)
# ============================================================================
class _BlockAwareChunker:
    """Splits a document into typed blocks and dispatches each to a dedicated
    sub-chunker. The key innovation is table → dual-text (original + key:value)
    for improved search recall.

    Block types:
        HEADING  → path prefix, not standalone chunks
        TABLE    → TableChunker: row-budgeted, key:value search text
        CODE     → CodeChunker: line-budgeted, isolated
        LIST     → ListChunker: item-budgeted, small ones packed
        IMAGE    → ImageChunker: alt-text only
        PARA     → ParagraphChunker: budget-based
    """

    # Patterns (shared with SEMANTIC for block detection)
    _HEADING = re.compile(r'^#{1,6}\s+.*$')
    _CODE_FENCE = re.compile(r'^```.*$')
    _ATOMIC_IMAGE = re.compile(r'^!\[[^\]]*\]\([^)]+\)(?:\s*"[^"]*")?\s*$')
    _ATOMIC_LINK = re.compile(r'^\[[^\]]+\]\([^)]+\)\s*$')
    # Detect markdown table lines
    _TABLE_SEP = re.compile(r'^\s*\|?\s*[-:]{3,}\s*(\|\s*[-:]{3,}\s*)+\|?\s*$')
    _TABLE_ROW = re.compile(r'^\s*\|.+\|\s*$')

    class _ParseBlock:
        __slots__ = ('kind', 'start', 'end')
        kind: str
        start: int
        end: int

        def __init__(self, kind: str, start: int, end: int):
            self.kind = kind  # HEADING / TABLE / CODE / LIST / IMAGE / PARA
            self.start = start
            self.end = end

    def chunk(self, text: str, options: BlockAwareOptions) -> list[ChunkRecord]:
        if not text or not text.strip():
            return []

        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # 1) Parse into typed blocks
        blocks = self._parse_blocks(text)

        # 2) Dispatch each block to its sub-chunker
        raw_chunks: list[ChunkRecord] = []
        current_heading_path: list[str] = []
        # kind → chunk block_type label
        kind_labels = {
            "TABLE": "TABLE", "CODE": "CODE", "LIST": "LIST",
            "IMAGE": "IMAGE", "PARA": "PARAGRAPH",
        }

        for blk in blocks:
            body = text[blk.start:blk.end]
            if blk.kind == "HEADING":
                h_text = body.strip().lstrip('#').strip()
                if options.heading_as_path:
                    current_heading_path.append(h_text)
                    # Keep path at most 4 levels deep
                    if len(current_heading_path) > 4:
                        current_heading_path.pop(0)
                else:
                    raw_chunks.append(ChunkRecord(
                        content=body, chunk_index=0, block_type="HEADING",
                    ))
                continue

            if blk.kind == "TABLE":
                sub = self._chunk_table(body, options)
            elif blk.kind == "CODE":
                sub = self._chunk_code(body, options)
            elif blk.kind == "LIST":
                sub = self._chunk_list(body, options)
            elif blk.kind == "IMAGE":
                sub = self._chunk_image(body)
            else:  # PARA
                sub = self._chunk_paragraph(body, options)

            block_type = kind_labels.get(blk.kind, "PARAGRAPH")
            for r in sub:
                if not r.block_type:
                    r.block_type = block_type
                r.outline_path = list(current_heading_path)

            # Prepend heading path to each chunk (content + embedding text)
            if current_heading_path:
                path_str = " > ".join(current_heading_path)
                for r in sub:
                    r.content = f"[{path_str}]\n{r.content}"
                    if r.embedding_text:
                        r.embedding_text = f"[{path_str}]\n{r.embedding_text}"

            raw_chunks.extend(sub)

        # 3) Pack adjacent small chunks
        packed = self._pack_chunks(raw_chunks, options)

        # Re-index
        for i, r in enumerate(packed):
            r.chunk_index = i

        return packed

    # ------------------------------------------------------------------
    # 1) Parse document into typed blocks
    # ------------------------------------------------------------------
    def _parse_blocks(self, text: str) -> list[_ParseBlock]:
        """Linear-scan parser that identifies TABLE, CODE, HEADING, LIST,
        IMAGE regions, with everything else treated as PARA."""
        blocks: list[_BlockAwareChunker._ParseBlock] = []
        n = len(text)
        pos = 0
        in_para = False
        para_start = -1
        in_code = False
        code_start = -1
        in_table = False
        table_start = -1

        while pos < n:
            line_end = text.find('\n', pos)
            if line_end == -1:
                line_end = n
            line_end_nl = line_end + 1 if line_end < n and text[line_end] == '\n' else line_end
            line = text[pos:line_end]
            trimmed = self._trim_right(line)

            # CODE fence
            if self._CODE_FENCE.match(trimmed):
                if in_code:
                    # Close code block
                    blocks.append(self._ParseBlock("CODE", code_start, line_end_nl))
                    in_code = False
                else:
                    if in_para:
                        blocks.append(self._ParseBlock("PARA", para_start, pos))
                        in_para = False
                    if in_table:
                        blocks.append(self._ParseBlock("TABLE", table_start, pos))
                        in_table = False
                    in_code = True
                    code_start = pos
                pos = line_end_nl
                continue

            if in_code:
                pos = line_end_nl
                continue

            # TABLE detection
            if in_table:
                # Check if we're still in a table
                if not self._TABLE_ROW.match(line) and trimmed.strip():
                    blocks.append(self._ParseBlock("TABLE", table_start, pos))
                    in_table = False
                    # Don't advance pos — re-evaluate this line
                    continue
                pos = line_end_nl
                continue

            if self._TABLE_ROW.match(line) or self._TABLE_SEP.match(trimmed):
                if in_para:
                    blocks.append(self._ParseBlock("PARA", para_start, pos))
                    in_para = False
                if not in_table:
                    in_table = True
                    table_start = pos
                pos = line_end_nl
                continue

            # Blank line → block boundary
            if not trimmed:
                if in_para:
                    blocks.append(self._ParseBlock("PARA", para_start, pos))
                    in_para = False
                pos = line_end_nl
                continue

            # Heading
            if self._HEADING.match(trimmed):
                if in_para:
                    blocks.append(self._ParseBlock("PARA", para_start, pos))
                    in_para = False
                blocks.append(self._ParseBlock("HEADING", pos, line_end_nl))
                pos = line_end_nl
                continue

            # Atomic image / link
            if self._ATOMIC_IMAGE.match(trimmed) or self._ATOMIC_LINK.match(trimmed):
                if in_para:
                    blocks.append(self._ParseBlock("PARA", para_start, pos))
                    in_para = False
                blocks.append(self._ParseBlock("IMAGE", pos, line_end_nl))
                pos = line_end_nl
                continue

            # Check for list items (unordered: `- `, `* `, `+ `; ordered: `1. `)
            if self._is_list_item(trimmed):
                if in_para:
                    blocks.append(self._ParseBlock("PARA", para_start, pos))
                    in_para = False
                # Accumulate list items into one LIST block
                list_start = pos
                while pos < n:
                    le = text.find('\n', pos)
                    if le == -1:
                        le = n
                    le_nl = le + 1 if le < n and text[le] == '\n' else le
                    l_line = text[pos:le]
                    l_trim = self._trim_right(l_line)
                    if not l_trim:
                        pos = le_nl
                        # Check if next line is still a list item
                        if pos < n and self._is_list_item(self._trim_right(text[pos:text.find('\n', pos) if text.find('\n', pos) != -1 else n])):
                            continue
                        else:
                            break
                    if self._is_list_item(l_trim):
                        pos = le_nl
                    else:
                        break
                blocks.append(self._ParseBlock("LIST", list_start, pos))
                continue

            # Regular text → accumulate paragraph
            if not in_para:
                in_para = True
                para_start = pos
            pos = line_end_nl

        # Flush remaining
        if in_code:
            blocks.append(self._ParseBlock("CODE", code_start, n))
        elif in_table:
            blocks.append(self._ParseBlock("TABLE", table_start, n))
        elif in_para:
            blocks.append(self._ParseBlock("PARA", para_start, n))

        return blocks

    def _is_list_item(self, line: str) -> bool:
        """Check if a line starts a list item (ordered or unordered)."""
        if not line:
            return False
        # Unordered: - , * , +
        if line[0] in ('-', '*', '+') and len(line) > 1 and line[1] == ' ':
            return True
        # Ordered: 1. , 1) , 1、
        m = re.match(r'^(\d+)[.)、]\s', line)
        return m is not None

    # ------------------------------------------------------------------
    # 2) Sub-chunkers
    # ------------------------------------------------------------------

    def _chunk_table(self, text: str, options: BlockAwareOptions) -> list[ChunkRecord]:
        """Chunk a markdown table by rows with dual-text separation:
        - content: original table fragment (human-readable, full header)
        - embedding_text: key:value rows (embedding models can't align
          markdown table columns positionally, so embed "col: val" pairs)
        """
        lines = text.split('\n')
        if len(lines) < 2:
            return [ChunkRecord(content=text, chunk_index=0, block_type="TABLE")]

        # Separate header + separator from data rows
        header_rows: list[str] = []
        sep_row: str = ""
        data_rows: list[str] = []
        past_sep = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if self._TABLE_SEP.match(stripped):
                sep_row = stripped
                past_sep = True
                continue
            if not past_sep:
                header_rows.append(stripped)
            else:
                data_rows.append(stripped)

        if not data_rows:
            return [ChunkRecord(content=text, chunk_index=0, block_type="TABLE")]

        # Parse header column names for key:value conversion
        col_names = self._parse_table_header(header_rows[0] if header_rows else "")
        header_ctx = "headers: " + ", ".join(col_names) if col_names else ""

        records: list[ChunkRecord] = []
        batch_size = max(1, options.table_max_rows)
        idx = 0

        for batch_start in range(0, len(data_rows), batch_size):
            batch = data_rows[batch_start:batch_start + batch_size]

            # Build original table fragment (content, human-readable)
            orig_lines = header_rows + [sep_row] + batch if sep_row else header_rows + batch
            orig = '\n'.join(orig_lines)

            # Build key:value embedding text (search-optimized)
            kv_lines: list[str] = []
            for row in batch:
                cells = self._parse_table_cells(row)
                kv_pairs: list[str] = []
                for ci, cell in enumerate(cells):
                    col_name = col_names[ci] if ci < len(col_names) else f"col{ci + 1}"
                    kv_pairs.append(f"{col_name}: {cell}")
                kv_lines.append("; ".join(kv_pairs))

            embedding_text = '\n'.join(
                part for part in [header_ctx, *kv_lines] if part
            )

            records.append(ChunkRecord(
                content=orig,
                chunk_index=idx,
                block_type="TABLE",
                embedding_text=embedding_text,
            ))
            idx += 1

        return records if records else [
            ChunkRecord(content=text, chunk_index=0, block_type="TABLE")
        ]

    def _parse_table_header(self, header_line: str) -> list[str]:
        """Extract column names from a | col1 | col2 | header."""
        if not header_line:
            return []
        cells = header_line.split('|')
        return [c.strip() for c in cells if c.strip()]

    def _parse_table_cells(self, row: str) -> list[str]:
        """Extract cell values from a | val1 | val2 | row."""
        cells = row.split('|')
        return [c.strip() for c in cells if c.strip()]

    def _chunk_code(self, text: str, options: BlockAwareOptions) -> list[ChunkRecord]:
        """Chunk a code block by lines."""
        lines = text.split('\n')
        if len(lines) <= options.code_max_lines:
            return [ChunkRecord(content=text, chunk_index=0)]

        records: list[ChunkRecord] = []
        idx = 0
        for i in range(0, len(lines), options.code_max_lines):
            batch = lines[i:i + options.code_max_lines]
            records.append(ChunkRecord(content='\n'.join(batch), chunk_index=idx))
            idx += 1
        return records

    def _chunk_list(self, text: str, options: BlockAwareOptions) -> list[ChunkRecord]:
        """Chunk a list by item count. Small lists are kept whole."""
        lines = text.split('\n')
        items = [line for line in lines if self._is_list_item(self._trim_right(line))]

        if len(items) <= options.list_max_items:
            return [ChunkRecord(content=text, chunk_index=0)]

        # Split large lists
        records: list[ChunkRecord] = []
        idx = 0
        for i in range(0, len(items), options.list_max_items):
            batch = items[i:i + options.list_max_items]
            records.append(ChunkRecord(content='\n'.join(batch), chunk_index=idx))
            idx += 1
        return records

    def _chunk_image(self, text: str) -> list[ChunkRecord]:
        """Keep original image markup (URL preserved) as content; use the
        alt text as embedding text for search."""
        m = re.match(r'^!\[([^\]]*)\]', text)
        alt = m.group(1) if m else ""
        return [ChunkRecord(
            content=text,
            chunk_index=0,
            block_type="IMAGE",
            embedding_text=f"[图片: {alt}]" if alt else "",
        )]

    def _chunk_paragraph(self, text: str, options: BlockAwareOptions) -> list[ChunkRecord]:
        """Chunk a paragraph by character budget."""
        if not text.strip():
            return []

        target = options.target_chars
        max_chars = options.max_chars
        if len(text) <= max_chars:
            return [ChunkRecord(content=text, chunk_index=0)]

        # Split on sentence boundaries near target
        records: list[ChunkRecord] = []
        idx = 0
        start = 0
        while start < len(text):
            target_end = min(start + target, len(text))
            # Try to break at sentence boundary
            end = self._find_sentence_boundary(text, start, target_end, max_chars)

            if end <= start:
                end = target_end

            content = text[start:end].strip()
            if content:
                records.append(ChunkRecord(content=content, chunk_index=idx))
                idx += 1
            start = end

        return records

    def _find_sentence_boundary(self, text: str, start: int, target_end: int, max_chars: int) -> int:
        """Find the best sentence boundary near target_end, not exceeding max_chars."""
        search_end = min(start + max_chars, len(text))

        # Look for sentence-ending punctuation + space/newline
        for i in range(target_end, search_end):
            if i <= start:
                continue
            if text[i - 1] in ('。', '！', '？', '.', '!', '?'):
                if i >= len(text) or text[i].isspace() or text[i] == '\n':
                    return i
                # Also break if followed by a capital letter or CJK character
                if i < len(text) and (text[i].isupper() or ord(text[i]) > 0x2E80):
                    return i

        # Fall back to newline
        for i in range(target_end, search_end):
            if text[i] == '\n':
                return i + 1

        return target_end

    # ------------------------------------------------------------------
    # 3) ChunkPacker — merge adjacent small chunks
    # ------------------------------------------------------------------

    # Only free-flowing block types may be merged; TABLE/CODE/HEADING are
    # atomic — they never merge and they break the merge chain (ragent
    # ChunkPacker MERGEABLE_TYPES semantics).
    _MERGEABLE_TYPES = frozenset({"PARAGRAPH", "LIST", "IMAGE", ""})

    def _pack_chunks(self, chunks: list[ChunkRecord], options: BlockAwareOptions) -> list[ChunkRecord]:
        """Merge adjacent small mergeable chunks to avoid fragmentation."""
        if len(chunks) <= 1:
            return chunks

        min_size = options.min_chars
        max_size = options.max_chars

        packed: list[ChunkRecord] = []
        buffer: list[ChunkRecord] = []

        for c in chunks:
            # Atomic block: flush buffer, pass through untouched
            if c.block_type not in self._MERGEABLE_TYPES:
                if buffer:
                    packed.append(self._merge_buffer(buffer))
                    buffer = []
                packed.append(c)
                continue

            c_len = len(c.content)
            if c_len < min_size and buffer:
                buffer.append(c)
                combined_len = sum(len(b.content) for b in buffer)
                if combined_len >= min_size or combined_len > max_size:
                    packed.append(self._merge_buffer(buffer))
                    buffer = []
            elif c_len < min_size:
                buffer.append(c)
            else:
                if buffer:
                    packed.append(self._merge_buffer(buffer))
                    buffer = []
                packed.append(c)

        if buffer:
            packed.append(self._merge_buffer(buffer))

        return packed

    def _merge_buffer(self, buf: list[ChunkRecord]) -> ChunkRecord:
        """Merge a buffer of small chunks into one, preserving metadata:
        embedding texts are joined (explicit text wins over content),
        outline_path keeps the longest common prefix."""
        if len(buf) == 1:
            return buf[0]
        merged = '\n\n'.join(b.content for b in buf)

        # Join embedding texts only if at least one chunk has an explicit one
        if any(b.embedding_text for b in buf):
            merged_embedding = '\n\n'.join(
                b.embedding_text or b.content for b in buf
            )
        else:
            merged_embedding = ""

        # Longest common outline prefix
        common_path = list(buf[0].outline_path)
        for b in buf[1:]:
            limit = min(len(common_path), len(b.outline_path))
            k = 0
            while k < limit and common_path[k] == b.outline_path[k]:
                k += 1
            common_path = common_path[:k]

        block_types = {b.block_type for b in buf if b.block_type}
        block_type = block_types.pop() if len(block_types) == 1 else "PARAGRAPH"

        return ChunkRecord(
            content=merged,
            chunk_index=0,
            block_type=block_type,
            embedding_text=merged_embedding,
            outline_path=common_path,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _trim_right(s: str) -> str:
        r = len(s)
        while r > 0 and s[r - 1].isspace() and s[r - 1] not in ('\n', '\r'):
            r -= 1
        return s[:r]
