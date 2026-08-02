"""Zero-result query clustering to identify knowledge gaps.

Uses cosine-distance DBSCAN (no external ML dependency) to group similar
failed queries so operators can prioritize corpus additions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TraceQuery:
    query: str
    embedding: list[float]
    kb_id: str = ""
    trace_id: str = ""


@dataclass
class QueryCluster:
    representative: str
    count: int
    queries: list[str] = field(default_factory=list)
    kb_ids: list[str] = field(default_factory=list)


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Return 1 - cosine_similarity (range [0, 2])."""
    if len(a) != len(b) or not a:
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


class QueryClusterAnalyzer:
    """DBSCAN-style clustering over query embeddings."""

    def __init__(self, *, eps: float = 0.3, min_samples: int = 3):
        self._eps = eps
        self._min_samples = min_samples

    def cluster_queries(self, queries: list[TraceQuery]) -> list[QueryCluster]:
        if not queries:
            return []

        n = len(queries)
        labels = [-1] * n  # -1 = unvisited/noise
        cluster_id = 0

        for i in range(n):
            if labels[i] != -1:
                continue
            neighbors = self._region_query(queries, i)
            # min_samples includes the point itself (standard DBSCAN)
            if len(neighbors) + 1 < self._min_samples:
                labels[i] = 0  # noise
                continue
            cluster_id += 1
            labels[i] = cluster_id
            seed_set = list(neighbors)
            j = 0
            while j < len(seed_set):
                q = seed_set[j]
                if labels[q] == 0:
                    labels[q] = cluster_id
                elif labels[q] == -1:
                    labels[q] = cluster_id
                    q_neighbors = self._region_query(queries, q)
                    if len(q_neighbors) >= self._min_samples:
                        seed_set.extend(
                            nb for nb in q_neighbors if nb not in seed_set
                        )
                j += 1

        # Build cluster results
        clusters_map: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            if label > 0:
                clusters_map.setdefault(label, []).append(idx)

        results: list[QueryCluster] = []
        for indices in clusters_map.values():
            cluster_queries = [queries[i] for i in indices]
            # Representative = longest query (most descriptive)
            representative = max(cluster_queries, key=lambda q: len(q.query)).query
            kb_ids = sorted({q.kb_id for q in cluster_queries if q.kb_id})
            results.append(
                QueryCluster(
                    representative=representative,
                    count=len(cluster_queries),
                    queries=[q.query for q in cluster_queries],
                    kb_ids=kb_ids,
                )
            )
        # Sort by count descending
        results.sort(key=lambda c: c.count, reverse=True)
        return results

    def _region_query(self, queries: list[TraceQuery], index: int) -> list[int]:
        neighbors: list[int] = []
        for j, q in enumerate(queries):
            if j == index:
                continue
            if _cosine_distance(queries[index].embedding, q.embedding) <= self._eps:
                neighbors.append(j)
        return neighbors
