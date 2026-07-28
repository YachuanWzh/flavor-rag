# Cross-Knowledge-Base Graph RAG — SDD

Status: implementation target  
Date: 2026-07-28

## 1. Goal

Add a permission-safe `全部` retrieval scope that searches every knowledge
base the current principal can read, requires Graph RAG for that scope, and
renders a combined interactive knowledge graph with at most 200 entities.

The feature must preserve single-knowledge-base behavior and must never turn
the global scope into a tenant-wide authorization bypass.

## 2. User-visible contract

1. Both desktop and mobile knowledge-base selectors contain `全部`.
2. The wire value for `全部` is the explicit sentinel `*`. `null` retains the
   legacy meaning and must not silently become global retrieval.
3. Selecting `全部` immediately enables Graph RAG. The Graph RAG control is
   shown as locked and cannot be disabled while `*` is selected.
4. The server independently enforces `graph_rag=true` for `kb_id=*`; a crafted
   client request with `graph_rag=false` cannot disable it.
5. The combined graph shows nodes from all readable knowledge bases, identifies
   node provenance, distinguishes cross-knowledge-base edges, supports pointer
   panning and wheel/button zoom, and exposes keyboard-focusable nodes.
6. A successful graph retrieval animates the relevant node-to-node path. The
   animation is disabled under `prefers-reduced-motion`.
7. The graph returns the actual extracted node count until the hard display
   limit of 200. `truncated=true` is returned only when more than 200 matching
   nodes exist.

## 3. Scope resolution and authorization

The API resolves retrieval scopes before starting the RAG pipeline:

- a concrete `kb_id` resolves to exactly one authorized knowledge base;
- `kb_id=*` resolves to every knowledge base matched by
  `kb_access_predicate(principal, READ)`;
- an empty result produces no retrieval evidence;
- no external index name supplied by the client is trusted.

Each resolved scope carries:

- `kb_id`;
- active Milvus collection name;
- embedding model;
- display name.

Vector, BM25, native Neo4j, and LightRAG retrieval fan out across the resolved
scopes. Results are fused once. Before reranking/context construction,
PostgreSQL performs a fail-closed chunk and document ACL filter constrained to
the exact resolved KB ID set.

## 4. Cross-knowledge-base graph model

Existing within-document co-occurrence edges remain `FLAVOR_RELATED`.

During graph ingestion each entity also stores:

- `tenant_id`;
- `kb_id`;
- `normalized_name`, produced by Unicode case folding and punctuation/space
  removal.

Entities with the same non-empty normalized name, in different knowledge bases
inside the same tenant, receive a deterministic `CROSS_KB_RELATED` edge labeled
`跨库同名实体`. This is the reliable baseline association: explainable,
idempotent, and independent of an optional LLM. It does not connect different
tenants.

Future semantic synonym or LLM-extracted cross-library edges must use a
different relation type and retain confidence/provenance; they must not replace
the deterministic baseline.

The graph read API accepts either one resolved KB or the complete authorized KB
set. It returns `knowledgeBaseId`/`knowledgeBaseName` on nodes and
`crossKnowledgeBase`/`type` on edges.

## 5. Retrieval execution

For global retrieval:

1. query understanding runs once without applying a single-KB term mapping;
2. each query fans out to each resolved vector collection and KB-filtered BM25
   query;
3. native Neo4j and LightRAG queries are scoped per knowledge base;
4. per-scope results are flattened into their channel;
5. existing channel timeouts, breakers, RRF, deduplication and reranking apply;
6. PostgreSQL filters all candidates by tenant, readable document and the exact
   resolved KB set;
7. source records retain their canonical document/chunk identity.

Graph failure remains visible in channel status. The global request still
requires the Graph RAG channel to be scheduled, but an optional graph backend
outage may degrade through the existing native/fallback behavior and must not
leak data.

## 6. Graph presentation

The visual language is a “knowledge star map”:

- quiet deep-slate/cyan canvas and provenance colors per knowledge base;
- deterministic clustered layout so reloads do not jump randomly;
- solid local relations and dashed luminous cross-library bridges;
- one orchestrated recall-path light flow after successful graph recall;
- node selection dims unrelated nodes and exposes description/provenance.

Interaction state is a transform `{x, y, scale}`:

- pointer drag changes `x/y`;
- wheel zoom is centered on the pointer and clamped to `0.45..2.5`;
- buttons zoom and reset;
- the canvas does not scroll the page while wheel-zooming;
- reduced-motion users receive static highlighted paths.

## 7. API compatibility

- Existing concrete `kb_id` calls remain valid.
- Graph limit defaults to 200 and is capped at 200 by the public endpoint.
- Existing graph node/edge fields remain; provenance and cross-edge fields are
  additive.
- `null`/missing chat KB behavior remains legacy auto-selection. Only `*`
  means global retrieval.
- Knowledge-base creation treats the server's `EMBEDDING_MODEL` as the default
  authority. The UI omits an unselected model instead of duplicating a
  provider-specific shorthand, and the server canonicalizes the legacy
  `qwen3-embedding-8b` alias before probing, persisting, or calling the provider.

## 8. TDD matrix

### Backend

1. Global scope resolution returns only KBs readable by the principal.
2. `kb_id=*` forces effective Graph RAG even when the request says false.
3. ACL post-filter accepts candidates from the resolved KB set and rejects a
   candidate from any other KB/tenant/document.
4. Retrieval scope fan-out invokes vector, BM25 and graph channels for each
   resolved KB.
5. Cross-KB graph writes include tenant and normalized name and create
   `CROSS_KB_RELATED`, never across tenants.
6. Combined graph reads include multiple KBs, cross-edge metadata and at most
   200 nodes.
7. Exactly 200 nodes is not truncated; 201 nodes is truncated.
8. The legacy Qwen embedding shorthand resolves to the configured
   provider-qualified model identifier.

### Frontend

1. Selecting `*` forces Graph RAG in state and a later disable attempt is
   ignored until a concrete KB is selected.
2. Graph request defaults/caps the limit at 200.
3. Wheel zoom clamps scale and preserves the pointer anchor.
4. Recall path selection chooses connected edges from a query-matching node,
   with a deterministic high-degree fallback.
5. Knowledge-base creation omits `embedding_model` by default but preserves an
   explicitly selected custom model.

## 9. Definition of done

- New tests are observed failing before the corresponding implementation.
- New tests and existing relevant regression tests pass.
- Full backend test suite passes, or every unrelated environmental failure is
  recorded with evidence.
- Frontend unit tests, TypeScript checking and production build pass.
- The graph is visually inspected at small, medium and 200-node fixtures.
- `git log` is unchanged by this work: no intermediate or final commit is made.
