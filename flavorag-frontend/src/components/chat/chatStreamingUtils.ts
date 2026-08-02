type NeighborEvidence = {
  neighborOf?: unknown[];
};

export function displayedNeighborEvidenceCount(
  sources: NeighborEvidence[] | undefined,
  streamedCount: number | undefined,
): number {
  if (sources !== undefined) {
    return sources.filter((source) => source.neighborOf?.length).length;
  }
  return Math.max(0, streamedCount ?? 0);
}
