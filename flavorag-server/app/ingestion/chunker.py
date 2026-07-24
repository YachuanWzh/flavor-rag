"""Document chunker — splits text into chunks using configurable strategies."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ChunkConfig:
    """Configuration for document chunking.

    Attributes:
        strategy: Chunking strategy — "FIXED_SIZE" or "STRUCTURE_AWARE".
        chunk_size: Target number of characters per chunk.
        overlap: Number of characters to overlap between adjacent chunks.
        min_chunk_size: Minimum character count for a chunk; smaller chunks
            are merged into the previous chunk.
    """
    strategy: str = "FIXED_SIZE"
    chunk_size: int = 800
    overlap: int = 100
    min_chunk_size: int = 50


class DocumentChunker:
    """Splits document text into chunks for embedding and indexing.

    Usage:
        chunker = DocumentChunker()
        chunks = chunker.chunk(text, ChunkConfig(strategy="FIXED_SIZE"))
    """

    def chunk(self, text: str, config: ChunkConfig | None = None) -> list[dict]:
        """Split *text* into chunks according to *config*.

        Returns a list of dicts, each with keys:
            - content (str): the chunk text
            - chunk_index (int): zero-based index
            - char_count (int): number of characters in content
        """
        if config is None:
            config = ChunkConfig()

        if not text:
            return []

        if config.strategy == "FIXED_SIZE":
            chunks = self._chunk_fixed_size(text, config)
        elif config.strategy == "STRUCTURE_AWARE":
            chunks = self._chunk_structure_aware(text, config)
        else:
            raise ValueError(f"Unknown chunk strategy: {config.strategy}")

        # Post-process: merge small trailing chunk
        return self._post_process(chunks, config)

    # ------------------------------------------------------------------
    # FIXED_SIZE strategy
    # ------------------------------------------------------------------

    def _chunk_fixed_size(self, text: str, config: ChunkConfig) -> list[dict]:
        """Split text by fixed character count, preferring paragraph breaks."""
        chunks: list[dict] = []
        start = 0
        text_len = len(text)
        index = 0

        while start < text_len:
            end = min(start + config.chunk_size, text_len)

            if end == text_len:
                # Last chunk — take remainder
                content = text[start:]
            else:
                # Try to find a clean break point near the chunk boundary.
                # Priority: paragraph break (\n\n) > line break (\n) > exact cut.
                search_start = max(start, end - config.chunk_size // 2)
                segment = text[search_start:end + 1]

                # Look for paragraph break closest to (but not past) end
                para_break = segment.rfind("\n\n")
                if para_break != -1:
                    cut = search_start + para_break + 2  # include the \n\n
                else:
                    line_break = segment.rfind("\n")
                    if line_break != -1:
                        cut = search_start + line_break + 1
                    else:
                        cut = end

                # Don't cut too early — if the break is more than half chunk_size away, force cut
                if cut - start < config.chunk_size // 2:
                    cut = end

                content = text[start:cut]

            if content:
                chunks.append({
                    "content": content,
                    "chunk_index": index,
                    "char_count": len(content),
                })
                index += 1

            # Advance: move past this chunk, then back up by overlap.
            advance = len(content)
            next_start = start + advance
            if next_start >= text_len:
                break  # fully consumed
            start = next_start - config.overlap if config.overlap < advance else next_start

        return chunks

    # ------------------------------------------------------------------
    # STRUCTURE_AWARE strategy
    # ------------------------------------------------------------------

    _HEADER_PATTERN = re.compile(r"^(#{1,6})\s+", re.MULTILINE)

    def _chunk_structure_aware(self, text: str, config: ChunkConfig) -> list[dict]:
        """Split text by Markdown headings, then by blank lines within large sections."""
        # Find all header positions
        header_matches = list(self._HEADER_PATTERN.finditer(text))
        if not header_matches:
            # No headers — split by blank-line-separated paragraphs
            return self._chunk_by_paragraphs(text, config)

        chunks: list[dict] = []
        for i, match in enumerate(header_matches):
            start = match.start()
            end = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(text)
            section = text[start:end].strip()

            if not section:
                continue

            # If section is small enough, keep it as one chunk
            if len(section) <= config.chunk_size:
                chunks.append({
                    "content": section,
                    "chunk_index": len(chunks),
                    "char_count": len(section),
                })
            else:
                # Large section — split further by paragraphs
                sub_chunks = self._chunk_by_paragraphs(section, config)
                for sc in sub_chunks:
                    sc["chunk_index"] = len(chunks)
                    chunks.append(sc)

        return chunks

    def _chunk_by_paragraphs(self, text: str, config: ChunkConfig) -> list[dict]:
        """Split text by blank lines (\\n\\n) into individual paragraph chunks.

        Each blank-line-separated paragraph becomes its own chunk, preserving
        semantic boundaries. If a single paragraph exceeds *chunk_size* it is
        further split by FIXED_SIZE.
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return []

        chunks: list[dict] = []
        for para in paragraphs:
            if len(para) <= config.chunk_size:
                chunks.append({
                    "content": para,
                    "chunk_index": 0,
                    "char_count": len(para),
                })
            else:
                # Single paragraph too large — sub-split with FIXED_SIZE
                sub = self._chunk_fixed_size(para, config)
                chunks.extend(sub)

        for i, c in enumerate(chunks):
            c["chunk_index"] = i

        return chunks

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    def _post_process(self, chunks: list[dict], config: ChunkConfig) -> list[dict]:
        """Merge chunks smaller than min_chunk_size into the previous chunk."""
        if not chunks or config.min_chunk_size <= 0:
            return chunks

        result = [chunks[0]]
        for c in chunks[1:]:
            if c["char_count"] < config.min_chunk_size:
                # Merge into last chunk
                prev = result[-1]
                merged_content = prev["content"] + "\n\n" + c["content"]
                prev["content"] = merged_content
                prev["char_count"] = len(merged_content)
            else:
                c["chunk_index"] = len(result)
                result.append(c)

        # Re-index
        for i, c in enumerate(result):
            c["chunk_index"] = i

        return result
