import {
  AlertCircle,
  Focus,
  Loader2,
  LockKeyhole,
  Minus,
  Network,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { fetchKnowledgeGraph } from "@/services/graphService";
import type { GraphNode, GraphView } from "@/types";
import {
  MAX_GRAPH_NODES,
  nextViewportForWheel,
  recallPathEdges,
  type GraphViewport,
} from "./knowledgeGraphUtils";

interface Props {
  kbId: string | null;
  kbName?: string;
  refreshKey: number;
  focusQuery?: string;
  locked?: boolean;
  onClose: () => void;
}

interface PositionedNode extends GraphNode {
  x: number;
  y: number;
  color: string;
  degree: number;
}

const WIDTH = 1100;
const HEIGHT = 760;
const INITIAL_VIEWPORT: GraphViewport = { x: 0, y: 0, scale: 1 };
const palette = [
  "#22d3ee",
  "#818cf8",
  "#f472b6",
  "#34d399",
  "#fbbf24",
  "#fb7185",
  "#a78bfa",
  "#2dd4bf",
];
const goldenAngle = Math.PI * (3 - Math.sqrt(5));

function hash(value: string) {
  let result = 0;
  for (let index = 0; index < value.length; index += 1) {
    result = (result * 31 + value.charCodeAt(index)) | 0;
  }
  return Math.abs(result);
}

function nodeColor(node: GraphNode) {
  return palette[
    hash(node.knowledgeBaseId || node.type || node.name) % palette.length
  ];
}

function layoutGraph(view: GraphView | null): PositionedNode[] {
  if (!view?.nodes.length) return [];
  const degrees = new Map<string, number>();
  view.edges.forEach((edge) => {
    degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1);
    degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1);
  });

  const groups = new Map<string, GraphNode[]>();
  view.nodes.forEach((node) => {
    const key = node.knowledgeBaseId || "default";
    groups.set(key, [...(groups.get(key) || []), node]);
  });
  const orderedGroups = [...groups.entries()].sort(([a], [b]) =>
    a.localeCompare(b),
  );
  const groupCount = orderedGroups.length;

  return orderedGroups.flatMap(([groupId, nodes], groupIndex) => {
    const groupAngle =
      (groupIndex / Math.max(1, groupCount)) * Math.PI * 2 - Math.PI / 2;
    const groupRadius = groupCount > 1 ? Math.min(275, 130 + groupCount * 24) : 0;
    const centerX = WIDTH / 2 + Math.cos(groupAngle) * groupRadius;
    const centerY = HEIGHT / 2 + Math.sin(groupAngle) * groupRadius * 0.72;
    const ordered = [...nodes].sort(
      (a, b) =>
        (degrees.get(b.id) || 0) - (degrees.get(a.id) || 0) ||
        a.name.localeCompare(b.name),
    );
    const spacing =
      groupCount === 1
        ? Math.max(24, 42 - ordered.length * 0.08)
        : Math.max(20, 34 - ordered.length * 0.06);

    return ordered.map((node, index) => {
      if (index === 0) {
        return {
          ...node,
          x: centerX,
          y: centerY,
          color: nodeColor(node),
          degree: degrees.get(node.id) || 0,
        };
      }
      const radius = Math.sqrt(index) * spacing;
      const angle = index * goldenAngle + hash(groupId) * 0.001;
      return {
        ...node,
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius * 0.82,
        color: nodeColor(node),
        degree: degrees.get(node.id) || 0,
      };
    });
  });
}

function readableError(error: unknown) {
  const candidate = error as {
    response?: { data?: { detail?: string } };
    message?: string;
  };
  return candidate.response?.data?.detail || candidate.message || "图谱加载失败";
}

