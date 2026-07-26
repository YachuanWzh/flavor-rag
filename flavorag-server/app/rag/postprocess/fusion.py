"""RRF (Reciprocal Rank Fusion) + deduplication for multi-channel search results."""
from __future__ import annotations

from app.rag.search.base import SearchResult


def rrf_fusion(
    *result_lists: list[SearchResult],
    k: int = 60,
    weights: dict[str, float] | None = None,
    channel_names: list[str] | None = None,
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

    scores: dict[str, dict] = {}

    for channel_index, result_list in enumerate(result_lists):
        channel = (
            channel_names[channel_index]
            if channel_names and channel_index < len(channel_names)
            else f"channel_{channel_index + 1}"
        )
        weight = max(0.0, float((weights or {}).get(channel, 1.0)))
        for rank, result in enumerate(result_list):
            contribution = weight / (k + rank + 1)
            key = result.chunk_id or result.content  # fallback key
            if key in scores:
                scores[key]["score"] += contribution
            else:
                scores[key] = {
                    "score": contribution,
                    "result": result,
                    "channels": {},
                }
            scores[key]["channels"][channel] = {
                "rank": rank + 1,
                "rawScore": float(result.score),
                "weight": weight,
                "rrfContribution": contribution,
            }

    fused = sorted(scores.values(), key=lambda item: item["score"], reverse=True)
    output: list[SearchResult] = []
    for item in fused:
        result = item["result"]
        result.metadata["channelScores"] = item["channels"]
        result.metadata["matchedChannels"] = list(item["channels"])
        result.metadata["fusionScore"] = item["score"]
        result.score = item["score"]
        output.append(result)
    return output


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
            continue
        # Near duplicates still contribute useful channel attribution.
        target = next(
            (
                item
                for item in deduped
                if _jaccard(grams, _ngrams(item.content)) >= content_threshold
            ),
            None,
        )
        if target is not None:
            existing = target.metadata.setdefault("channelScores", {})
            existing.update(result.metadata.get("channelScores", {}))
            target.metadata["matchedChannels"] = list(existing)

    return deduped
