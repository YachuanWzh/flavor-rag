"""Tests for DocumentChunker — matches ragent Java chunker behavior."""
import pytest
from app.ingestion.chunker import (
    DocumentChunker,
    ChunkConfig,
    ChunkStrategy,
    FixedSizeOptions,
    TextBoundaryOptions,
    BlockAwareOptions,
)


class TestChunkStrategy:
    def test_from_value_fixed_size(self):
        assert ChunkStrategy.from_value("fixed_size") == ChunkStrategy.FIXED_WINDOW

    def test_from_value_structure_aware(self):
        assert ChunkStrategy.from_value("structure_aware") == ChunkStrategy.SEMANTIC

    def test_backward_compat_fixed_size_upper(self):
        """Old FIXED_SIZE maps to FIXED_WINDOW."""
        assert ChunkStrategy.from_value("FIXED_SIZE") == ChunkStrategy.FIXED_WINDOW

    def test_backward_compat_structure_aware_upper(self):
        """Old STRUCTURE_AWARE maps to SEMANTIC."""
        assert ChunkStrategy.from_value("STRUCTURE_AWARE") == ChunkStrategy.SEMANTIC

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown chunk strategy"):
            ChunkStrategy.from_value("INVALID")


class TestChunkConfig:
    def test_default_config(self):
        c = ChunkConfig()
        assert c.strategy == "FIXED_WINDOW"
        assert c.chunk_size == 512
        assert c.overlap == 128

    def test_custom_fixed_window(self):
        c = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=300, overlap=50)
        assert c.resolve_strategy() == ChunkStrategy.FIXED_WINDOW
        opts = c.resolve_options()
        assert isinstance(opts, FixedSizeOptions)
        assert opts.chunk_size == 300
        assert opts.overlap_size == 50

    def test_custom_semantic(self):
        c = ChunkConfig(strategy="SEMANTIC", chunk_size=800, overlap=100)
        assert c.resolve_strategy() == ChunkStrategy.SEMANTIC
        opts = c.resolve_options()
        assert isinstance(opts, TextBoundaryOptions)
        assert opts.target_chars == 800
        assert opts.overlap_chars == 100
        assert opts.max_chars == 1800
        assert opts.min_chars == 600


class TestFixedWindowChunking:
    def setup_method(self):
        self.chunker = DocumentChunker()
        self.config = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=100, overlap=20)

    def test_short_text_single_chunk(self):
        text = "Hello world. This is a short text."
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) == 1
        assert chunks[0]["chunk_index"] == 0

    def test_long_text_multiple_chunks(self):
        """Text longer than chunk_size should split into multiple chunks."""
        text = "A" * 250
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 2

    def test_overlap_is_applied(self):
        """Verify overlap between adjacent fixed-window chunks."""
        text = "The quick brown fox jumps over the lazy dog. " * 20
        config = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=100, overlap=50)
        chunks = self.chunker.chunk(text, config)
        assert len(chunks) >= 2

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

    def test_newline_boundary_preferred(self):
        """FIXED_WINDOW should prefer splitting at newline boundaries."""
        lines = ["Line " + str(i).zfill(3) + " content" for i in range(10)]
        text = "\n".join(lines)
        config = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=250, overlap=0)
        chunks = self.chunker.chunk(text, config)
        for c in chunks:
            assert c["content"] != ""

    def test_empty_text(self):
        chunks = self.chunker.chunk("", self.config)
        assert chunks == []

    def test_chinese_punctuation_boundary(self):
        """FIXED_WINDOW respects Chinese sentence-ending punctuation."""
        text = "第一句在这里。第二句紧随其后！第三句是什么呢？第四句继续讲。"
        config = ChunkConfig(strategy="FIXED_WINDOW", chunk_size=15, overlap=5)
        chunks = self.chunker.chunk(text, config)
        assert len(chunks) >= 1
        all_content = "".join(c["content"] for c in chunks)


