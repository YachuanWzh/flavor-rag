import type { GraphView } from "@/types";

export const GLOBAL_KB_SCOPE = "*";
export const MAX_GRAPH_NODES = 200;

export interface GraphViewport {
  x: number;
  y: number;
  scale: number;
}

export interface GraphPoint {
  x: number;
  y: number;
}

export function clampGraphLimit(limit = MAX_GRAPH_NODES) {
  return Math.max(1, Math.min(MAX_GRAPH_NODES, Math.round(limit)));
}

export function graphRagForScope(
  kbId: string | null,
  requested: boolean,
) {
  return kbId === GLOBAL_KB_SCOPE ? true : requested;
}

export function nextViewportForWheel(
  viewport: GraphViewport,
  pointer: GraphPoint,
  deltaY: number,
): GraphViewport {
  const factor = deltaY < 0 ? 1.12 : 1 / 1.12;
  const scale = Math.max(0.45, Math.min(2.5, viewport.scale * factor));
  if (scale === viewport.scale) return viewport;

  const worldX = (pointer.x - viewport.x) / viewport.scale;
  const worldY = (pointer.y - viewport.y) / viewport.scale;
  return {
    x: pointer.x - worldX * scale,
    y: pointer.y - worldY * scale,
    scale,
  };
}

function normalized(value: string) {
  return value.toLocaleLowerCase().replace(/[\W_]+/gu, "");
}

export function recallPathEdges(
  graph: GraphView,
  query: string,
  maxEdges = 4,
) {
  const degree = new Map<string, number>();
  graph.edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  });

  const normalizedQuery = normalized(query);
  const matched = graph.nodes
    .filter((node) => {
      const name = normalized(node.name);
      return name.length >= 2 && normalizedQuery.includes(name);
    })
    .sort(
      (left, right) =>
        right.name.length - left.name.length || left.id.localeCompare(right.id),
    );
  const fallback = [...graph.nodes].sort(
    (left, right) =>
      (degree.get(right.id) || 0) - (degree.get(left.id) || 0) ||
      left.id.localeCompare(right.id),
  );
  const seed = matched[0] || fallback[0];
  if (!seed || maxEdges <= 0) return new Set<string>();

  const selected = new Set<string>();
  const visited = new Set<string>([seed.id]);
  const queue = [seed.id];
  const orderedEdges = [...graph.edges].sort((a, b) => a.id.localeCompare(b.id));

  while (queue.length && selected.size < maxEdges) {
    const nodeId = queue.shift()!;
    for (const edge of orderedEdges) {
      if (selected.size >= maxEdges) break;
      if (edge.source !== nodeId && edge.target !== nodeId) continue;
      const neighbor = edge.source === nodeId ? edge.target : edge.source;
      if (visited.has(neighbor)) continue;
      selected.add(edge.id);
      visited.add(neighbor);
      queue.push(neighbor);
    }
  }
  return selected;
}

