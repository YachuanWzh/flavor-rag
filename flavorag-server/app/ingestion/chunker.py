"""Document chunker — splits text into chunks using configurable strategies.

Direct Python translation of ragent Java chunking subsystem:
  - FIXED_WINDOW (fixed_size): 固定窗口切分，按 chunkSize 滑动，overlapSize 重叠
  - SEMANTIC (structure_aware): 语义感知切分，保留 Markdown 结构（标题/代码/图片/段落）
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ============================================================================
# Strategy / Options (Java: ChunkingMode, ChunkingOptions)
# ============================================================================

class ChunkStrategy(Enum):
    FIXED_WINDOW = ("fixed_size", "固定窗口")
    SEMANTIC = ("structure_aware", "语义切分")

    def __init__(self, value: str, label: str):
        self._value_ = value
        self.label = label

    @classmethod
    def from_value(cls, value: str) -> "ChunkStrategy":
        if not value:
            raise ValueError("Chunk strategy value must not be empty")
        normalized = value.strip().lower().replace("-", "_")
        # Direct match
        for s in cls:
            if s.value == normalized:
                return s
        # User-facing names: FIXED_WINDOW, SEMANTIC
        name_map = {
            "fixed_window": cls.FIXED_WINDOW,
            "semantic": cls.SEMANTIC,
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
    content: str
    chunk_index: int

    @property
    def char_count(self) -> int:
        return len(self.content)

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
        }


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
        else:
            records = _StructureAwareChunker().chunk(text, options)

        return [r.to_dict() for r in records]


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
