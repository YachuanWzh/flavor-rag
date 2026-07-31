# Knowledge archive golden dataset

`knowledge-archive-golden-v1.jsonl` is the production-shaped retrieval and
answer dataset for the six active knowledge bases visible to the archive owner
on 2026-07-31.

## Coverage

| Knowledge base | Documents | Chunks | Snapshot |
|---|---:|---:|---|
| huamulan-agent | 11 | 1112 | `254bd3d68885e28f` |
| flavor-code | 3 | 475 | `8baaa610ee794712` |
| flavor-rag | 2 | 258 | `4666bc1a380171bc` |
| 面试知识库 | 4 | 90 | `3f2caef844e6d301` |
| 助理虾 | 7 | 156 | `9451d6ac45f840a3` |
| 踩坑相关 | 9 | 83 | `02f3aa43fa95913e` |
| **Total** | **36** | **2174** | `0ed5c8e19f1635dc` |

The dataset contains 54 active cases: 48 answerable cases and 6 refusal or
security cases. Every archived document has at least one labeled positive case,
and every answerable case has a reference answer. Three cases require evidence
from multiple knowledge bases.

Use the `*` (all knowledge bases) scope for the release gate. Individual
knowledge-base slices are useful diagnostics, but intentionally contain fewer
than the gate's 30-case minimum.

## Label contract

- `knowledge_base_ids` defines the exact corpus slice for the case.
- `expected_chunk_ids` contains only chunks that directly support the answer.
- `expected_doc_ids` binds the label to archived documents.
- `corpus_snapshot` is SHA-256 over sorted
  `document_id:content_hash:active_generation` rows for the case's KB scope,
  truncated to 16 hex characters.
- `document_generation` is included when every positive document has the same
  active generation. Cross-KB cases rely on their scoped corpus snapshot.
- `expected_answer` is a concise reference, not a required verbatim response.

When any source document is re-ingested, review affected cases and refresh both
chunk IDs and snapshots. A snapshot mismatch intentionally blocks the run
instead of silently scoring stale labels.

`minimal.jsonl` is retained as a legacy example for the older single-corpus
fixture; the application uses `knowledge-archive-golden-v1.jsonl`.
