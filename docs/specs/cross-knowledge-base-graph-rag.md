# Cross-Knowledge-Base Graph RAG — SDD

Status: implemented in v0.0.7; reliability hardening specified for v0.0.9
Date: 2026-07-28; updated 2026-08-02

## 1. Goal

Add a permission-safe `全部` retrieval scope that searches every knowledge
base the current principal can read, requires Graph RAG for that scope, and
renders a combined interactive knowledge graph with at most 200 entities per
normalized entity type in the global scope.

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
7. A concrete knowledge base retains the total 200-entity cap. The global
   scope applies the 200 cap independently to each normalized entity type;
   `truncatedByType` identifies only the types whose matching nodes exceed it.
8. At overview zoom, the UI renders `knowledge base × entity type` aggregate
   constellations. At detail zoom it renders only nodes and edges intersecting
   the viewport plus a bounded overscan margin.

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
inside the same tenant, are eligible for a deterministic `CROSS_KB_RELATED`
edge labeled `跨库同名实体`. Generic code/structure tokens are retained as local
nodes but are not cross-linkable. To avoid a complete bipartite explosion, the
highest-local-degree representative per knowledge base is selected and one edge
is retained per knowledge-base pair. This is the reliable baseline association:
explainable, idempotent, and independent of an optional LLM. It does not connect
different tenants.

Legacy graph nodes are upgraded in place by the idempotent backfill command.
New, updated, and deleted documents maintain only their affected normalized
entity groups during ordinary ingestion; no re-chunk or vector re-index is
required solely to maintain cross-knowledge-base edges.

v0.0.7 adds evidence-grounded `SEMANTIC_RELATED` edges without replacing the
deterministic baseline. A zero-temperature lightweight LLM may propose entities
and allow-listed relation types, but the server accepts them only when both
entities occur in the supplied document, the evidence occurs verbatim in the
cited chunk, and confidence meets the configured threshold. Each accepted edge
retains `chunk_id`, `doc_id`, `kb_id`, `tenant_id`, evidence, confidence, model,
prompt version, and a deterministic evidence ID. Generic transport/format terms
such as JSON, API, HTTP, XML, and YAML are explicitly ineligible for name-only
cross-library bridges.

Document ingestion replaces semantic data by document ID. Deletion removes the
document's nodes and relations and rebuilds only affected deterministic bridges;
knowledge-base deletion invokes the same operation for every document. Existing
active chunks can be semantically backfilled in place without re-chunking or
embedding through `python -m app.rag.graph.semantic_backfill --apply`.

The graph read API accepts either one resolved KB or the complete authorized KB
set. It returns `knowledgeBaseId`/`knowledgeBaseName` on nodes and
`crossKnowledgeBase`/`type` on edges. Empty or whitespace-only entity types are
normalized into the `unclassified` quota.

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

### 5.1 Reliability hardening (v0.0.9)

Global scope remains an authorization boundary, not a requirement to query
every readable index for every question. After resolving the complete readable
scope set, chat retrieval applies deterministic query-aware narrowing:

- when the original question contains one or more complete knowledge-base
  display names (Unicode NFKC + case-insensitive comparison), retrieval searches
  exactly those matched readable scopes;
- short one- or two-character names are not used for implicit narrowing because
  they are too ambiguous;
- when no readable knowledge-base name is mentioned, all resolved scopes remain
  active;
- the client cannot use a name mention to escape the previously resolved ACL
  boundary.

Embedding generation is shared per `(provider, canonical model, query)` while
concurrent scope searches are in flight. One caller owns the provider request;
other callers await the same task. Cancellation of one scope must not cancel the
shared provider request, and failed tasks must be evicted so a later request can
retry. Milvus searches remain independently scoped after the vector is shared.

Per-KB quota operates only on candidates returned by the reranker and passing
the active relevance threshold. A pre-rerank fallback candidate must never enter
the final context merely to satisfy diversity. If a KB has no eligible candidate,
its quota is intentionally left unmet.

Adjacent-chunk expansion runs after canonical metadata resolution. It must:

- fetch enabled, non-deleted chunks in the configured window from the same
  document and resolved KB set;
- append each neighbor once and retain `neighbor_of` attribution;
- use the structured application logger without allowing observability failures
  to discard successfully fetched neighbors;
- report a failed expansion as failed rather than a successful zero-addition
  trace.

Empty evidence has two distinct public outcomes:

- at least one retrieval channel completed successfully: `insufficient_relevance`;
- every scheduled channel timed out or failed: `retrieval_unavailable` and a
  retryable service-unavailable message, never a knowledge-gap message.

