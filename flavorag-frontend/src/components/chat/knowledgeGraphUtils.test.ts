import { describe, expect, it } from "vitest";
import type { GraphView } from "@/types";
import {
  aggregateGraphForOverview,
  clampGraphLimit,
  cullGraphToViewport,
  graphRecallFocusForMessages,
  graphRagForScope,
  nextViewportForWheel,
  recallPathEdges,
  shouldAggregateGraph,
} from "./knowledgeGraphUtils";

describe("knowledge graph interaction contract", () => {
  it("keeps Graph RAG enabled for the global scope", () => {
    expect(graphRagForScope("*", false)).toBe(true);
    expect(graphRagForScope("*", true)).toBe(true);
    expect(graphRagForScope("kb-a", false)).toBe(false);
  });

  it("caps graph requests at 200 entities per type", () => {
    expect(clampGraphLimit()).toBe(200);
    expect(clampGraphLimit(80)).toBe(80);
    expect(clampGraphLimit(999)).toBe(200);
  });

  it("aggregates overview nodes by knowledge base and normalized type", () => {
    const nodes = [
      {
        id: "a",
        name: "A",
        type: "Concept",
        knowledgeBaseId: "kb-1",
        x: 10,
        y: 20,
      },
      {
        id: "b",
        name: "B",
        type: " concept ",
        knowledgeBaseId: "kb-1",
        x: 30,
        y: 40,
      },
      {
        id: "c",
        name: "C",
        type: "Concept",
        knowledgeBaseId: "kb-2",
        x: 80,
        y: 100,
      },
    ];
    const overview = aggregateGraphForOverview(nodes, [
      { id: "ab", source: "a", target: "b" },
      { id: "bc", source: "b", target: "c", crossKnowledgeBase: true },
    ]);

    expect(overview.nodes).toHaveLength(2);
    expect(overview.nodes[0]).toMatchObject({
      count: 2,
      type: "concept",
      knowledgeBaseId: "kb-1",
      x: 20,
      y: 30,
    });
    expect(overview.edges).toHaveLength(1);
    expect(overview.edges[0]).toMatchObject({
      count: 1,
      crossKnowledgeBase: true,
    });
    expect(shouldAggregateGraph(600, 0.7)).toBe(true);
    expect(shouldAggregateGraph(600, 1)).toBe(false);
  });

  it("culls detail nodes and edges outside the viewport while retaining pins", () => {
    const nodes = [
      { id: "inside", name: "Inside", x: 50, y: 50 },
      { id: "overscan", name: "Overscan", x: 108, y: 50 },
      { id: "outside", name: "Outside", x: 180, y: 50 },
      { id: "pinned", name: "Pinned", x: 220, y: 50 },
    ];
    const result = cullGraphToViewport(
      nodes,
      [
        { id: "visible-edge", source: "inside", target: "overscan" },
        { id: "culled-edge", source: "inside", target: "outside" },
        { id: "pinned-edge", source: "inside", target: "pinned" },
      ],
      { x: 0, y: 0, scale: 1 },
      { width: 100, height: 100, overscan: 10 },
      new Set(["pinned"]),
    );

    expect(result.nodes.map((node) => node.id)).toEqual([
      "inside",
      "overscan",
      "pinned",
    ]);
    expect(result.edges.map((edge) => edge.id)).toEqual([
      "visible-edge",
      "pinned-edge",
    ]);
  });

  it("collapses a 600-entity fixture to a bounded overview model", () => {
    const nodes = Array.from({ length: 600 }, (_, index) => ({
      id: `node-${index}`,
      name: `Node ${index}`,
      type: ["concept", "identifier", "section"][index % 3],
      knowledgeBaseId: "kb-a",
      x: index % 30,
      y: Math.floor(index / 30),
    }));

    const overview = aggregateGraphForOverview(nodes, []);

    expect(overview.nodes).toHaveLength(3);
    expect(overview.nodes.map((node) => node.count)).toEqual([200, 200, 200]);
  });

  it("bounds dense detail rendering without dropping pinned nodes", () => {
    const nodes = Array.from({ length: 600 }, (_, index) => ({
      id: `node-${index}`,
      name: `Node ${index}`,
      x: index % 30,
      y: Math.floor(index / 30),
    }));
    const result = cullGraphToViewport(
      nodes,
      [],
      { x: 0, y: 0, scale: 1 },
      { width: 100, height: 100, maxNodes: 120 },
      new Set(["node-599"]),
    );

    expect(result.nodes).toHaveLength(120);
    expect(result.nodes.some((node) => node.id === "node-599")).toBe(true);
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

  it("restores the latest successful graph recall query from chat history", () => {
    expect(
      graphRecallFocusForMessages([
        { id: "u1", role: "user", content: "first graph question" },
        {
          id: "a1",
          role: "assistant",
          content: "first answer",
          retrievalChannels: { graph: { count: 2 } },
        },
        { id: "u2", role: "user", content: "plain follow-up" },
        {
          id: "a2",
          role: "assistant",
          content: "plain answer",
          retrievalChannels: { graph: { count: 0 } },
        },
      ]),
    ).toBe("first graph question");
  });

  it("returns no graph focus for a new or non-graph conversation", () => {
    expect(graphRecallFocusForMessages([])).toBe("");
    expect(
      graphRecallFocusForMessages([
        { id: "u1", role: "user", content: "hello" },
        { id: "a1", role: "assistant", content: "hi" },
      ]),
    ).toBe("");
  });
});
