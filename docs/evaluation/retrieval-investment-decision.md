# Retrieval investment decision

Date: 2026-07-26  
Corpus inspected: local `flavorag_dev.db`

## Observed corpus

- 1 active Markdown document
- 66 active text chunks
- 0 structured image chunks
- 0 persisted image assets
- no labeled visual question with a positive expected result
- 1 preliminary multi-step question in the minimal evaluation set

## Measured lexical baseline

The minimal set was run against the 66 live chunks in `flavorag_dev.db` using a
deterministic character-bigram lexical baseline on 2026-07-26:

- evaluated active cases: 7
- Recall@5: 0.80
- MRR: 0.507
- nDCG@5: 0.577
- refusal precision: 1.00
- refusal recall: 0.50
- lexical runner ACL leakage signal: not applicable
- local retrieval p95: 15 ms

This baseline exposes a refusal weakness: a lexical retriever alone treats an
ACL-denied sentinel as answerable. Production retrieval therefore applies the
relational ACL post-filter before answerability is decided. The result is not
evidence of graph or visual uplift because neither candidate retriever had
labeled positive cases in this run.

A separate cross-tenant relational integration test returned zero chunks for a
known foreign-tenant chunk; the enforced ACL leakage count in that test is 0.

## Decision

### GraphRAG: HOLD

There are not yet enough labeled multi-hop cases to measure incremental
Recall@K. The enablement gate requires at least five active labeled multi-hop
cases, positive Recall@K uplift of at least 0.05, and acceptable p95 latency.
The single current multi-step case was already retrieved at rank 1 by the
baseline, so it does not justify graph cost. The graph feature remains disabled
by default.

### Visual retrieval: HOLD

The current corpus has no image assets, so a visual embedding index cannot
produce measurable benefit. Continue extracting image assets, VLM descriptions,
page coordinates, and image-dependent labels during normal PDF ingestion.
Enable a visual retrieval experiment only after at least five active labeled
visual cases exist.

### Structured PDF text/table retrieval: CONTINUE

Coordinate-bearing text, OCR, tables, and VLM descriptions remain valuable
without a separate visual vector index because they improve citations and make
visual content searchable through the existing text channels.

## Hard gates

Any candidate must keep ACL leakage at exactly zero. An optional retriever is
enabled only when its labeled slice passes both quality uplift and latency
budgets; missing labels produce `HOLD`, not `ENABLE`.
