# Enterprise RAG Platform — SDD

Status: implementation target  
Date: 2026-07-26

## 1. Scope and invariants

This increment turns the current single-tenant RAG demo into a controlled,
observable retrieval platform. The following are non-negotiable invariants:

1. A principal may only discover a tenant, knowledge base, document, chunk,
   asset, trace, conversation, or evaluation record they are authorized to
   read.
2. Authorization is enforced before retrieval and again after every external
   search channel. An unavailable filter or stale external index must fail
   closed.
3. Deleting or replacing a document immediately makes its chunks invisible in
   PostgreSQL-backed security filtering, then synchronizes Milvus,
   Elasticsearch, GraphRAG, and object storage through an auditable cleanup
   operation.
4. URL ingestion rejects SSRF targets, unsupported schemes, redirect escapes,
   oversized responses, and unsupported content before persistence.
5. Every RAG run has one trace ID propagated through rewrite, decomposition,
   retrieval, fusion, rerank, rejection, tools, generation, and failure paths.
   Secrets, full prompts, credentials, and unrestricted document content are
   not stored in traces.
6. Retrieval has explicit candidate, channel, latency, context-character, and
   tool-step budgets. Optional dependencies fail through circuit breakers and
   deterministic fallbacks.
7. Agentic RAG is bounded, allowlisted, read-only by default, tenant-scoped,
   and unable to invoke arbitrary MCP or SQL operations.
8. Graph and visual retrieval remain gated by measured evaluation uplift, not
   by feature availability.

## 2. Identity and ACL model

Every `User`, `KnowledgeBase`, `KnowledgeDocument`, `KnowledgeChunk`,
`KnowledgeAsset`, `Conversation`, and trace run carries `tenant_id`.
Users and resources may carry `department_id`.

`t_resource_acl` grants `READ`, `WRITE`, or `ADMIN` to one subject:

- `USER:{user_id}`
- `DEPARTMENT:{department_id}`
- `ROLE:{role}`

The resource is `KNOWLEDGE_BASE` or `DOCUMENT`. Permissions are monotonic:
`ADMIN > WRITE > READ`.

Access is allowed when all conditions hold:

- resource tenant equals principal tenant, unless the principal is the
  explicitly configured `system_admin`;
- the principal owns the resource, is a tenant admin, matches the resource
  department, or has a matching ACL grant;
- a document grant cannot cross its parent KB boundary.

Document ACL may narrow KB access but never broaden access to an unauthorized
KB. External channels over-fetch; PostgreSQL resolves live chunk IDs and applies
tenant, KB, document ACL, enabled, and soft-delete predicates before context is
built.

## 3. Ingestion and deletion

URL fetch performs scheme and credential validation, DNS resolution, private
and reserved address rejection, manual redirect validation, streamed size
limits, MIME/extension validation, and content hashing. The exact persisted
path is passed to ingestion.

Deletion/reprocessing:

1. resolve and authorize the parent KB/document;
2. collect live chunk and asset IDs;
3. mark relational records deleted in the transaction;
4. enqueue/execute idempotent cleanup for Milvus, Elasticsearch, graph, and
   object assets;
5. record per-channel status and retry metadata;
6. retrieval security post-filtering guarantees immediate invisibility even
   while an optional external channel is retrying.

## 4. Structured PDF

The intermediate representation consists of typed blocks:

- heading, paragraph, list, table, image, formula, header/footer;
- one-based page range;
- page-space bounding boxes and normalized coordinates;
- extraction method (`native`, `ocr`, `vlm`) and confidence;
- table row-to-page provenance and asset references.

Native text/layout extraction runs first. Pages with insufficient native text
are rendered and sent to an injected OCR provider. OCR blocks retain returned
coordinates and confidence. Images are described by VLM at write time. The
existing conservative cross-page table merger remains authoritative.

Every returned source contains document ID, chunk ID, page range, bounding
boxes, block type, score, and permitted asset references.

## 5. Retrieval governance

Rewrite and optional decomposition produce a bounded set of subqueries.
Vector, BM25, and gated graph channels run concurrently under per-channel
timeouts. Failures are isolated by circuit breaker.

Results are fused, deduplicated, security-filtered, reranked, thresholded, and
packed into a character budget. Cross-encoder scores replace candidate scores.
If no result clears `min_relevance_score`, the pipeline returns
`answerable=false` with a stable refusal reason; generation must not invent an
answer from an empty context.

## 6. Conversation, tools, and controlled agent

Older turns are summarized into `Conversation.summary`; recent messages remain
verbatim. The summary is included in rewrite/decomposition but is not treated
as retrieved evidence.

Assistant messages persist their retrieval execution metadata alongside the
answer: bounded agent steps, effective RAG modes, and per-channel retrieval
status/counts. The conversation history API returns the same camel-case shape
as the live SSE events so Agent and Graph evidence badges survive refresh and
session reload. Legacy messages without metadata remain valid and render
without badges.

Tools:

- SQL: one statement, `SELECT`/`WITH` only, allowlisted tables/views, tenant
  predicate required, row and time limits, no comments or multi-statements.
- MCP: only configured server/tool pairs, JSON-schema input validation,
  read-only classification, tenant context injection, timeout and output limit.

The agent planner can choose `retrieve`, `sql`, `mcp`, or `finish`. It has fixed
step/tool/latency budgets, repeated-call detection, structured observations,
and no dynamic tool registration. Tool outputs are evidence and retain
provenance. Any side-effecting tool is rejected.

## 7. Trace and evaluation

Trace nodes store timing, counts, status, channel/tool names, budget remaining,
rejection reason, and redacted metadata. Trace APIs enforce tenant scope.

The minimal JSONL evaluation set contains:

- direct lookup;
- lexical identifier lookup;
- paraphrase;
- multi-document/multi-hop;
- unanswerable/refusal;
- ACL-denied query;
- table/page citation;
- image-dependent query when such documents exist.

The runner reports Recall@K, MRR, nDCG@K, citation accuracy, ACL leakage,
refusal precision/recall, latency percentiles, and optional-channel uplift.

GraphRAG is enabled for a corpus only when labeled multi-hop failures are
material and graph retrieval provides configured Recall@K uplift within latency
budget. Visual retrieval is enabled only when image/table-dependent questions
are material and visual retrieval improves them. With no labeled relevant
examples, the decision is `HOLD`, never `ENABLE`.

## 8. Acceptance tests

1. URL upload passes the persisted path and blocks private-network redirects.
2. Trace nodes use the API-created trace ID.
3. Deleted chunks are removed from each configured index and are immediately
   excluded by relational security filtering.
4. Cross-tenant and unauthorized department/document access returns no data.
5. Every retrieval channel receives or is followed by equivalent ACL filters.
6. A scanned page invokes OCR and returns coordinate-bearing OCR blocks.
7. Retrieval channels execute concurrently and respect time/candidate budgets.
8. Rerank scores are preserved; low relevance produces a refusal.
9. Circuit breakers open after configured failures and recover after cooldown.
10. Conversation summary replaces only older turns.
11. SQL mutation/multi-statement and unregistered MCP calls are rejected.
12. The controlled agent cannot exceed its step budget or call a non-allowlisted
    tool.
13. Evaluation reports zero tolerated ACL leakage and emits graph/visual
    `ENABLE`, `HOLD`, or `DISABLE` decisions from labeled measurements.
