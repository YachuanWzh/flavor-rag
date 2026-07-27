import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, BarChart3, CheckCircle2, Clock, Database,
  Gauge, RefreshCw, RotateCcw, Search, Sparkles,
} from "lucide-react";
import { api } from "@/services/api";

interface MonitoringSummary {
  windowHours: number;
  rag: {
    total: number;
    success: number;
    failed: number;
    successRate: number | null;
    avgTotalMs: number;
    p95TotalMs: number;
    avgSearchMs: number;
    avgLlmMs: number;
  };
  ingestion: {
    queue: Record<string, number>;
    windowCompleted: number;
    windowDead: number;
    avgJobMs: number;
  };
  documents: Record<string, number>;
}

interface TimeseriesPoint {
  time: string;
  ragTotal: number;
  ragFailed: number;
  ragAvgMs: number;
  jobsCompleted: number;
  jobsDead: number;
}

interface IngestionJobItem {
  id: string;
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
}

const WINDOW_OPTIONS = [
  { label: "近 1 小时", hours: 1 },
  { label: "近 24 小时", hours: 24 },
  { label: "近 7 天", hours: 168 },
];

const JOB_STATUS_STYLES: Record<string, string> = {
  QUEUED: "text-blue-600 bg-blue-50",
  RUNNING: "text-yellow-600 bg-yellow-50",
  RETRY: "text-orange-600 bg-orange-50",
  SUCCESS: "text-green-600 bg-green-50",
  DEAD: "text-red-600 bg-red-50",
};

const Card = ({ icon: Icon, label, value, sub, color }: {
  icon: any; label: string; value: number | string; sub?: string; color: string;
}) => (
  <div className="bg-white rounded-xl border p-5 hover:shadow-sm transition-shadow">
    <div className="flex items-center justify-between mb-2">
      <span className="text-xs text-gray-500">{label}</span>
      <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${color}`}>
        <Icon size={18} className="text-white" />
      </div>
    </div>
    <div className="text-2xl font-bold text-gray-900">{value}</div>
    {sub && <div className="mt-1 text-xs text-gray-400">{sub}</div>}
  </div>
);

function BarChart({ points, title }: { points: TimeseriesPoint[]; title: string }) {
  const max = Math.max(1, ...points.map((p) => p.ragTotal));
  return (
    <div className="bg-white rounded-xl border p-5">
      <div className="mb-3 text-sm font-semibold text-gray-600">{title}</div>
      <div className="flex h-32 items-end gap-[2px]">
        {points.map((p, i) => {
          const okHeight = ((p.ragTotal - p.ragFailed) / max) * 100;
          const failHeight = (p.ragFailed / max) * 100;
          return (
            <div
              key={i}
              className="group relative flex flex-1 flex-col justify-end h-full"
              title={`${p.time}\n请求 ${p.ragTotal} 次，失败 ${p.ragFailed} 次，平均 ${p.ragAvgMs} ms`}
            >
              <div className="w-full rounded-t-sm bg-red-400" style={{ height: `${failHeight}%` }} />
              <div className="w-full bg-teal-500" style={{ height: `${okHeight}%` }} />
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-gray-400">
        <span>{points[0]?.time?.slice(5, 16)}</span>
        <span>{points[points.length - 1]?.time?.slice(5, 16)}</span>
      </div>
    </div>
  );
}

function JobsChart({ points }: { points: TimeseriesPoint[] }) {
  const max = Math.max(1, ...points.map((p) => p.jobsCompleted + p.jobsDead));
  return (
    <div className="bg-white rounded-xl border p-5">
      <div className="mb-3 text-sm font-semibold text-gray-600">摄取任务完成量</div>
      <div className="flex h-32 items-end gap-[2px]">
        {points.map((p, i) => (
          <div
            key={i}
            className="flex flex-1 flex-col justify-end h-full"
            title={`${p.time}\n成功 ${p.jobsCompleted}，DEAD ${p.jobsDead}`}
          >
            <div
              className="w-full rounded-t-sm bg-red-400"
              style={{ height: `${(p.jobsDead / max) * 100}%` }}
            />
            <div
              className="w-full bg-indigo-500"
              style={{ height: `${(p.jobsCompleted / max) * 100}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-gray-400">
        <span>{points[0]?.time?.slice(5, 16)}</span>
        <span>{points[points.length - 1]?.time?.slice(5, 16)}</span>
      </div>
    </div>
  );
}

