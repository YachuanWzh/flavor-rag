import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, ArrowUpRight, CheckCircle2, ChevronRight,
  Clock, Database, Gauge, Radio, RefreshCw, RotateCcw, Search,
  Server, ShieldAlert, Sparkles, X,
} from "lucide-react";
import { api } from "@/services/api";

interface Diagnostic {
  key: string;
  severity: "healthy" | "warning" | "critical";
  title: string;
  reason: string;
  action: string;
  lastOccurredAt: string;
}

interface MonitoringSummary {
  windowHours: number;
  rag: {
    total: number; success: number; failed: number; successRate: number | null;
    avgTotalMs: number; p95TotalMs: number; avgSearchMs: number; avgLlmMs: number;
  };
  ingestion: {
    queue: Record<string, number>;
    sources: {
      asyncOutbox: Record<string, number>;
      pipelineTasks: Record<string, number>;
    };
    windowCompleted: number; windowDead: number; avgJobMs: number;
  };
  documents: Record<string, number>;
  diagnostics: Diagnostic[];
  errors: { windowTotal: number };
  observability: {
    prometheus: { enabled: boolean; metricsEndpoint: string; uiUrl: string };
    grafana: { uiUrl: string };
    otel: {
      enabled: boolean; active: boolean; exporterEndpoint: string;
      serviceName: string; jaegerUrl: string;
    };
  };
}

interface TimeseriesPoint {
  time: string; ragTotal: number; ragFailed: number; ragAvgMs: number;
  jobsCompleted: number; jobsDead: number;
}

interface IngestionJobItem {
  id: string;
  source: "async_outbox" | "pipeline_task";
  sourceLabel: string;
  docId: string;
  docName: string;
  kbId: string;
  operation: string;
  status: string;
  attempts: number;
  maxAttempts: number;
  durationMs: number | null;
  chunkCount: number | null;
  errorMessage: string;
  nextRetryTime: string | null;
  createTime: string;
  completedAt: string | null;
  retryable: boolean;
  detail: Record<string, unknown>;
}

const WINDOW_OPTIONS = [
  { label: "1 小时", hours: 1 },
  { label: "24 小时", hours: 24 },
  { label: "7 天", hours: 168 },
];

const JOB_STATUS_STYLES: Record<string, string> = {
  QUEUED: "border-blue-200 bg-blue-50 text-blue-700",
  RUNNING: "border-amber-200 bg-amber-50 text-amber-700",
  RETRY: "border-orange-200 bg-orange-50 text-orange-700",
  SUCCESS: "border-emerald-200 bg-emerald-50 text-emerald-700",
  DEAD: "border-red-200 bg-red-50 text-red-700",
};

const DIAGNOSTIC_STYLES = {
  healthy: {
    frame: "border-emerald-200 bg-emerald-50/60",
    icon: "bg-emerald-600 text-white",
    title: "text-emerald-950",
    Icon: CheckCircle2,
  },
  warning: {
    frame: "border-amber-200 bg-amber-50/70",
    icon: "bg-amber-500 text-white",
    title: "text-amber-950",
    Icon: AlertTriangle,
  },
  critical: {
    frame: "border-red-200 bg-red-50/70",
    icon: "bg-red-600 text-white",
    title: "text-red-950",
    Icon: ShieldAlert,
  },
};

function Metric({
  label, value, hint, Icon,
}: {
  label: string; value: string | number; hint: string; Icon: typeof Activity;
}) {
  return (
    <div className="min-w-0 border-l border-slate-200 pl-4 first:border-l-0 first:pl-0">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <p className="mt-1 font-mono text-2xl font-semibold tracking-tight text-slate-950">{value}</p>
      <p className="mt-0.5 truncate text-[11px] text-slate-400" title={hint}>{hint}</p>
    </div>
  );
}

