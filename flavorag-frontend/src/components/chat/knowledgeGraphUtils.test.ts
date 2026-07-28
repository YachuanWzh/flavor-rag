import { describe, expect, it } from "vitest";
import type { GraphView } from "@/types";
import {
  clampGraphLimit,
  graphRagForScope,
  nextViewportForWheel,
  recallPathEdges,
} from "./knowledgeGraphUtils";

describe("knowledge graph interaction contract", () => {
  it("keeps Graph RAG enabled for the global scope", () => {
    expect(graphRagForScope("*", false)).toBe(true);
    expect(graphRagForScope("*", true)).toBe(true);
    expect(graphRagForScope("kb-a", false)).toBe(false);
  });

  it("caps graph requests at 200 entities", () => {
    expect(clampGraphLimit()).toBe(200);
    expect(clampGraphLimit(80)).toBe(80);
    expect(clampGraphLimit(999)).toBe(200);
  });

  it("clamps wheel zoom while preserving the pointer anchor", () => {
    const viewport = { x: 10, y: 20, scale: 1 };
    const zoomed = nextViewportForWheel(
      viewport,
      { x: 200, y: 100 },
      -120,
    );

    expect(zoomed.scale).toBeGreaterThan(1);
    expect(
      (200 - zoomed.x) / zoomed.scale,
    ).toBeCloseTo((200 - viewport.x) / viewport.scale);
    expect(
      (100 - zoomed.y) / zoomed.scale,
    ).toBeCloseTo((100 - viewport.y) / viewport.scale);

    expect(
      nextViewportForWheel({ x: 0, y: 0, scale: 2.5 }, { x: 0, y: 0 }, -999)
        .scale,
    ).toBe(2.5);
    expect(
      nextViewportForWheel({ x: 0, y: 0, scale: 0.45 }, { x: 0, y: 0 }, 999)
        .scale,
    ).toBe(0.45);
  });

  it("builds a deterministic connected recall path from a query match", () => {
    const graph: GraphView = {
      nodes: [
        { id: "a", name: "Graph RAG" },
        { id: "b", name: "Knowledge Base" },
        { id: "c", name: "Citation" },
        { id: "d", name: "Isolated" },
      ],
      edges: [
        { id: "ab", source: "a", target: "b" },
        { id: "bc", source: "b", target: "c" },
      ],
      truncated: false,
    };

    expect(recallPathEdges(graph, "Graph RAG 如何跨库检索", 2)).toEqual(
      new Set(["ab", "bc"]),
    );
    expect(recallPathEdges(graph, "no direct match", 1)).toEqual(
      new Set(["ab"]),
    );
  });
});