export default function MonitoringPage() {
  const [hours, setHours] = useState(24);
  const [summary, setSummary] = useState<MonitoringSummary | null>(null);
  const [points, setPoints] = useState<TimeseriesPoint[]>([]);
  const [jobs, setJobs] = useState<IngestionJobItem[]>([]);
  const [jobStatus, setJobStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      const [sum, ts, jobList] = await Promise.all([
        api.get(`/api/admin/monitoring/summary?hours=${hours}`),
        api.get(`/api/admin/monitoring/timeseries?hours=${hours}&buckets=48`),
        api.get(`/api/admin/monitoring/ingestion-jobs?status=${jobStatus}&limit=20`),
      ]);
      setSummary(sum as any);
      setPoints((ts as any).points || []);
      setJobs((jobList as any).items || []);
    } catch (e: any) {
      setError(e?.message || "加载监控数据失败");
    } finally {
      setLoading(false);
    }
  }, [hours, jobStatus]);

  useEffect(() => {
    setLoading(true);
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  const retryJob = async (id: string) => {
    try {
      await api.post(`/api/admin/monitoring/ingestion-jobs/${id}/retry`);
      load();
    } catch (e: any) {
      setError(e?.message || "重试失败");
    }
  };

  if (loading) return <div className="p-8 text-gray-400">加载中...</div>;

  const queue = summary?.ingestion.queue || {};
  const successRate =
    summary?.rag.successRate != null
      ? `${(summary.rag.successRate * 100).toFixed(1)}%`
      : "--";

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-lg font-bold">系统监控</h2>
        <div className="flex items-center gap-2">
          {WINDOW_OPTIONS.map((opt) => (
            <button
              key={opt.hours}
              onClick={() => setHours(opt.hours)}
              className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
                hours === opt.hours
                  ? "bg-teal-600 text-white"
                  : "border bg-white text-gray-600 hover:bg-gray-50"
              }`}
            >
              {opt.label}
            </button>
          ))}
          <button
            onClick={load}
            className="rounded-lg border bg-white p-2 text-gray-500 hover:bg-gray-50"
            title="刷新"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">{error}</div>
      )}

      {/* RAG metrics */}
      <h3 className="mb-3 text-sm font-semibold text-gray-600">RAG 检索问答</h3>
      <div className="mb-6 grid grid-cols-4 gap-4">
        <Card icon={Activity} label="请求总数" value={summary?.rag.total ?? 0} color="bg-teal-500" />
        <Card icon={CheckCircle2} label="成功率" value={successRate}
          sub={`失败 ${summary?.rag.failed ?? 0} 次`} color="bg-green-500" />
        <Card icon={BarChart3} label="平均耗时" value={`${summary?.rag.avgTotalMs ?? 0} ms`}
          sub={`P95 ${summary?.rag.p95TotalMs ?? 0} ms`} color="bg-rose-500" />
        <Card icon={Search} label="检索 / LLM 耗时"
          value={`${summary?.rag.avgSearchMs ?? 0} / ${summary?.rag.avgLlmMs ?? 0} ms`}
          color="bg-violet-500" />
      </div>

      {/* Charts */}
      <div className="mb-6 grid grid-cols-2 gap-4">
        <BarChart points={points} title="RAG 请求量（绿=成功，红=失败）" />
        <JobsChart points={points} />
      </div>

      {/* Ingestion queue */}
      <h3 className="mb-3 text-sm font-semibold text-gray-600">异步摄取队列</h3>
      <div className="mb-6 grid grid-cols-5 gap-4">
        <Card icon={Clock} label="排队 QUEUED" value={queue.QUEUED ?? 0} color="bg-blue-500" />
        <Card icon={Gauge} label="执行中 RUNNING" value={queue.RUNNING ?? 0} color="bg-yellow-500" />
        <Card icon={RotateCcw} label="待重试 RETRY" value={queue.RETRY ?? 0} color="bg-orange-500" />
        <Card icon={AlertTriangle} label="死信 DEAD" value={queue.DEAD ?? 0} color="bg-red-500" />
        <Card icon={Sparkles} label="窗口内完成" value={summary?.ingestion.windowCompleted ?? 0}
          sub={`平均 ${summary?.ingestion.avgJobMs ?? 0} ms`} color="bg-indigo-500" />
      </div>

      {/* Document status */}
      <h3 className="mb-3 text-sm font-semibold text-gray-600">文档状态分布</h3>
      <div className="mb-6 grid grid-cols-4 gap-4">
        <Card icon={Database} label="排队中" value={summary?.documents.queued ?? 0} color="bg-blue-500" />
        <Card icon={Database} label="处理中" value={summary?.documents.running ?? 0} color="bg-yellow-500" />
        <Card icon={Database} label="已完成" value={summary?.documents.success ?? 0} color="bg-green-500" />
        <Card icon={Database} label="失败" value={summary?.documents.failed ?? 0} color="bg-red-500" />
      </div>

      {/* Job list */}
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-600">最近摄取任务</h3>
        <select
          value={jobStatus}
          onChange={(e) => setJobStatus(e.target.value)}
          className="rounded-lg border bg-white px-2 py-1 text-xs text-gray-600"
        >
          <option value="">全部状态</option>
          <option value="QUEUED">QUEUED</option>
          <option value="RUNNING">RUNNING</option>
          <option value="RETRY">RETRY</option>
          <option value="SUCCESS">SUCCESS</option>
          <option value="DEAD">DEAD</option>
        </select>
      </div>
      <div className="overflow-hidden rounded-xl border bg-white">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs text-gray-500">
            <tr>
              <th className="px-4 py-2.5">文档</th>
              <th className="px-4 py-2.5">操作</th>
              <th className="px-4 py-2.5">状态</th>
              <th className="px-4 py-2.5">尝试</th>
              <th className="px-4 py-2.5">耗时</th>
              <th className="px-4 py-2.5">错误</th>
              <th className="px-4 py-2.5">创建时间</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-400">
                  暂无任务
                </td>
              </tr>
            )}
            {jobs.map((job) => (
              <tr key={job.id} className="border-t">
                <td className="max-w-[200px] truncate px-4 py-2.5" title={job.docName || job.docId}>
                  {job.docName || job.docId}
                </td>
                <td className="px-4 py-2.5 text-xs text-gray-500">{job.operation}</td>
                <td className="px-4 py-2.5">
                  <span className={`rounded px-2 py-0.5 text-xs ${JOB_STATUS_STYLES[job.status] || "bg-gray-50 text-gray-500"}`}>
                    {job.status}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-xs text-gray-500">
                  {job.attempts}/{job.maxAttempts}
                </td>
                <td className="px-4 py-2.5 text-xs text-gray-500">
                  {job.durationMs != null ? `${job.durationMs} ms` : "--"}
                </td>
                <td className="max-w-[240px] truncate px-4 py-2.5 text-xs text-red-500" title={job.errorMessage}>
                  {job.errorMessage || "--"}
                </td>
                <td className="px-4 py-2.5 text-xs text-gray-500">{job.createTime}</td>
                <td className="px-4 py-2.5 text-right">
                  {(job.status === "DEAD" || job.status === "RETRY") && (
                    <button
                      onClick={() => retryJob(job.id)}
                      className="rounded border px-2 py-1 text-xs text-teal-700 hover:bg-teal-50"
                    >
                      重新入队
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