function ActivityChart({ points }: { points: TimeseriesPoint[] }) {
  const maxRag = Math.max(1, ...points.map((point) => point.ragTotal));
  const maxJobs = Math.max(1, ...points.map((point) => point.jobsCompleted + point.jobsDead));
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {[
        {
          title: "问答请求",
          description: "青色成功，红色未完成",
          max: maxRag,
          values: (point: TimeseriesPoint) => [point.ragTotal - point.ragFailed, point.ragFailed],
          colors: ["bg-cyan-500", "bg-red-400"],
        },
        {
          title: "摄取吞吐",
          description: "靛蓝完成，红色死信",
          max: maxJobs,
          values: (point: TimeseriesPoint) => [point.jobsCompleted, point.jobsDead],
          colors: ["bg-indigo-500", "bg-red-400"],
        },
      ].map((chart) => (
        <section key={chart.title} className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="flex items-baseline justify-between">
            <h3 className="text-sm font-semibold text-slate-800">{chart.title}</h3>
            <span className="text-[10px] text-slate-400">{chart.description}</span>
          </div>
          <div className="mt-5 flex h-28 items-end gap-[2px]">
            {points.map((point, index) => {
              const [ok, failed] = chart.values(point);
              return (
                <div
                  key={`${chart.title}-${index}`}
                  className="group relative flex h-full flex-1 flex-col justify-end"
                  title={`${point.time}\n成功 ${ok}，失败 ${failed}`}
                >
                  <div className={`w-full rounded-t-sm ${chart.colors[1]}`} style={{ height: `${failed / chart.max * 100}%` }} />
                  <div className={`w-full ${chart.colors[0]}`} style={{ height: `${ok / chart.max * 100}%` }} />
                </div>
              );
            })}
          </div>
          <div className="mt-2 flex justify-between font-mono text-[9px] text-slate-400">
            <span>{points[0]?.time?.slice(5, 16) || "—"}</span>
            <span>{points[points.length - 1]?.time?.slice(5, 16) || "—"}</span>
          </div>
        </section>
      ))}
    </div>
  );
}