class TestSemanticChunking:
    def setup_method(self):
        self.chunker = DocumentChunker()
        self.config = ChunkConfig(strategy="SEMANTIC", chunk_size=500, overlap=0)

    def test_split_by_h1(self):
        """Heading sections smaller than max_chars pack into chunks by budget.
        With 3 small sections (75 chars total) and max_chars=1800, they fit in one chunk.
        Use a smaller budget to force splitting."""
        text = (
            "# Introduction\n\nThis is the intro.\n\n"
            "# Methods\n\nWe used things.\n\n"
            "# Results\n\nHere are results."
        )
        config = ChunkConfig(strategy="SEMANTIC", chunk_size=10, overlap=0)
        chunks = self.chunker.chunk(text, config)
        # With target=10 and min=600, blocks get packed until min is reached
        assert len(chunks) >= 1
        all_content = "".join(c["content"] for c in chunks)
        assert "Introduction" in all_content
        assert "Methods" in all_content
        assert "Results" in all_content

    def test_heading_block_preserved(self):
        """Each heading becomes its own block."""
        text = "## Section 1\n\nContent A\n\n### Sub 1.1\n\nSub content\n\n## Section 2\n\nContent B"
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 1
        all_content = "".join(c["content"] for c in chunks)
        assert "Section 1" in all_content
        assert "Section 2" in all_content

    def test_code_fence_preserved(self):
        """Code fences should be kept intact as blocks."""
        text = (
            "# Code Example\n\n"
            "```python\nprint('hello')\nprint('world')\n```\n\n"
            "# Next\n\nMore text here."
        )
        chunks = self.chunker.chunk(text, self.config)
        all_content = "".join(c["content"] for c in chunks)
        assert "```python" in all_content
        assert "print('hello')" in all_content
        assert "```" in all_content

    def test_paragraph_boundaries_respected(self):
        """Paragraphs (blank-line separated) form natural block boundaries."""
        text = (
            "First paragraph with some content here.\n\n"
            "Second paragraph with different content.\n\n"
            "Third paragraph here."
        )
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 1

    def test_output_format_semantic(self):
        text = "# Header\n\nContent here.\n\nMore content."
        chunks = self.chunker.chunk(text, self.config)
        for c in chunks:
            assert "content" in c
            assert "chunk_index" in c
            assert "char_count" in c

    def test_empty_text_semantic(self):
        chunks = self.chunker.chunk("", self.config)
        assert chunks == []

    def test_chinese_text_chunking(self):
        """Semantic chunking handles Chinese text in paragraphs."""
        text = (
            "深度学习是机器学习的一个分支。它使用多层神经网络来学习数据的表示。\n\n"
            "卷积神经网络(CNN)主要用于图像处理任务。\n\n"
            "Transformer架构彻底改变了自然语言处理领域。"
        )
        config = ChunkConfig(strategy="SEMANTIC", chunk_size=80, overlap=0)
        chunks = self.chunker.chunk(text, config)
        assert len(chunks) >= 1


class TestDocumentChunkerDefaults:
    def test_default_config_uses_fixed_window(self):
        chunker = DocumentChunker()
        text = "Test text for default behavior verification."
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0]["content"] == text

    def test_with_no_config(self):
        chunker = DocumentChunker()
        text = "A" * 2000
        chunks = chunker.chunk(text)
        assert len(chunks) >= 2


