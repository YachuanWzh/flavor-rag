import { useEffect, useState } from "react";
import { Activity, ChevronRight, Clock3, Search, X } from "lucide-react";
import { api } from "@/services/api";

interface TraceItem {
  id: string;
  query: string;
  rewrite?: string;
  intent: string;
  searchMs: number;
  llmMs: number;
  totalMs: number;
  recallCount: number;
  finalCount: number;
  status: string;
  createTime: string;
}

interface TraceNode {
  id: string;
  node_type: string;
  node_name: string;
  duration_ms: number;
  status: string;
  input_data?: Record<string, unknown>;
  output_data?: Record<string, unknown>;
}

interface TraceDetail {
  run: {
    query: string;
    rewrite_query?: string;
    intent?: string;
    total_duration_ms: number;
    metadata?: Record<string, unknown>;
  };
  nodes: TraceNode[];
}

export default function TracesPage() {
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    api.get("/api/admin/traces?limit=50")
      .then((data: any) => setTraces(data.items))
      .finally(() => setLoading(false));
  }, []);

  const openTrace = async (traceId: string) => {
    setDetailLoading(true);
    try {
      setDetail(await api.get(`/api/admin/traces/${traceId}`) as unknown as TraceDetail);
    } finally {
      setDetailLoading(false);
    }
  };

  if (loading) return <div className="p-8 text-slate-400">加载中...</div>;

  return (
    <div className="p-6">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-700">Observability</p>
        <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">链路追踪</h2>
        <p className="mt-1 text-sm text-slate-500">查看改写、意图、多路检索、融合与重排的逐节点输入输出。</p>
      </div>

      <div className="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="hidden grid-cols-[minmax(220px,1fr)_100px_90px_90px_120px_24px] border-b border-slate-100 bg-slate-50/70 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400 md:grid">
          <span>查询</span><span>意图</span><span>总耗时</span><span>召回 / 最终</span><span>时间</span><span />
        </div>
        <div className="divide-y divide-slate-100">
          {traces.map((trace) => (
            <button
              key={trace.id}
              type="button"
              onClick={() => openTrace(trace.id)}
              className="grid w-full gap-2 px-4 py-3 text-left transition hover:bg-cyan-50/40 md:grid-cols-[minmax(220px,1fr)_100px_90px_90px_120px_24px] md:items-center"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-800">{trace.query}</p>
                {trace.rewrite && trace.rewrite !== trace.query && (
                  <p className="mt-0.5 truncate text-[11px] text-slate-400">改写：{trace.rewrite}</p>
                )}
              </div>
              <span className="w-fit rounded-full bg-cyan-50 px-2 py-0.5 text-[10px] text-cyan-700">{trace.intent || "未识别"}</span>
              <span className="font-mono text-xs text-slate-600">{trace.totalMs} ms</span>
              <span className="font-mono text-xs text-slate-600">{trace.recallCount} / {trace.finalCount}</span>
              <span className="text-[11px] text-slate-400">{trace.createTime?.slice(5, 16) || "—"}</span>
              <ChevronRight className="h-4 w-4 text-slate-300" />
            </button>
          ))}
          {!traces.length && <p className="py-10 text-center text-sm text-slate-400">暂无追踪记录</p>}
        </div>
      </div>

      {(detail || detailLoading) && (
        <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/20 backdrop-blur-[1px]" onClick={() => setDetail(null)}>
          <aside
            className="h-full w-full max-w-2xl overflow-y-auto border-l border-slate-200 bg-slate-50 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950 px-5 py-4 text-white">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-400">Trace inspector</p>
                  <h3 className="mt-1 text-base font-semibold">{detail?.run.query || "正在加载…"}</h3>
                </div>
                <button type="button" onClick={() => setDetail(null)} className="rounded-lg p-1 text-slate-400 hover:bg-white/10 hover:text-white">
                  <X className="h-5 w-5" />
                </button>
              </div>
              {detail && (
                <div className="mt-4 grid grid-cols-3 gap-2">
                  <TraceStat icon={<Clock3 />} label="总耗时" value={`${detail.run.total_duration_ms} ms`} />
                  <TraceStat icon={<Search />} label="意图" value={detail.run.intent || "—"} />
                  <TraceStat icon={<Activity />} label="节点" value={String(detail.nodes.length)} />
                </div>
              )}
            </header>
            <div className="space-y-3 p-5">
              {detail?.run.rewrite_query && (
                <div className="rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-3">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-cyan-700">Query rewrite</p>
                  <p className="mt-1 text-sm text-slate-700">{detail.run.rewrite_query}</p>
                </div>
              )}
              {detail?.nodes.map((node, index) => (
                <section key={node.id} className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                  <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-950 font-mono text-[10px] text-white">{index + 1}</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-slate-800">{node.node_name}</p>
                      <p className="text-[10px] uppercase tracking-[0.1em] text-slate-400">{node.node_type}</p>
                    </div>
                    <span className="font-mono text-xs text-slate-500">{node.duration_ms} ms</span>
                  </div>
                  <div className="grid gap-px bg-slate-100 sm:grid-cols-2">
                    <JsonPanel title="输入" value={node.input_data} />
                    <JsonPanel title="输出" value={node.output_data} />
                  </div>
                </section>
              ))}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

function TraceStat({ icon, label, value }: { icon: React.ReactElement; label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white/5 p-2">
      <div className="flex items-center gap-1.5 text-slate-400 [&>svg]:h-3 [&>svg]:w-3">{icon}<span className="text-[9px] uppercase">{label}</span></div>
      <p className="mt-1 truncate font-mono text-xs text-white">{value}</p>
    </div>
  );
}

function JsonPanel({ title, value }: { title: string; value?: Record<string, unknown> }) {
  return (
    <div className="min-w-0 bg-white p-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">{title}</p>
      <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-5 text-slate-600">
        {value ? JSON.stringify(value, null, 2) : "—"}
      </pre>
    </div>
  );
}