export default function KnowledgeGraphPanel({
  kbId,
  kbName,
  refreshKey,
  focusQuery = "",
  locked = false,
  onClose,
}: Props) {
  const [view, setView] = useState<GraphView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [entity, setEntity] = useState("");
  const [activeEntity, setActiveEntity] = useState("*");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [viewport, setViewport] =
    useState<GraphViewport>(INITIAL_VIEWPORT);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    clientX: number;
    clientY: number;
    origin: GraphViewport;
  } | null>(null);

  const loadGraph = useCallback(
    async (focus = "*") => {
      if (!kbId) {
        setView(null);
        return;
      }
      setLoading(true);
      setError("");
      try {
        const result = await fetchKnowledgeGraph(kbId, {
          entity: focus || "*",
          depth: 2,
          limit: MAX_GRAPH_NODES,
        });
        setView(result);
        setActiveEntity(focus || "*");
        setSelectedId(null);
        setViewport(INITIAL_VIEWPORT);
      } catch (reason) {
        setError(readableError(reason));
        setView(null);
      } finally {
        setLoading(false);
      }
    },
    [kbId],
  );

  useEffect(() => {
    void loadGraph("*");
  }, [kbId, refreshKey, loadGraph]);

  const positioned = useMemo(() => layoutGraph(view), [view]);
  const byId = useMemo(
    () => new Map(positioned.map((node) => [node.id, node])),
    [positioned],
  );
  const selected = selectedId ? byId.get(selectedId) : undefined;
  const connectedIds = useMemo(() => {
    if (!selectedId || !view) return new Set<string>();
    const ids = new Set<string>([selectedId]);
    view.edges.forEach((edge) => {
      if (edge.source === selectedId) ids.add(edge.target);
      if (edge.target === selectedId) ids.add(edge.source);
    });
    return ids;
  }, [selectedId, view]);
  const recalledEdges = useMemo(
    () =>
      view && refreshKey > 0
        ? recallPathEdges(view, focusQuery, Math.min(7, view.edges.length))
        : new Set<string>(),
    [focusQuery, refreshKey, view],
  );
  const recalledNodes = useMemo(() => {
    const ids = new Set<string>();
    if (!view) return ids;
    view.edges.forEach((edge) => {
      if (recalledEdges.has(edge.id)) {
        ids.add(edge.source);
        ids.add(edge.target);
      }
    });
    return ids;
  }, [recalledEdges, view]);
  const knowledgeBases = useMemo(() => {
    const values = new Map<string, { name: string; color: string }>();
    positioned.forEach((node) => {
      const id = node.knowledgeBaseId || "default";
      if (!values.has(id)) {
        values.set(id, {
          name: node.knowledgeBaseName || (id === "default" ? kbName || "知识库" : id),
          color: node.color,
        });
      }
    });
    return [...values.entries()];
  }, [kbName, positioned]);

  const submitEntity = () => {
    const value = entity.trim();
    void loadGraph(value || "*");
  };

  const zoomAtCenter = (direction: "in" | "out") => {
    setViewport((current) =>
      nextViewportForWheel(
        current,
        { x: WIDTH / 2, y: HEIGHT / 2 },
        direction === "in" ? -120 : 120,
      ),
    );
  };

  return (
    <aside className="relative flex h-[42vh] min-h-[330px] shrink-0 flex-col overflow-hidden border-t border-slate-800 bg-[#07131c] text-slate-100 lg:h-auto lg:min-h-0 lg:w-[470px] lg:border-l lg:border-t-0 xl:w-[570px]">
      <header className="relative z-10 border-b border-cyan-950 bg-[#091923]/95 px-4 py-3 backdrop-blur">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-cyan-800/70 bg-cyan-950/70 text-cyan-300 shadow-[0_0_28px_rgba(34,211,238,.12)]">
            <Network className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold tracking-tight text-white">
                知识关系星图
              </h2>
              <span className="rounded-full border border-cyan-800/60 bg-cyan-950/70 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-cyan-300">
                Live graph
              </span>
              {locked && (
                <span title="全部知识库检索必须保持 Graph RAG 开启">
                  <LockKeyhole className="h-3.5 w-3.5 text-amber-300" />
                </span>
              )}
            </div>
            <p className="mt-0.5 truncate text-[11px] text-slate-400">
              {kbName || "请选择知识库"}
              {view
                ? ` · ${view.nodes.length} 个实体 · ${view.edges.length} 条关系`
                : ""}
            </p>
          </div>
          {!locked && (
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭知识关系图"
              className="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="mt-3 flex gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <input
              value={entity}
              onChange={(event) => setEntity(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submitEntity();
              }}
              placeholder="输入实体名称聚焦子图"
              className="h-8 w-full rounded-lg border border-slate-700 bg-slate-900/80 pl-8 pr-2 text-xs text-slate-100 outline-none transition placeholder:text-slate-600 focus:border-cyan-600 focus:ring-2 focus:ring-cyan-950"
            />
          </div>
          <button
            type="button"
            onClick={submitEntity}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-cyan-400 px-2.5 text-xs font-semibold text-slate-950 transition hover:bg-cyan-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
          >
            <Focus className="h-3.5 w-3.5" />
            聚焦
          </button>
          <button
            type="button"
            onClick={() => {
              setEntity("");
              void loadGraph("*");
            }}
            aria-label="刷新全图"
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-slate-400 transition hover:border-cyan-700 hover:text-cyan-300"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </header>

      <div className="relative min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_50%_44%,#102d3a_0,#091923_46%,#050d14_100%)]">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 opacity-30 [background-image:linear-gradient(rgba(34,211,238,.1)_1px,transparent_1px),linear-gradient(90deg,rgba(34,211,238,.1)_1px,transparent_1px)] [background-size:34px_34px]"
        />

        {!kbId && (
          <EmptyState
            title="先选择一个检索范围"
            detail="选择“全部”可查看当前账号有权限访问的跨库知识关联。"
          />
        )}
        {error && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 px-8 text-center">
            <AlertCircle className="h-8 w-8 text-rose-400" />
            <p className="text-sm font-medium text-slate-200">图谱暂不可用</p>
            <p className="text-xs leading-5 text-slate-500">{error}</p>
            <button
              type="button"
              onClick={() => void loadGraph(activeEntity)}
              className="mt-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-300 hover:border-cyan-700 hover:text-cyan-300"
            >
              重新连接
            </button>
          </div>
        )}
        {!error && !loading && kbId && view && view.nodes.length === 0 && (
          <EmptyState
            title="这个范围还没有图数据"
            detail="请确认 Graph 服务已启用，并重新处理需要抽取实体的文档。"
          />
        )}
        {loading && (
          <div className="absolute inset-0 z-30 flex items-center justify-center bg-[#07131c]/70 backdrop-blur-[2px]">
            <div className="flex items-center gap-2 rounded-full border border-cyan-900 bg-slate-950/90 px-3 py-2 text-xs text-cyan-300 shadow-[0_0_30px_rgba(34,211,238,.1)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在编排关系网络
            </div>
          </div>
        )}

        {view && positioned.length > 0 && (
          <svg
            ref={svgRef}
            role="img"
            aria-label={`${kbName || "知识库"}关系图`}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="relative h-full w-full cursor-grab select-none touch-none active:cursor-grabbing"
            onWheel={(event) => {
              event.preventDefault();
              const bounds = event.currentTarget.getBoundingClientRect();
              const point = {
                x: ((event.clientX - bounds.left) / bounds.width) * WIDTH,
                y: ((event.clientY - bounds.top) / bounds.height) * HEIGHT,
              };
              setViewport((current) =>
                nextViewportForWheel(current, point, event.deltaY),
              );
            }}
            onPointerDown={(event) => {
              dragRef.current = {
                pointerId: event.pointerId,
                clientX: event.clientX,
                clientY: event.clientY,
                origin: viewport,
              };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              const drag = dragRef.current;
              if (!drag || drag.pointerId !== event.pointerId) return;
              const bounds = event.currentTarget.getBoundingClientRect();
              setViewport({
                ...drag.origin,
                x:
                  drag.origin.x +
                  ((event.clientX - drag.clientX) / bounds.width) * WIDTH,
                y:
                  drag.origin.y +
                  ((event.clientY - drag.clientY) / bounds.height) * HEIGHT,
              });
            }}
            onPointerUp={(event) => {
              dragRef.current = null;
              event.currentTarget.releasePointerCapture(event.pointerId);
            }}
            onPointerCancel={() => {
              dragRef.current = null;
            }}
          >
            <defs>
              <filter id="node-glow" x="-100%" y="-100%" width="300%" height="300%">
                <feGaussianBlur stdDeviation="5" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              <filter id="path-glow" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              <marker
                id="graph-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="4"
                markerHeight="4"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b" />
              </marker>
              <style>{`
                @keyframes graph-flow {
                  from { stroke-dashoffset: 42; }
                  to { stroke-dashoffset: 0; }
                }
                @keyframes node-breathe {
                  0%, 100% { opacity: .65; }
                  50% { opacity: 1; }
                }
                .graph-flow { animation: graph-flow 1.1s linear infinite; }
                .node-breathe { animation: node-breathe 1.7s ease-in-out infinite; }
                @media (prefers-reduced-motion: reduce) {
                  .graph-flow, .node-breathe { animation: none; }
                }
              `}</style>
            </defs>
            <g
              transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.scale})`}
            >
              {view.edges.map((edge) => {
                const source = byId.get(edge.source);
                const target = byId.get(edge.target);
                if (!source || !target) return null;
                const muted =
                  Boolean(selectedId) &&
                  edge.source !== selectedId &&
                  edge.target !== selectedId;
                const recalled = recalledEdges.has(edge.id);
                const cross = Boolean(edge.crossKnowledgeBase);
                return (
                  <g key={edge.id} opacity={muted ? 0.08 : recalled ? 1 : 0.55}>
                    <line
                      x1={source.x}
                      y1={source.y}
                      x2={target.x}
                      y2={target.y}
                      stroke={
                        recalled ? "#67e8f9" : cross ? "#c084fc" : "#456174"
                      }
                      strokeWidth={recalled ? 3.2 : cross ? 1.8 : 1.1}
                      strokeDasharray={
                        recalled ? "8 6" : cross ? "3 5" : undefined
                      }
                      markerEnd={recalled ? undefined : "url(#graph-arrow)"}
                      filter={recalled ? "url(#path-glow)" : undefined}
                      className={recalled ? "graph-flow" : undefined}
                    />
                    {edge.label &&
                      !muted &&
                      (cross || positioned.length <= 28) && (
                        <text
                          x={(source.x + target.x) / 2}
                          y={(source.y + target.y) / 2 - 6}
                          textAnchor="middle"
                          className={
                            cross
                              ? "fill-violet-300 text-[9px]"
                              : "fill-slate-500 text-[9px]"
                          }
                        >
                          {edge.label.slice(0, 16)}
                        </text>
                      )}
                  </g>
                );
              })}

              {positioned.map((node) => {
                const selectedNode = node.id === selectedId;
                const muted = Boolean(selectedId) && !connectedIds.has(node.id);
                const recalled = recalledNodes.has(node.id);
                const radius = Math.min(20, 9 + Math.sqrt(node.degree + 1) * 2.2);
                const showLabel =
                  selectedNode ||
                  recalled ||
                  positioned.length <= 45 ||
                  node.degree >= 3;
                return (
                  <g
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`查看实体 ${node.name}`}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => setSelectedId(selectedNode ? null : node.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedId(selectedNode ? null : node.id);
                      }
                    }}
                    className="cursor-pointer outline-none"
                    opacity={muted ? 0.14 : 1}
                  >
                    {(selectedNode || recalled) && (
                      <circle
                        cx={node.x}
                        cy={node.y}
                        r={radius + 9}
                        fill="none"
                        stroke={node.color}
                        strokeWidth="1.5"
                        opacity=".7"
                        className={recalled ? "node-breathe" : undefined}
                      />
                    )}
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={selectedNode ? radius + 3 : radius}
                      fill="#081822"
                      stroke={node.color}
                      strokeWidth={selectedNode ? 3.5 : 2}
                      filter={selectedNode || recalled ? "url(#node-glow)" : undefined}
                    />
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={Math.max(2.8, radius * 0.27)}
                      fill={node.color}
                    />
                    {showLabel && (
                      <text
                        x={node.x}
                        y={node.y + radius + 14}
                        textAnchor="middle"
                        paintOrder="stroke"
                        stroke="#07131c"
                        strokeWidth="4"
                        className={`text-[10px] font-medium ${
                          selectedNode || recalled
                            ? "fill-white"
                            : "fill-slate-400"
                        }`}
                      >
                        {node.name.length > 18
                          ? `${node.name.slice(0, 17)}…`
                          : node.name}
                      </text>
                    )}
                    <title>
                      {`${node.name}${node.knowledgeBaseName ? ` · ${node.knowledgeBaseName}` : ""}`}
                    </title>
                  </g>
                );
              })}
            </g>
          </svg>
        )}

        {knowledgeBases.length > 1 && (
          <div className="pointer-events-none absolute left-3 top-3 z-10 max-w-[65%] rounded-xl border border-slate-700/70 bg-slate-950/70 px-2.5 py-2 backdrop-blur">
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {knowledgeBases.slice(0, 8).map(([id, item]) => (
                <span
                  key={id}
                  className="inline-flex items-center gap-1.5 text-[9px] text-slate-400"
                >
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ backgroundColor: item.color }}
                  />
                  {item.name}
                </span>
              ))}
              {knowledgeBases.length > 8 && (
                <span className="text-[9px] text-slate-500">
                  +{knowledgeBases.length - 8}
                </span>
              )}
            </div>
          </div>
        )}

        <div className="absolute bottom-3 right-3 z-10 flex items-center overflow-hidden rounded-xl border border-slate-700 bg-slate-950/85 shadow-lg backdrop-blur">
          <button
            type="button"
            aria-label="缩小图谱"
            onClick={() => zoomAtCenter("out")}
            className="p-2 text-slate-400 hover:bg-slate-800 hover:text-cyan-300"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setViewport(INITIAL_VIEWPORT)}
            title="重置画布"
            className="flex items-center gap-1 border-x border-slate-700 px-2 py-2 text-[10px] tabular-nums text-slate-400 hover:bg-slate-800 hover:text-white"
          >
            <RotateCcw className="h-3 w-3" />
            {Math.round(viewport.scale * 100)}%
          </button>
          <button
            type="button"
            aria-label="放大图谱"
            onClick={() => zoomAtCenter("in")}
            className="p-2 text-slate-400 hover:bg-slate-800 hover:text-cyan-300"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        {view?.truncated && (
          <span className="absolute bottom-3 left-3 max-w-[calc(100%-150px)] truncate rounded-full border border-amber-700/60 bg-amber-950/80 px-2 py-1 text-[10px] text-amber-300">
            实体超过上限，已展示关联度最高的 {MAX_GRAPH_NODES} 个
          </span>
        )}
      </div>

      {selected && (
        <div className="border-t border-slate-800 bg-[#091923] px-4 py-3">
          <div className="flex items-start gap-2">
            <span
              className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full shadow-[0_0_10px_currentColor]"
              style={{ backgroundColor: selected.color, color: selected.color }}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="truncate text-xs font-semibold text-white">
                  {selected.name}
                </h3>
                {selected.type && (
                  <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-400">
                    {selected.type}
                  </span>
                )}
                {selected.knowledgeBaseName && (
                  <span className="text-[9px] text-cyan-400">
                    {selected.knowledgeBaseName}
                  </span>
                )}
              </div>
              <p className="mt-1 line-clamp-3 text-[11px] leading-4 text-slate-400">
                {selected.description || "该实体暂无补充说明。"}
              </p>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center px-8 text-center">
      <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-900 bg-slate-950/80 text-cyan-400 shadow-[0_0_30px_rgba(34,211,238,.1)]">
        <Network className="h-5 w-5" />
      </span>
      <p className="text-sm font-medium text-slate-200">{title}</p>
      <p className="mt-1 max-w-xs text-xs leading-5 text-slate-500">{detail}</p>
    </div>
  );
}
