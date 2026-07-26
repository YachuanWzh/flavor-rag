# Complex PDF Multimodal Ingestion

Status: Accepted for implementation

## 1. Goal

Upgrade PDF ingestion from plain-text extraction to a layout-aware, multimodal
pipeline that preserves paragraphs, headings, tables, images, page provenance,
and retrieval-specific representations.

The implementation must work for both ingestion entry points:

1. The legacy `IngestionPipeline` used by knowledge bases without a bound
   pipeline.
2. The DAG `ParserNode -> ChunkerNode -> IndexerNode` flow.

## 2. Required outcomes

- PDF parsing returns an ordered structured document instead of only a string.
- Every block retains one-based page numbers and PDF bounding boxes.
- Tables retain rows and columns, not only flattened text.
- Tables split across consecutive pages are reconstructed as one logical table.
- A repeated header on a continuation page is stored only once.
- A headerless continuation is merged only when geometry and column value types
  provide strong evidence; ambiguous tables remain separate.
- Table chunks use Markdown for display and `column: value` rows for embedding.
- Embedded images are extracted, content-addressed, stored as assets, and
  referenced by image chunks.
- When VLM enrichment is configured, image descriptions are generated at write
  time and used as embedding text.
- Chunk metadata includes block type, page range, bounding boxes, logical table
  identity, row range, and asset references.
- Retrieval sources expose page ranges and block type.

## 3. Non-goals

- Handwriting recognition.
- Formula-to-LaTeX conversion.
- Direct visual page embeddings such as ColPali.
- LLM-based repair of malformed tables.
- Query-time numerical aggregation over tables.

These can be added later without changing the structured document contract.

## 4. Structured document contract

### 4.1 Document

`StructuredPdfDocument`

- `document_id`
- `source_file`
- `page_count`
- ordered `blocks`
- extracted `assets`
- parser metadata and version

### 4.2 Block

`PdfBlock`

- stable `block_id`
- `block_type`: `HEADING`, `PARAGRAPH`, `TABLE`, or `IMAGE`
- `page_start`, `page_end`
- one or more page-specific bounding boxes
- display `content`
- retrieval `embedding_text`
- optional outline path
- optional table headers, rows, and row page provenance
- optional asset identifiers
- parser and merge metadata

Page numbers are one-based. Bounding boxes use PDF coordinates
`[x0, top, x1, bottom]`.

### 4.3 Asset

`PdfAsset`

- content-addressed `asset_id`
- page number and optional bounding box
- MIME type and original filename
- SHA-256 hash
- extracted bytes during ingestion
- optional VLM description
- storage key and URL after persistence

## 5. Cross-page table reconstruction

Two table fragments may merge only when all base conditions hold:

1. They are on consecutive pages.
2. The first fragment ends in the bottom 22% of its page.
3. The second fragment starts in the top 22% of the next page.
4. Both have the same column count.
5. Their normalized horizontal spans overlap by at least 94%.

After the base conditions, one semantic condition is required:

- Repeated-header continuation: normalized headers have at least 90% similarity.
- Headerless continuation: the candidate first row matches the established
  per-column data-type signature with at least 80% confidence, and at least one
  column has a non-text data type.

A new table caption immediately above the candidate fragment (for example
`表 2` or `Table 2`) is a hard veto even when the headers match.

The conservative headerless rule intentionally prefers a false negative over
merging two unrelated tables.

When fragments merge:

- the first fragment's headers remain authoritative;
- a repeated continuation header is removed;
- a headerless candidate's provisional header row is restored as data;
- row-to-page provenance is retained;
- `page_start/page_end`, bounding boxes, and merge confidence are retained;
- three or more consecutive fragments can merge into the same logical table.

## 6. Chunking

### Paragraphs and headings

- Heading blocks update the current outline path.
- Adjacent paragraph blocks are packed up to the configured character budget.
- Packed chunks retain the union of source block IDs, pages, and bounding boxes.

### Tables

- A logical table is never split by raw character offsets.
- Large tables split only on row boundaries.
- Every table chunk repeats the authoritative headers.
- Display content is a Markdown table.
- Embedding content is section context plus `header: value` pairs.
- Each chunk records logical table ID, logical page range, row range, and
  cross-page status.

### Images

- Every image is an atomic chunk.
- Display content contains the VLM description and an asset reference.
- Embedding content is the VLM description without storage URL noise.
- If VLM is disabled, caption/filename is used as a low-quality fallback and the
  chunk is marked `description_status=missing`.

## 7. Persistence

`t_knowledge_chunk` gains:

- `embedding_content`
- `block_type`
- `page_start`
- `page_end`
- `bbox_json`
- `metadata_json`

`t_knowledge_asset` stores extracted PDF assets and their provenance.

Dense embeddings are generated from `embedding_content` when present, while
Milvus and PostgreSQL retain display `content`. Elasticsearch indexes both
display and embedding content.

PostgreSQL schema changes are applied by Alembic. The zero-setup development
SQLite database performs an explicit additive compatibility upgrade at startup,
because SQLAlchemy `create_all` does not add columns to existing tables. This
upgrade must inspect the current schema before each `ALTER TABLE`, be safe to
run repeatedly, and must not be used for production database migration.

Asset upload is content-addressed:

`assets/{kb_id}/{doc_id}/{sha256-prefix}.{extension}`

An asset upload failure fails the indexing node. The system must not report a
successful multimodal ingestion with missing image assets.

## 8. VLM contract

The VLM client uses an OpenAI-compatible multimodal chat endpoint and is
controlled by:

- `VLM_ENABLED`
- `VLM_BASE_URL`
- `VLM_API_KEY`
- `VLM_MODEL`
- `VLM_MAX_CONCURRENCY`

VLM calls happen only during ingestion. Parsing remains usable without a VLM,
but image chunks are explicitly marked as not enriched.

## 9. Acceptance tests

1. Two page-boundary table fragments with repeated headers become one table and
   the repeated header is removed.
2. A three-page table becomes one logical table.
3. A headerless numeric continuation is restored and merged.
4. An ambiguous text-only headerless table is not merged.
5. Tables away from page boundaries are not merged.
6. Tables with different column counts or horizontal geometry are not merged.
7. Table chunks contain Markdown display content, key-value embedding content,
   row ranges, and page ranges.
8. An extracted image becomes an atomic image chunk with asset provenance and a
   mocked VLM description.
9. A generated two-page PDF with a repeated table header is parsed as one
   logical table.
10. Legacy and DAG ingestion paths both select the structured PDF parser.
11. An existing SQLite chunk table is upgraded with all multimodal columns at
    startup, and running the initializer again makes no schema changes.

## 10. Operational signals

The parser and indexer must log:

- page, block, table, cross-page table, and image counts;
- tables merged and tables rejected as ambiguous;
- VLM success/failure counts;
- asset upload count and bytes;
- parse, VLM, asset persistence, embedding, and index durations.
