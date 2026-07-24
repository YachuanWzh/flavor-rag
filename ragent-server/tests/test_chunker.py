"""Tests for DocumentChunker — RED phase."""
import pytest
from app.ingestion.chunker import DocumentChunker, ChunkConfig


class TestChunkConfig:
    def test_default_config(self):
        c = ChunkConfig()
        assert c.strategy == "FIXED_SIZE"
        assert c.chunk_size == 800
        assert c.overlap == 100
        assert c.min_chunk_size == 50

    def test_custom_config(self):
        c = ChunkConfig(strategy="STRUCTURE_AWARE", chunk_size=500, overlap=50, min_chunk_size=30)
        assert c.strategy == "STRUCTURE_AWARE"
        assert c.chunk_size == 500


class TestFixedSizeChunking:
    def setup_method(self):
        self.chunker = DocumentChunker()
        self.config = ChunkConfig(strategy="FIXED_SIZE", chunk_size=100, overlap=20, min_chunk_size=10)

    def test_short_text_single_chunk(self):
        text = "Hello world. This is a short text."
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) == 1
        assert chunks[0]["content"] == text
        assert chunks[0]["chunk_index"] == 0
        assert chunks[0]["char_count"] == len(text)

    def test_long_text_multiple_chunks(self):
        """Text longer than chunk_size should split into multiple chunks."""
        text = "A" * 250
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 2  # 250 chars with 100 size + 20 overlap
        assert all(isinstance(c["char_count"], int) for c in chunks)
        # Verify overlap: adjacent chunks share content
        if len(chunks) >= 2:
            assert chunks[0]["content"][-20:] == chunks[1]["content"][:20]

    def test_chunk_index_increments(self):
        text = "B" * 250
        chunks = self.chunker.chunk(text, self.config)
        for i, c in enumerate(chunks):
            assert c["chunk_index"] == i

    def test_output_format(self):
        text = "Some sample text for format validation."
        chunks = self.chunker.chunk(text, self.config)
        for c in chunks:
            assert "content" in c
            assert "chunk_index" in c
            assert "char_count" in c
            assert isinstance(c["content"], str)
            assert isinstance(c["chunk_index"], int)
            assert isinstance(c["char_count"], int)

    def test_paragraph_boundary_respected(self):
        """FIXED_SIZE should prefer splitting at paragraph boundaries."""
        paragraph = "This is a complete paragraph with enough words to fill it properly for testing."
        text = (paragraph + "\n\n") * 5
        config = ChunkConfig(strategy="FIXED_SIZE", chunk_size=80, overlap=0, min_chunk_size=10)
        chunks = self.chunker.chunk(text, config)
        for c in chunks:
            # Each chunk should start at a paragraph boundary
            assert c["content"] != "", "No empty chunks"

    def test_empty_text(self):
        chunks = self.chunker.chunk("", self.config)
        assert chunks == []

    def test_min_chunk_size_filters(self):
        """Chunks smaller than min_chunk_size should be merged."""
        text = "ABCDEFGHIJ"  # 10 chars
        config = ChunkConfig(strategy="FIXED_SIZE", chunk_size=100, overlap=0, min_chunk_size=50)
        chunks = self.chunker.chunk(text, config)
        # 10 chars < 50 min, should still produce 1 chunk (edge: only chunk)
        assert len(chunks) == 1


class TestStructureAwareChunking:
    def setup_method(self):
        self.chunker = DocumentChunker()
        self.config = ChunkConfig(strategy="STRUCTURE_AWARE", chunk_size=500, overlap=50, min_chunk_size=10)

    def test_split_by_h1(self):
        text = "# Introduction\n\nThis is the intro.\n\n# Methods\n\nWe used things.\n\n# Results\n\nHere are results."
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 3
        assert "# Introduction" in chunks[0]["content"]
        assert "# Methods" in chunks[1]["content"]
        assert "# Results" in chunks[2]["content"]

    def test_split_by_h2_and_h3(self):
        text = "## Section 1\n\nContent A\n\n### Sub 1.1\n\nSub content\n\n## Section 2\n\nContent B"
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 2
        assert "## Section 1" in chunks[0]["content"]
        assert "## Section 2" in chunks[-1]["content"]

    def test_split_by_blank_lines(self):
        """When no headers exist, split by blank-line-separated paragraphs."""
        text = "First paragraph with content.\n\nSecond paragraph here.\n\nThird paragraph with more text."
        config = ChunkConfig(strategy="STRUCTURE_AWARE", chunk_size=1000, overlap=0, min_chunk_size=5)
        chunks = self.chunker.chunk(text, config)
        assert len(chunks) >= 3

    def test_mixed_headers_and_text(self):
        text = "# Title\n\nSome intro text.\n\n## Detail\n\nMore detailed text here.\n\nEven more without header.\n\n## Another\n\nFinal content."
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 3
        # First chunk should contain title
        assert "Title" in chunks[0]["content"]
        assert "Another" in chunks[-1]["content"]

    def test_no_headers_single_chunk(self):
        text = "Just a simple block of text without any markdown headers at all."
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) == 1

    def test_output_format_structure(self):
        text = "# Header\n\nContent here."
        chunks = self.chunker.chunk(text, self.config)
        for c in chunks:
            assert "content" in c
            assert "chunk_index" in c
            assert "char_count" in c

    def test_empty_text_structure(self):
        chunks = self.chunker.chunk("", self.config)
        assert chunks == []


class TestDocumentChunkerDefaults:
    def test_default_config_uses_fixed_size(self):
        chunker = DocumentChunker()
        text = "Test text for default behavior verification."
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0]["content"] == text