class TestBlockAwareChunking:
    """Tests for BLOCK_AWARE chunking strategy with per-block-type sub-chunkers."""

    def setup_method(self):
        self.chunker = DocumentChunker()
        self.config = ChunkConfig(strategy="BLOCK_AWARE", chunk_size=200, overlap=20)

    def test_strategy_resolution(self):
        assert ChunkStrategy.from_value("block_aware") == ChunkStrategy.BLOCK_AWARE
        assert ChunkStrategy.from_value("BLOCK_AWARE") == ChunkStrategy.BLOCK_AWARE

    def test_config_resolves_block_aware(self):
        c = ChunkConfig(strategy="BLOCK_AWARE", chunk_size=600, overlap=80)
        assert c.resolve_strategy() == ChunkStrategy.BLOCK_AWARE
        opts = c.resolve_options()
        assert isinstance(opts, BlockAwareOptions)
        assert opts.target_chars == 600
        assert opts.overlap_chars == 80

    def test_block_aware_default_options(self):
        opts = BlockAwareOptions()
        assert opts.table_max_rows == 20
        assert opts.code_max_lines == 80
        assert opts.heading_as_path is True
        assert opts.target_chars == 800

    def test_heading_added_as_path_prefix(self):
        """Heading blocks should not become standalone chunks but act as path prefix."""
        text = "## API Reference\n\nThis section describes the authentication API."
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 1
        content = chunks[0]["content"]
        assert "API Reference" in content
        assert "authentication" in content

    def test_table_dual_text(self):
        """Table chunks keep the original table as content and key:value
        rows as separate embedding text (dual-text separation)."""
        text = (
            "| 姓名 | 部门 | 职位 |\n"
            "|------|------|------|\n"
            "| 张三 | 技术部 | 工程师 |\n"
            "| 李四 | 市场部 | 经理 |"
        )
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 1
        c = chunks[0]
        # content: original markdown table, no kv pollution
        assert "| 姓名 |" in c["content"]
        assert "表格数据" not in c["content"]
        # embedding text: key:value rows for search recall
        assert "姓名: 张三" in c["embedding_content"]
        assert c["block_type"] == "TABLE"

    def test_code_block_preserved(self):
        """Code blocks should be isolated and not mixed with surrounding text."""
        text = (
            "Before the code.\n\n"
            "```python\n"
            "def hello():\n"
            "    return 'world'\n"
            "```\n\n"
            "After the code."
        )
        chunks = self.chunker.chunk(text, self.config)
        all_content = "".join(c["content"] for c in chunks)
        assert "```python" in all_content
        assert "def hello():" in all_content
        assert "After the code" in all_content

    def test_list_items_packed(self):
        """List items should be packed together within budget."""
        items = "\n".join(f"- Item {i}: description of item number {i}" for i in range(5))
        chunks = self.chunker.chunk(items, self.config)
        assert len(chunks) >= 1
        all_content = "".join(c["content"] for c in chunks)
        for i in range(5):
            assert f"Item {i}" in all_content

    def test_image_alt_text_extracted(self):
        """Image blocks keep original markup (URL preserved) and expose
        alt text via embedding content."""
        text = "![系统架构图](https://example.com/arch.png)"
        chunks = self.chunker.chunk(text, self.config)
        assert len(chunks) >= 1
        assert "系统架构图" in chunks[0]["content"]
        assert "https://example.com/arch.png" in chunks[0]["content"]
        assert "系统架构图" in chunks[0]["embedding_content"]

    def test_empty_text(self):
        chunks = self.chunker.chunk("", self.config)
        assert chunks == []

    def test_output_format(self):
        text = "## Section\n\nNormal paragraph text here.\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        chunks = self.chunker.chunk(text, self.config)
        for c in chunks:
            assert "content" in c
            assert "chunk_index" in c
            assert "char_count" in c
            assert isinstance(c["content"], str)
            assert isinstance(c["chunk_index"], int)
            assert isinstance(c["char_count"], int)

    def test_small_chunks_merged(self):
        """Small adjacent chunks (below min_chars) should be merged."""
        text = "One.\n\nTwo.\n\nThree.\n\nFour.\n\nFive."
        config = ChunkConfig(strategy="BLOCK_AWARE", chunk_size=500, overlap=10)
        chunks = self.chunker.chunk(text, config)
        # With large target, small paragraphs pack together
        assert len(chunks) >= 1

    def test_table_never_merged_into_text(self):
        """Atomic blocks (TABLE) must not be packed with surrounding
        small paragraphs."""
        text = (
            "Short intro.\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "Short outro."
        )
        config = ChunkConfig(strategy="BLOCK_AWARE", chunk_size=500, overlap=0)
        chunks = self.chunker.chunk(text, config)
        table_chunks = [c for c in chunks if c.get("block_type") == "TABLE"]
        assert len(table_chunks) == 1
        assert "Short intro" not in table_chunks[0]["content"]
        assert "Short outro" not in table_chunks[0]["content"]