Deployment configuration must leave headroom between embedding retries and the
outer retrieval budgets. With two 10-second query attempts and a one-second
backoff, `RETRIEVAL_CHANNEL_TIMEOUT_MS` must exceed 21 seconds and
`RETRIEVAL_TOTAL_TIMEOUT_MS` must exceed the channel timeout.

## 6. Graph presentation

The visual language is a “knowledge star map”:

- quiet deep-slate/cyan canvas and provenance colors per knowledge base;
- deterministic clustered layout so reloads do not jump randomly;
- solid local relations and dashed luminous cross-library bridges;
- one orchestrated recall-path light flow after successful graph recall;
- node selection dims unrelated nodes and exposes description/provenance.
- a semantic zoom transition: overview bubbles encode provenance, entity type,
  and member count; detail zoom reveals the individual constellation.

Interaction state is a transform `{x, y, scale}`:

- pointer drag changes `x/y`;
- wheel zoom is centered on the pointer and clamped to `0.45..2.5`;
- buttons zoom and reset;
- the canvas does not scroll the page while wheel-zooming;
- overview mode draws aggregate bubbles and aggregate inter-type relations;
- detail mode culls offscreen nodes and any edge without two rendered endpoints;
- selected and recalled nodes remain render-pinned even just outside the
  viewport so interaction feedback does not disappear;
- reduced-motion users receive static highlighted paths.

## 7. API compatibility

- Existing concrete `kb_id` calls remain valid.
- Graph `limit` defaults to 200 and is capped at 200. For a concrete KB it is a
  total node limit; for `kb_id=*` it is a per-normalized-type limit.
- Graph responses add `limitMode`, `limitPerType`, `typeStats`, and
  `truncatedByType`; existing `truncated` remains the aggregate compatibility
  flag.
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
6. Combined graph reads include multiple KBs and cross-edge metadata.
7. A global graph with 200 nodes in each of several types returns all of them;
   the 201st node truncates only its own type.
8. The legacy Qwen embedding shorthand resolves to the configured
   provider-qualified model identifier.
9. Semantic extraction rejects hallucinated entities, fabricated evidence,
   illegal relation types, duplicate relations, and confidence below threshold.
10. Semantic writes replace prior document edges idempotently and retain
    evidence/model/prompt provenance.
11. Semantic model failure preserves the deterministic graph and allows the
    optional LightRAG enrichment step to continue.
12. A global question naming two readable KBs searches those two scopes and no
    others; a question naming none retains all readable scopes.
13. Concurrent scope searches for the same canonical embedding model/query
    make one provider embedding call, including when distinct client instances
    participate.
14. Per-KB quota cannot admit a below-threshold or pre-rerank fallback row.
15. Adjacent-chunk expansion returns existing neighbors with parent attribution
    and does not lose them while emitting structured logs.
16. All-channel timeout/error is classified as `retrieval_unavailable`; a
    successful empty channel remains `insufficient_relevance`.
17. The chat refusal text distinguishes temporary retrieval unavailability from
    an actual lack of authorized evidence.
18. Retrieval-stage SSE metadata includes the final neighbor-evidence count so
    streaming clients do not display a placeholder zero until `finish`.

### Frontend

1. Selecting `*` forces Graph RAG in state and a later disable attempt is
   ignored until a concrete KB is selected.
2. Graph request defaults/caps the limit at 200.
3. Wheel zoom clamps scale and preserves the pointer anchor.
4. Recall path selection chooses connected edges from a query-matching node,
   with a deterministic high-degree fallback.
5. Knowledge-base creation omits `embedding_model` by default but preserves an
   explicitly selected custom model.
6. Overview aggregation groups by knowledge base and normalized entity type
   and preserves aggregate edge counts.
7. Detail rendering excludes offscreen nodes/edges while retaining overscan
   and explicitly pinned interaction nodes.
8. Before `finish` supplies final sources, the neighbor badge uses the count
   delivered by retrieval metadata; after `finish`, final sources are the
   source of truth.

## 9. Definition of done

- New tests are observed failing before the corresponding implementation.
- New tests and existing relevant regression tests pass.
- Full backend test suite passes, or every unrelated environmental failure is
  recorded with evidence.
- Frontend unit tests, TypeScript checking and production build pass.
- The graph is visually inspected at small, medium, 200-node, and multi-type
  600+ node fixtures.
- `git log` is unchanged by this work: no intermediate or final commit is made.
