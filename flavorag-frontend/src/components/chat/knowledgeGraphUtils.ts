import type { GraphEdge, GraphNode, GraphView, Message } from "@/types";

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

export interface PositionedGraphNode extends GraphNode {
  x: number;
  y: number;
  color?: string;
  degree?: number;
}

export interface AggregateGraphNode extends PositionedGraphNode {
  count: number;
  memberIds: string[];
}

export interface AggregateGraphEdge extends GraphEdge {
  count: number;
  memberEdgeIds: string[];
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

export function normalizedGraphType(type?: string) {
  return type?.trim().toLocaleLowerCase() || "unclassified";
}

export function shouldAggregateGraph(nodeCount: number, scale: number) {
  return nodeCount > 120 && scale < 0.82;
}

export function aggregateGraphForOverview<T extends PositionedGraphNode>(
  nodes: T[],
  edges: GraphEdge[],
) {
  const nodeGroup = new Map<string, string>();
  const groups = new Map<
    string,
    {
      type: string;
      knowledgeBaseId: string;
      knowledgeBaseName?: string;
      color?: string;
      x: number;
      y: number;
      members: string[];
    }
  >();

  nodes.forEach((node) => {
    const type = normalizedGraphType(node.type);
    const knowledgeBaseId = node.knowledgeBaseId || "default";
    const key = `${knowledgeBaseId}\u0000${type}`;
    nodeGroup.set(node.id, key);
    const group = groups.get(key);
    if (group) {
      group.x += node.x;
      group.y += node.y;
      group.members.push(node.id);
      return;
    }
    groups.set(key, {
      type,
      knowledgeBaseId,
      knowledgeBaseName: node.knowledgeBaseName,
      color: node.color,
      x: node.x,
      y: node.y,
      members: [node.id],
    });
  });

  const aggregateNodes: AggregateGraphNode[] = [...groups.entries()].map(
    ([key, group]) => ({
      id: `aggregate:${encodeURIComponent(key)}`,
      name: group.type === "unclassified" ? "未分类" : group.type,
      type: group.type,
      knowledgeBaseId: group.knowledgeBaseId,
      knowledgeBaseName: group.knowledgeBaseName,
      color: group.color,
      x: group.x / group.members.length,
      y: group.y / group.members.length,
      count: group.members.length,
      memberIds: group.members,
    }),
  );
  const aggregateIdByKey = new Map(
    [...groups.keys()].map((key, index) => [key, aggregateNodes[index].id]),
  );
  const edgeGroups = new Map<string, AggregateGraphEdge>();
  edges.forEach((edge) => {
    const sourceGroup = nodeGroup.get(edge.source);
    const targetGroup = nodeGroup.get(edge.target);
    if (!sourceGroup || !targetGroup || sourceGroup === targetGroup) return;
    const source = aggregateIdByKey.get(sourceGroup)!;
    const target = aggregateIdByKey.get(targetGroup)!;
    const ordered = source < target ? [source, target] : [target, source];
    const key = `${ordered[0]}\u0000${ordered[1]}`;
    const existing = edgeGroups.get(key);
    if (existing) {
      existing.count += 1;
      existing.memberEdgeIds.push(edge.id);
      existing.crossKnowledgeBase ||= Boolean(edge.crossKnowledgeBase);
      return;
    }
    edgeGroups.set(key, {
      id: `aggregate-edge:${encodeURIComponent(key)}`,
      source: ordered[0],
      target: ordered[1],
      count: 1,
      memberEdgeIds: [edge.id],
      crossKnowledgeBase: Boolean(edge.crossKnowledgeBase),
    });
  });

  return {
    nodes: aggregateNodes,
    edges: [...edgeGroups.values()],
  };
}

export function cullGraphToViewport<T extends PositionedGraphNode>(
  nodes: T[],
  edges: GraphEdge[],
  viewport: GraphViewport,
  bounds: {
    width: number;
    height: number;
    overscan?: number;
    maxNodes?: number;
  },
  pinnedIds = new Set<string>(),
) {
  const overscan = Math.max(0, bounds.overscan ?? 80);
  const left = -viewport.x / viewport.scale - overscan;
  const top = -viewport.y / viewport.scale - overscan;
  const right = (bounds.width - viewport.x) / viewport.scale + overscan;
  const bottom = (bounds.height - viewport.y) / viewport.scale + overscan;
  let visibleNodes = nodes.filter(
    (node) =>
      pinnedIds.has(node.id) ||
      (node.x >= left && node.x <= right && node.y >= top && node.y <= bottom),
  );
  const maxNodes = Math.max(1, bounds.maxNodes ?? Number.MAX_SAFE_INTEGER);
  if (visibleNodes.length > maxNodes) {
    const centerX = (left + right) / 2;
    const centerY = (top + bottom) / 2;
    const pinned = visibleNodes.filter((node) => pinnedIds.has(node.id));
    const capacity = Math.max(0, maxNodes - pinned.length);
    const nearest = visibleNodes
      .filter((node) => !pinnedIds.has(node.id))
      .sort((leftNode, rightNode) => {
        const leftDistance =
          (leftNode.x - centerX) ** 2 + (leftNode.y - centerY) ** 2;
        const rightDistance =
          (rightNode.x - centerX) ** 2 + (rightNode.y - centerY) ** 2;
        return (
          leftDistance - rightDistance ||
          (rightNode.degree || 0) - (leftNode.degree || 0) ||
          leftNode.id.localeCompare(rightNode.id)
        );
      })
      .slice(0, capacity);
    const retainedIds = new Set(
      [...pinned, ...nearest].map((node) => node.id),
    );
    visibleNodes = nodes.filter((node) => retainedIds.has(node.id));
  }
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  return {
    nodes: visibleNodes,
    edges: edges.filter(
      (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
    ),
  };
}

export function graphRecallFocusForMessages(messages: Message[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (
      message.role !== "assistant" ||
      Number(message.retrievalChannels?.graph?.count || 0) <= 0
    ) {
      continue;
    }
    for (let questionIndex = index - 1; questionIndex >= 0; questionIndex -= 1) {
      if (messages[questionIndex].role === "user") {
        return messages[questionIndex].content.trim();
      }
    }
  }
  return "";
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