export default function MonitoringPage() {
  const [hours, setHours] = useState(24);
  const [summary, setSummary] = useState<MonitoringSummary | null>(null);
  const [points, setPoints] = useState<TimeseriesPoint[]>([]);
  const [jobs, setJobs] = useState<IngestionJobItem[]>([]);
  const [selectedJob, setSelectedJob] = useState<IngestionJobItem | null>(null);
  const [jobStatus, setJobStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (quiet = false) => {
    try {
      if (!quiet) setRefreshing(true);
      setError("");
      const [sum, ts, jobList] = await Promise.all([
        api.get(`/api/admin/monitoring/summary?hours=${hours}`),
        api.get(`/api/admin/monitoring/timeseries?hours=${hours}&buckets=48`),
        api.get(`/api/admin/monitoring/ingestion-jobs?status=${jobStatus}&limit=20`),
      ]);
      setSummary(sum as unknown as MonitoringSummary);
      setPoints((ts as any).points || []);
      setJobs((jobList as any).items || []);
    } catch (caught: any) {
      setError(caught?.message || "监控数据暂时无法加载，请稍后重试。");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [hours, jobStatus]);

  useEffect(() => {
    setLoading(true);
    load();
    const timer = window.setInterval(() => load(true), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const retryJob = async (job: IngestionJobItem) => {
    try {
      await api.post(`/api/admin/monitoring/ingestion-jobs/${job.id}/retry`);
      setSelectedJob(null);
      await load();
    } catch (caught: any) {
      setError(caught?.message || "任务暂时无法重新入队，请稍后重试。");
    }
  };

  if (loading) {
    return <div className="p-8 text-sm text-slate-400">正在汇总运行状态…</div>;
  }

  const queue = summary?.ingestion.queue || {};
  const sourceCounts = summary?.ingestion.sources;
  const successRate = summary?.rag.successRate == null
    ? "—"
    : `${(summary.rag.successRate * 100).toFixed(1)}%`;

  return (
    <div className="min-h-full bg-slate-50/70 p-6">
      <header className="flex flex-col gap-4 border-b border-slate-200 pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-700">Operations desk</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">系统运行态势</h1>
          <p className="mt-1 text-sm text-slate-500">先看哪里需要处理，再下钻链路、错误审计和摄取任务。</p>
        </div>
        <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
          {WINDOW_OPTIONS.map((option) => (
            <button
              key={option.hours}
              type="button"
              onClick={() => setHours(option.hours)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                hours === option.hours ? "bg-slate-950 text-white" : "text-slate-500 hover:bg-slate-100"
              }`}
            >
              {option.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => load()}
            className="ml-1 rounded-lg p-1.5 text-slate-500 hover:bg-slate-100"
            title="立即刷新"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        </div>
      </header>

      {error && (
        <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      <section className="mt-5">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">需要关注</h2>
          <span className="text-[10px] text-slate-400">30 秒自动刷新</span>
        </div>
        <div className="grid gap-3 xl:grid-cols-3">
          {(summary?.diagnostics || []).map((diagnostic) => {
            const style = DIAGNOSTIC_STYLES[diagnostic.severity];
            return (
              <article key={diagnostic.key} className={`rounded-2xl border p-4 ${style.frame}`}>
                <div className="flex items-start gap-3">
                  <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${style.icon}`}>
                    <style.Icon className="h-4 w-4" />
                  </span>
                  <div className="min-w-0">
                    <h3 className={`text-sm font-semibold ${style.title}`}>{diagnostic.title}</h3>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-600" title={diagnostic.reason}>{diagnostic.reason}</p>
                    <p className="mt-2 text-[11px] font-medium text-slate-700">下一步：{diagnostic.action}</p>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="mt-5 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-6">
          <Metric Icon={Activity} label="问答请求" value={summary?.rag.total ?? 0} hint={`窗口内失败 ${summary?.rag.failed ?? 0} 次`} />
          <Metric Icon={CheckCircle2} label="成功率" value={successRate} hint="成功完成并持久化回答" />
          <Metric Icon={Gauge} label="P95 延迟" value={`${summary?.rag.p95TotalMs ?? 0} ms`} hint={`平均 ${summary?.rag.avgTotalMs ?? 0} ms`} />
          <Metric Icon={Search} label="检索 / 生成" value={`${summary?.rag.avgSearchMs ?? 0} / ${summary?.rag.avgLlmMs ?? 0}`} hint="平均耗时，单位 ms" />
          <Metric Icon={Sparkles} label="摄取完成" value={summary?.ingestion.windowCompleted ?? 0} hint={`窗口内死信 ${summary?.ingestion.windowDead ?? 0}`} />
          <Metric Icon={ShieldAlert} label="错误审计" value={summary?.errors.windowTotal ?? 0} hint="可在审计日志按错误编号查询" />
        </div>
      </section>

      <section className="mt-5 grid gap-4 lg:grid-cols-3">
        <ObservabilityCard
          Icon={Radio}
          title="Prometheus"
          state={summary?.observability.prometheus.enabled ? "已采集" : "未启用"}
          healthy={Boolean(summary?.observability.prometheus.enabled)}
          detail={`指标端点 ${summary?.observability.prometheus.metricsEndpoint || "/metrics"}`}
          links={[
            ["打开 Prometheus", summary?.observability.prometheus.uiUrl],
            ["查看原始指标", summary?.observability.prometheus.metricsEndpoint],
          ]}
        />
        <ObservabilityCard
          Icon={Activity}
          title="OpenTelemetry"
          state={summary?.observability.otel.active ? "正在导出" : summary?.observability.otel.enabled ? "启动失败" : "未启用"}
          healthy={Boolean(summary?.observability.otel.active)}
          detail={summary?.observability.otel.enabled
            ? `${summary.observability.otel.serviceName} → ${summary.observability.otel.exporterEndpoint}`
            : "设置 OTEL_ENABLED=true 后导出链路"}
          links={[["打开 Jaeger", summary?.observability.otel.jaegerUrl]]}
        />
        <ObservabilityCard
          Icon={Server}
          title="Grafana"
          state="运维面板"
          healthy
          detail="查看 Prometheus 趋势、阈值与告警规则"
          links={[["打开 Grafana", summary?.observability.grafana.uiUrl]]}
        />
      </section>

      <div className="mt-5"><ActivityChart points={points} /></div>

      <section className="mt-5">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">摄取任务</h2>
            <p className="mt-0.5 text-xs text-slate-400">
              同时展示异步队列与可视化流水线；此前为空是因为页面只查询了异步队列表。
            </p>
          </div>
          <select
            value={jobStatus}
            onChange={(event) => setJobStatus(event.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 outline-none focus:border-cyan-500"
          >
            <option value="">全部状态</option>
            {["QUEUED", "RUNNING", "RETRY", "SUCCESS", "DEAD"].map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </div>

        <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
          {[
            ["QUEUED", "排队", Clock],
            ["RUNNING", "执行中", Gauge],
            ["RETRY", "待重试", RotateCcw],
            ["SUCCESS", "已完成", CheckCircle2],
            ["DEAD", "死信", AlertTriangle],
          ].map(([status, label, Icon]: any) => (
            <div key={status} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span>{label}</span><Icon className="h-3.5 w-3.5" />
              </div>
              <p className="mt-1 font-mono text-xl font-semibold text-slate-900">{queue[status] ?? 0}</p>
            </div>
          ))}
        </div>

        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div className="hidden grid-cols-[110px_minmax(220px,1fr)_100px_90px_100px_140px_24px] border-b border-slate-100 bg-slate-50/80 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400 lg:grid">
            <span>来源</span><span>文档</span><span>状态</span><span>尝试</span><span>耗时 / 切片</span><span>创建时间</span><span />
          </div>
          <div className="divide-y divide-slate-100">
            {jobs.map((job) => (
              <button
                type="button"
                key={`${job.source}-${job.id}`}
                onClick={() => setSelectedJob(job)}
                className="grid w-full gap-2 px-4 py-3 text-left hover:bg-cyan-50/30 lg:grid-cols-[110px_minmax(220px,1fr)_100px_90px_100px_140px_24px] lg:items-center"
              >
                <span className="w-fit rounded-md bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600">{job.sourceLabel}</span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800">{job.docName || job.docId || "未关联文档"}</p>
                  {job.errorMessage && <p className="mt-0.5 truncate text-[11px] text-red-500">{job.errorMessage}</p>}
                </div>
                <span className={`w-fit rounded-md border px-2 py-0.5 text-[10px] font-semibold ${JOB_STATUS_STYLES[job.status] || "border-slate-200 bg-slate-50 text-slate-600"}`}>
                  {job.status}
                </span>
                <span className="font-mono text-xs text-slate-500">
                  {job.source === "async_outbox" ? `${job.attempts}/${job.maxAttempts}` : job.attempts}
                </span>
                <span className="font-mono text-xs text-slate-500">{job.durationMs == null ? "—" : `${job.durationMs} ms`} / {job.chunkCount ?? "—"}</span>
                <span className="font-mono text-[11px] text-slate-400">{job.createTime?.slice(0, 19) || "—"}</span>
                <ChevronRight className="h-4 w-4 text-slate-300" />
              </button>
            ))}
            {!jobs.length && (
              <div className="px-6 py-12 text-center">
                <Database className="mx-auto h-8 w-8 text-slate-200" />
                <p className="mt-3 text-sm font-medium text-slate-500">这个筛选条件下没有摄取任务</p>
                <p className="mt-1 text-xs text-slate-400">
                  当前异步队列 {Object.values(sourceCounts?.asyncOutbox || {}).reduce((a, b) => a + b, 0)} 条，
                  流水线 {Object.values(sourceCounts?.pipelineTasks || {}).reduce((a, b) => a + b, 0)} 条。
                </p>
              </div>
            )}
          </div>
        </div>
      </section>

      {selectedJob && (
        <JobDrawer job={selectedJob} onClose={() => setSelectedJob(null)} onRetry={() => retryJob(selectedJob)} />
      )}
    </div>
  );
}

function ObservabilityCard({
  Icon, title, state, detail, healthy, links,
}: {
  Icon: typeof Activity; title: string; state: string; detail: string; healthy: boolean;
  links: Array<[string, string | undefined]>;
}) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-950 text-white"><Icon className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
            <span className={`flex items-center gap-1 text-[10px] font-medium ${healthy ? "text-emerald-600" : "text-amber-600"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${healthy ? "bg-emerald-500" : "bg-amber-500"}`} />{state}
            </span>
          </div>
          <p className="mt-1 truncate text-[11px] text-slate-400" title={detail}>{detail}</p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-3 border-t border-slate-100 pt-3">
        {links.filter(([, url]) => Boolean(url)).map(([label, url]) => (
          <a key={label} href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[11px] font-medium text-cyan-700 hover:text-cyan-900">
            {label}<ArrowUpRight className="h-3 w-3" />
          </a>
        ))}
      </div>
    </article>
  );
}

function JobDrawer({
  job, onClose, onRetry,
}: {
  job: IngestionJobItem; onClose: () => void; onRetry: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/20 backdrop-blur-[1px]" onClick={onClose}>
      <aside className="h-full w-full max-w-xl overflow-y-auto border-l border-slate-200 bg-slate-50 shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950 p-5 text-white">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-400">{job.sourceLabel}</p>
              <h2 className="mt-1 text-lg font-semibold">{job.docName || job.docId || "摄取任务详情"}</h2>
              <p className="mt-1 font-mono text-[11px] text-slate-400">{job.id}</p>
            </div>
            <button type="button" onClick={onClose} className="rounded-lg p-1 text-slate-400 hover:bg-white/10 hover:text-white"><X className="h-5 w-5" /></button>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2">
            <DrawerStat label="状态" value={job.status} />
            <DrawerStat label="耗时" value={job.durationMs == null ? "—" : `${job.durationMs} ms`} />
            <DrawerStat label="切片" value={String(job.chunkCount ?? "—")} />
          </div>
        </header>
        <div className="space-y-4 p-5">
          {job.errorMessage ? (
            <section className="rounded-xl border border-red-200 bg-red-50 p-4">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-red-600">失败原因</p>
              <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-red-900">{job.errorMessage}</p>
              <p className="mt-3 text-xs font-medium text-red-800">
                建议：先核对源文件、外部依赖与失败节点；确认问题已解除后再重新入队。
              </p>
            </section>
          ) : (
            <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
              当前任务没有记录错误原因。
            </section>
          )}
          <section className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">任务上下文</p>
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-5 text-slate-600">
              {JSON.stringify({
                documentId: job.docId,
                knowledgeBaseId: job.kbId,
                operation: job.operation,
                attempts: job.attempts,
                maxAttempts: job.maxAttempts,
                nextRetryTime: job.nextRetryTime,
                createdAt: job.createTime,
                completedAt: job.completedAt,
                ...job.detail,
              }, null, 2)}
            </pre>
          </section>
          {job.retryable && (
            <button type="button" onClick={onRetry} className="inline-flex items-center gap-2 rounded-lg bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800">
              <RotateCcw className="h-4 w-4" />重新入队
            </button>
          )}
        </div>
      </aside>
    </div>
  );
}

function DrawerStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white/5 p-2">
      <p className="text-[9px] uppercase tracking-[0.1em] text-slate-400">{label}</p>
      <p className="mt-1 truncate font-mono text-xs text-white">{value}</p>
    </div>
  );
}
