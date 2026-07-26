import { api } from "@/services/api";
import type { GraphView, RagCapabilities } from "@/types";

export async function fetchRagCapabilities() {
  return api.get<RagCapabilities, RagCapabilities>("/api/rag/v3/capabilities");
}

export async function fetchKnowledgeGraph(
  kbId: string,
  options: { entity?: string; depth?: number; limit?: number } = {}
) {
  return api.get<GraphView, GraphView>("/api/rag/v3/graph", {
    params: {
      kb_id: kbId,
      entity: options.entity || "*",
      depth: options.depth || 2,
      limit: options.limit || 80,
    },
  });
}

export async function fetchGraphLabels(keyword = "", limit = 30) {
  return api.get<string[], string[]>("/api/rag/v3/graph/labels", {
    params: { keyword, limit },
  });
}
