"""RRF (Reciprocal Rank Fusion) + deduplication for multi-channel search results."""
from __future__ import annotations

from app.rag.search.base import SearchResult


def rrf_fusion(
    *result_lists: list[SearchResult],
    k: int = 60,
) -> list[SearchResult]:
    """Fuse multiple ranked result lists using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) across all lists for each unique chunk.

    Args:
        *result_lists: One list per channel. Each list is assumed sorted by
            descending relevance (index 0 = best).
        k: Smoothing constant (default 60, per original RRF paper).

    Returns:
        Single list sorted by descending RRF score.
    """
    if not result_lists:
        return []

    scores: dict[str, tuple[float, SearchResult]] = {}

    for result_list in result_lists:
        for rank, result in enumerate(result_list):
            rrf_score = 1.0 / (k + rank + 1)
            key = result.chunk_id or result.content  # fallback key
            if key in scores:
                prev_score, prev_result = scores[key]
                scores[key] = (
                    prev_score + rrf_score,
                    prev_result if prev_result.score >= result.score else result,
                )
            else:
                scores[key] = (rrf_score, result)

    # Sort by descending RRF score
    fused = sorted(scores.values(), key=lambda x: x[0], reverse=True)
    return [sr for _, sr in fused]


def deduplicate(
    results: list[SearchResult],
    content_threshold: float = 0.9,
) -> list[SearchResult]:
    """Remove near-duplicate results by content similarity.

    Uses simple Jaccard similarity on character 3-grams to detect dupes.
    """
    if not results:
        return []

    def _ngrams(text: str, n: int = 3) -> set[str]:
        text = text.lower()
        return {text[i : i + n] for i in range(len(text) - n + 1)}

    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 1.0
        return len(a & b) / len(a | b) if a | b else 0.0

    deduped: list[SearchResult] = []
    seen_ngrams: list[set[str]] = []

    for result in results:
        grams = _ngrams(result.content)
        is_dup = any(_jaccard(grams, seen) >= content_threshold for seen in seen_ngrams)
        if not is_dup:
            deduped.append(result)
            seen_ngrams.append(grams)

    return deduped
