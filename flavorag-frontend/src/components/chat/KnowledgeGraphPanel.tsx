import {
  AlertCircle,
  Focus,
  Loader2,
  Minus,
  Network,
  Plus,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchKnowledgeGraph } from "@/services/graphService";
import type { GraphNode, GraphView } from "@/types";

interface Props {
  kbId: string | null;
  kbName?: string;
  refreshKey: number;
  onClose: () => void;
}

interface PositionedNode extends GraphNode {
  x: number;
  y: number;
  color: string;
}

const WIDTH = 820;
const HEIGHT = 560;
const colors = ["#0891b2", "#2563eb", "#7c3aed", "#db2777", "#059669", "#d97706"];

function hash(value: string) {
  let result = 0;
  for (let index = 0; index < value.length; index += 1) {
    result = (result * 31 + value.charCodeAt(index)) | 0;
  }
  return Math.abs(result);
}

function layoutGraph(view: GraphView | null): PositionedNode[] {
  if (!view?.nodes.length) return [];
  const degrees = new Map<string, number>();
  view.edges.forEach((edge) => {
    degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1);
    degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1);
  });
  const ordered = [...view.nodes].sort(
    (left, right) => (degrees.get(right.id) || 0) - (degrees.get(left.id) || 0)
  );
  const centerCount = ordered.length > 8 ? 1 : 0;
  return ordered.map((node, index) => {
    if (index < centerCount) {
      return {
        ...node,
        x: WIDTH / 2,
        y: HEIGHT / 2,
        color: colors[hash(node.type || node.name) % colors.length],
      };
    }
    const ringIndex = index - centerCount;
    const firstRing = Math.min(10, ordered.length - centerCount);
    const isFirstRing = ringIndex < firstRing;
    const ringItems = isFirstRing
      ? firstRing
      : Math.max(1, ordered.length - centerCount - firstRing);
    const positionInRing = isFirstRing ? ringIndex : ringIndex - firstRing;
    const radius = isFirstRing ? 150 : 245;
    const angle =
      (positionInRing / ringItems) * Math.PI * 2 -
      Math.PI / 2 +
      (isFirstRing ? 0 : Math.PI / Math.max(ringItems, 2));
    return {
      ...node,
      x: WIDTH / 2 + Math.cos(angle) * radius,
      y: HEIGHT / 2 + Math.sin(angle) * radius,
      color: colors[hash(node.type || node.name) % colors.length],
    };
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
  onClose,
}: Props) {
  const [view, setView] = useState<GraphView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [entity, setEntity] = useState("");
  const [activeEntity, setActiveEntity] = useState("*");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);

  const loadGraph = useCallback(
    async (focus = activeEntity) => {
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
          limit: 80,
        });
        setView(result);
        setActiveEntity(focus || "*");
        setSelectedId(null);
      } catch (reason) {
        setError(readableError(reason));
        setView(null);
      } finally {
        setLoading(false);
      }
    },
    [activeEntity, kbId]
  );

  useEffect(() => {
    void loadGraph("*");
    // A completed Graph RAG request refreshes the graph after ingestion/query.
  }, [kbId, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const positioned = useMemo(() => layoutGraph(view), [view]);
  const byId = useMemo(
    () => new Map(positioned.map((node) => [node.id, node])),
    [positioned]
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

  const submitEntity = () => {
    const value = entity.trim();
    void loadGraph(value || "*");
  };

  return (
    <aside className="relative flex h-[38vh] min-h-[300px] shrink-0 flex-col overflow-hidden border-t border-slate-200 bg-[#f7fbfc] lg:h-auto lg:min-h-0 lg:w-[430px] lg:border-l lg:border-t-0 xl:w-[500px]">
      <div className="relative z-10 border-b border-cyan-100 bg-white/90 px-4 py-3 backdrop-blur">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-cyan-950 text-cyan-200 shadow-sm">
            <Network className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold tracking-tight text-slate-900">
                知识关系图
              </h2>
              <span className="rounded-full bg-cyan-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-cyan-700">
                Live
              </span>
            </div>
            <p className="mt-0.5 truncate text-[11px] text-slate-500">
              {kbName || "请选择知识库"}
              {view ? ` · ${view.nodes.length} 个实体 · ${view.edges.length} 条关系` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭知识图谱"
            className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-3 flex gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              value={entity}
              onChange={(event) => setEntity(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submitEntity();
              }}
              placeholder="输入实体名聚焦子图"
              className="h-8 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-2 text-xs text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-cyan-400 focus:ring-2 focus:ring-cyan-100"
            />
          </div>
          <button
            type="button"
            onClick={submitEntity}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-cyan-950 px-2.5 text-xs font-medium text-white transition hover:bg-cyan-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
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
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-cyan-300 hover:text-cyan-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_center,_#ffffff_0,_#f2fbfc_50%,_#e5f5f7_100%)]">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 opacity-45 [background-image:linear-gradient(rgba(8,145,178,.08)_1px,transparent_1px),linear-gradient(90deg,rgba(8,145,178,.08)_1px,transparent_1px)] [background-size:28px_28px]"
        />

        {!kbId && (
          <EmptyState
            title="先选择一个知识库"
            detail="Graph RAG 会按当前知识库隔离和展示实体关系。"
          />
        )}
        {error && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 px-8 text-center">
            <AlertCircle className="h-8 w-8 text-rose-400" />
            <p className="text-sm font-medium text-slate-700">图谱暂不可用</p>
            <p className="text-xs leading-5 text-slate-500">{error}</p>
            <button
              type="button"
              onClick={() => void loadGraph(activeEntity)}
              className="mt-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-cyan-300 hover:text-cyan-700"
            >
              重新连接
            </button>
          </div>
        )}
        {!error && !loading && kbId && view && view.nodes.length === 0 && (
          <EmptyState
            title="这个知识库还没有图数据"
            detail="开启服务端 GRAPH_ENABLED 后重新处理文档，实体与关系会自动构建。"
          />
        )}
        {loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-white/65 backdrop-blur-[1px]">
            <div className="flex items-center gap-2 rounded-full border border-cyan-100 bg-white px-3 py-2 text-xs text-cyan-800 shadow-sm">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在读取关系网络
            </div>
          </div>
        )}

        {view && positioned.length > 0 && (
          <svg
            role="img"
            aria-label={`${kbName || "知识库"}关系图`}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="relative h-full w-full select-none"
          >
            <defs>
              <filter id="node-shadow" x="-50%" y="-50%" width="200%" height="200%">
                <feDropShadow dx="0" dy="4" stdDeviation="5" floodColor="#164e63" floodOpacity=".18" />
              </filter>
              <marker
                id="graph-arrow"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="4"
                markerHeight="4"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
              </marker>
            </defs>
            <g transform={`translate(${WIDTH / 2} ${HEIGHT / 2}) scale(${zoom}) translate(${-WIDTH / 2} ${-HEIGHT / 2})`}>
              {view.edges.map((edge) => {
                const source = byId.get(edge.source);
                const target = byId.get(edge.target);
                if (!source || !target) return null;
                const muted =
                  selectedId &&
                  edge.source !== selectedId &&
                  edge.target !== selectedId;
                return (
                  <g key={edge.id} opacity={muted ? 0.12 : 0.7}>
                    <line
                      x1={source.x}
                      y1={source.y}
                      x2={target.x}
                      y2={target.y}
                      stroke={muted ? "#cbd5e1" : "#94a3b8"}
                      strokeWidth={muted ? 1 : 1.4}
                      markerEnd="url(#graph-arrow)"
                    />
                    {edge.label && !muted && positioned.length <= 30 && (
                      <text
                        x={(source.x + target.x) / 2}
                        y={(source.y + target.y) / 2 - 5}
                        textAnchor="middle"
                        className="fill-slate-400 text-[9px]"
                      >
                        {edge.label.slice(0, 18)}
                      </text>
                    )}
                  </g>
                );
              })}
              {positioned.map((node, index) => {
                const selectedNode = node.id === selectedId;
                const muted = selectedId && !connectedIds.has(node.id);
                const radius = index === 0 && positioned.length > 8 ? 24 : 17;
                return (
                  <g
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`查看实体 ${node.name}`}
                    onClick={() => setSelectedId(selectedNode ? null : node.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        setSelectedId(selectedNode ? null : node.id);
                      }
                    }}
                    className="cursor-pointer outline-none"
                    opacity={muted ? 0.18 : 1}
                  >
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={selectedNode ? radius + 7 : radius}
                      fill={selectedNode ? "#ecfeff" : "#ffffff"}
                      stroke={node.color}
                      strokeWidth={selectedNode ? 4 : 2.5}
                      filter="url(#node-shadow)"
                    />
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={selectedNode ? 6 : 5}
                      fill={node.color}
                    />
                    <text
                      x={node.x}
                      y={node.y + radius + 15}
                      textAnchor="middle"
                      className={`text-[11px] font-medium ${selectedNode ? "fill-cyan-950" : "fill-slate-600"}`}
                    >
                      {node.name.length > 18 ? `${node.name.slice(0, 17)}…` : node.name}
                    </text>
                    <title>{`${node.name}${node.type ? ` · ${node.type}` : ""}`}</title>
                  </g>
                );
              })}
            </g>
          </svg>
        )}

        <div className="absolute bottom-3 right-3 z-10 flex items-center overflow-hidden rounded-xl border border-slate-200 bg-white/90 shadow-sm backdrop-blur">
          <button
            type="button"
            aria-label="缩小图谱"
            onClick={() => setZoom((value) => Math.max(0.65, value - 0.15))}
            className="p-2 text-slate-500 hover:bg-slate-50 hover:text-cyan-700"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setZoom(1)}
            className="border-x border-slate-200 px-2 py-2 text-[10px] tabular-nums text-slate-500 hover:bg-slate-50"
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            type="button"
            aria-label="放大图谱"
            onClick={() => setZoom((value) => Math.min(1.6, value + 0.15))}
            className="p-2 text-slate-500 hover:bg-slate-50 hover:text-cyan-700"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        {view?.truncated && (
          <span className="absolute bottom-3 left-3 rounded-full border border-amber-200 bg-amber-50/90 px-2 py-1 text-[10px] text-amber-700">
            已展示前 80 个实体
          </span>
        )}
      </div>

      {selected && (
        <div className="border-t border-cyan-100 bg-white px-4 py-3">
          <div className="flex items-start gap-2">
            <span
              className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: selected.color }}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="truncate text-xs font-semibold text-slate-900">
                  {selected.name}
                </h3>
                {selected.type && (
                  <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-500">
                    {selected.type}
                  </span>
                )}
              </div>
              <p className="mt-1 line-clamp-3 text-[11px] leading-4 text-slate-500">
                {selected.description || "该实体暂时没有补充说明。"}
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
      <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-100 bg-white text-cyan-700 shadow-sm">
        <Network className="h-5 w-5" />
      </span>
      <p className="text-sm font-medium text-slate-700">{title}</p>
      <p className="mt-1 max-w-xs text-xs leading-5 text-slate-500">{detail}</p>
    </div>
  );
}
