import { useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowRight, Check, CheckCircle2, ChevronRight,
  CircleDot, Clock3, FileText, Gauge, Layers3, ListTree, Play, Plus,
  RefreshCcw, RefreshCw, RotateCcw, ServerCog, ShieldCheck, TimerReset,
  Trash2, TrendingUp, X, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/services/api";
import {
  fetchDocuments,
  fetchKnowledgeBases,
  reprocessDocument,
} from "@/services/knowledgeService";
import type { KnowledgeBase, KnowledgeDocument } from "@/types";
import { formatLocalDateTime, parseApiDateTime } from "@/utils/dateTime";

type Health = "healthy" | "warning" | "critical";

interface PipelineItem {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  createTime: string;
  health: {
    runs7d: number;
    successRate: number | null;
    lastStatus: string | null;
  };
}

interface TaskItem {
  id: string;
  pipelineId: string;
  pipelineName: string;
  kbId?: string;
  docId?: string;
  traceId?: string;
  parentTaskId?: string;
  attempt: number;
  sourceType: string;
  sourceFileName: string;
  status: string;
  chunkCount: number;
  durationMs: number;
  slaMs: number;
  slaBreached: boolean;
  errorMessage?: string;
  heartbeatAt?: string;
  startedAt?: string;
  completedAt?: string;
  createTime?: string;
  nodes?: TaskNode[];
}

interface TaskNode {
  id?: string;
  nodeId: string;
  nodeType: string;
  nodeOrder?: number;
  attempt: number;
  status: string;
  durationMs: number;
  message?: string;
  errorMessage?: string;
}

interface MonitorData {
  health: Health;
  generatedAt: string;
  summary: {
    total24h: number;
    running: number;
    successRate: number;
    p50DurationMs: number;
    p95DurationMs: number;
    slaBreachRate: number;
    chunks24h: number;
    stuckTasks: number;
    indexBacklog: number;
    scheduleErrors: number;
    retryRecoveryRate: number;
  };
  trend: Array<{
    timestamp: string;
    total: number;
    success: number;
    error: number;
    chunks: number;
    p95DurationMs: number;
  }>;
  nodeStats: Array<{
    nodeType: string;
    runs: number;
    errors: number;
    errorRate: number;
    avgDurationMs: number;
    p95DurationMs: number;
    retryRate: number;
  }>;
  failureReasons: Array<{ reason: string; count: number }>;
  incidents: Array<{
    id: string;
    kind: string;
    severity: string;
    title: string;
    detail: string;
    timestamp: string;
    taskId?: string;
  }>;
  recentTasks: TaskItem[];
}

const nodeLabels: Record<string, string> = {
  fetcher: "获取",
  parser: "解析",
  chunker: "切分",
  enricher: "元数据增强",
  enhancer: "内容增强",
  indexer: "索引写入",
};

const failureLabels: Record<string, string> = {
  timeout: "超时",
  network: "网络",
  parse: "解析",
  embedding: "向量化",
  index: "索引",
  validation: "配置校验",
  storage: "存储",
  unknown: "其他",
};

export default function IngestionPipelinePage() {
  const [tab, setTab] = useState<"monitor" | "tasks" | "pipelines">("monitor");
  const [monitor, setMonitor] = useState<MonitorData | null>(null);
  const [pipelines, setPipelines] = useState<PipelineItem[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedTask, setSelectedTask] = useState<TaskItem | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [showCreate, setShowCreate] = useState(false);
  const [showExecute, setShowExecute] = useState(false);
  const [activePipeline, setActivePipeline] = useState<PipelineItem | null>(null);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [kbId, setKbId] = useState("");
  const [docId, setDocId] = useState("");
  const [executing, setExecuting] = useState(false);

  const load = async (quiet = false) => {
    quiet ? setRefreshing(true) : setLoading(true);
    try {
      const [monitorData, pipelineData, taskData] = await Promise.all([
        api.get("/api/admin/ingestion/monitor/overview"),
        api.get("/api/admin/ingestion/pipelines?pageSize=100"),
        api.get("/api/admin/ingestion/tasks?pageSize=100"),
      ]) as unknown as [
        MonitorData,
        { rows: PipelineItem[] },
        { rows: TaskItem[] },
      ];
      setMonitor(monitorData);
      setPipelines(pipelineData.rows);
      setTasks(taskData.rows);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "流程监测数据加载失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const openTask = async (task: TaskItem) => {
    try {
      const detail = await api.get(`/api/admin/ingestion/tasks/${task.id}`) as TaskItem;
      setSelectedTask(detail);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "任务详情加载失败");
    }
  };

  const retryTask = async (task: TaskItem) => {
    try {
      const retried = await api.post(`/api/admin/ingestion/tasks/${task.id}/retry`) as TaskItem;
      toast.success(retried.status === "success" ? "重试成功" : "重试已完成，请检查失败节点");
      setSelectedTask(null);
      await load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "任务重试失败");
    }
  };

  const createPipeline = async () => {
    if (!newName.trim()) return;
    const types = ["fetcher", "parser", "chunker", "enricher", "enhancer", "indexer"];
    try {
      await api.post("/api/admin/ingestion/pipelines", {
        name: newName.trim(),
        description: newDescription.trim(),
        nodes: types.map((nodeType, index) => ({
          node_id: `n${index + 1}`,
          node_type: nodeType,
          next_node_id: index < types.length - 1 ? `n${index + 2}` : null,
          settings_json: {
            timeout_ms: nodeType === "indexer" ? 300_000 : 120_000,
          },
        })),
      });
      toast.success("流水线已创建");
      setShowCreate(false);
      setNewName("");
      setNewDescription("");
      await load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "流水线创建失败");
    }
  };

  const deletePipeline = async (pipeline: PipelineItem) => {
    if (!window.confirm(`确定删除流水线“${pipeline.name}”吗？`)) return;
    try {
      await api.delete(`/api/admin/ingestion/pipelines/${pipeline.id}`);
      toast.success("流水线已删除");
      await load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "流水线删除失败");
    }
  };

  const openExecute = async (pipeline: PipelineItem) => {
    setActivePipeline(pipeline);
    setShowExecute(true);
    setKbId("");
    setDocId("");
    setDocs([]);
    try {
      setKbs(await fetchKnowledgeBases());
    } catch {
      setKbs([]);
    }
  };

  const changeKb = async (value: string) => {
    setKbId(value);
    setDocId("");
    if (!value) {
      setDocs([]);
      return;
    }
    try {
      setDocs(await fetchDocuments(value));
    } catch {
      setDocs([]);
    }
  };

  const executePipeline = async () => {
    if (!activePipeline || !docId) return;
    setExecuting(true);
    try {
      const result = await reprocessDocument(docId, activePipeline.id);
      toast.success(`执行成功，生成 ${result.chunkCount} 个分块`);
      setShowExecute(false);
      await load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "流水线执行失败");
      await load(true);
    } finally {
      setExecuting(false);
    }
  };

  const filteredTasks = useMemo(
    () => tasks.filter((task) => statusFilter === "all" || task.status === statusFilter),
    [tasks, statusFilter],
  );

  if (loading && !monitor) {
    return (
      <div className="min-h-screen bg-[#f5f7f8] p-8">
        <div className="animate-pulse space-y-5">
          <div className="h-32 rounded-2xl bg-slate-200" />
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="h-64 rounded-xl bg-slate-200 lg:col-span-2" />
            <div className="h-64 rounded-xl bg-slate-200" />
          </div>
        </div>
      </div>
    );
  }

  const summary = monitor?.summary;
  return (
    <div className="min-h-screen bg-[#f5f7f8] text-slate-900">
      <header className="border-b border-slate-200 bg-white px-5 py-5 lg:px-8">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.22em] text-indigo-700">
              <span className="h-px w-7 bg-indigo-600" />
              Ingestion control plane
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-slate-950">
              流程监测与保障
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              从数据获取到索引一致性，持续监测每个节点、每次重试和每条异常。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden text-[10px] text-slate-400 sm:inline">
              30 秒自动刷新 · {formatTime(monitor?.generatedAt)}
            </span>
            <button
              type="button"
              onClick={() => load(true)}
              className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 hover:text-slate-900"
              title="刷新"
            >
              <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            </button>
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-slate-950 px-4 text-xs font-semibold text-white hover:bg-indigo-800"
            >
              <Plus className="h-3.5 w-3.5" />
              新建流水线
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-5 py-6 lg:px-8">
        <section className={`overflow-hidden rounded-2xl border ${
          monitor?.health === "critical"
            ? "border-rose-900 bg-[#1e1116]"
            : monitor?.health === "warning"
              ? "border-amber-900 bg-[#1d1910]"
              : "border-slate-800 bg-slate-950"
        } text-white shadow-[0_22px_60px_-34px_rgba(15,23,42,0.7)]`}>
          <div className="grid lg:grid-cols-[300px_1fr]">
            <div className="border-b border-white/10 p-6 lg:border-b-0 lg:border-r">
              <div className="flex items-center gap-2">
                <HealthPulse health={monitor?.health || "healthy"} />
                <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-400">
                  End-to-end health
                </span>
              </div>
              <h2 className="mt-5 text-3xl font-semibold tracking-[-0.04em]">
                {monitor?.health === "critical"
                  ? "需要立即处理"
                  : monitor?.health === "warning"
                    ? "存在潜在风险"
                    : "全链路运行正常"}
              </h2>
              <p className="mt-3 text-xs leading-5 text-slate-400">
                {monitor?.health === "healthy"
                  ? "任务心跳、SLA 和外部索引一致性均处于安全范围。"
                  : `${summary?.stuckTasks || 0} 个失联任务，${summary?.indexBacklog || 0} 个索引恢复作业待处理。`}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-px bg-white/10 sm:grid-cols-3 xl:grid-cols-6">
              <HeroMetric label="24h 成功率" value={formatPercent(summary?.successRate)} icon={<ShieldCheck />} good={(summary?.successRate || 0) >= .95} />
              <HeroMetric label="P95 时延" value={formatDuration(summary?.p95DurationMs)} icon={<Clock3 />} good={(summary?.p95DurationMs || 0) <= 300_000} />
              <HeroMetric label="SLA 违约" value={formatPercent(summary?.slaBreachRate)} icon={<Gauge />} good={(summary?.slaBreachRate || 0) <= .02} />
              <HeroMetric label="运行中" value={String(summary?.running || 0)} icon={<Activity />} good={(summary?.stuckTasks || 0) === 0} />
              <HeroMetric label="索引积压" value={String(summary?.indexBacklog || 0)} icon={<ServerCog />} good={(summary?.indexBacklog || 0) === 0} />
              <HeroMetric label="重试恢复率" value={formatPercent(summary?.retryRecoveryRate)} icon={<TimerReset />} good={(summary?.retryRecoveryRate || 0) >= .9} />
            </div>
          </div>
        </section>

        <nav className="mt-6 flex items-center gap-1 border-b border-slate-200">
          {([
            ["monitor", "运行态势", Activity],
            ["tasks", "任务追踪", ListTree],
            ["pipelines", "流水线治理", Layers3],
          ] as const).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              onClick={() => setTab(value)}
              className={`relative flex items-center gap-2 px-4 py-3 text-xs font-semibold ${
                tab === value ? "text-indigo-800" : "text-slate-500 hover:text-slate-900"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
              {tab === value && (
                <span className="absolute inset-x-2 bottom-[-1px] h-0.5 bg-indigo-600" />
              )}
            </button>
          ))}
        </nav>

        {tab === "monitor" && monitor && (
          <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_380px]">
            <div className="space-y-5">
              <Panel
                title="24 小时吞吐与稳定性"
                subtitle={`${summary?.total24h || 0} 次运行 · ${summary?.chunks24h || 0} 个分块入库`}
              >
                <ThroughputChart points={monitor.trend} />
              </Panel>
              <Panel
                title="节点性能画像"
                subtitle="按 P95 时延排序，直接定位瓶颈和重试热点"
              >
                <NodeTable nodes={monitor.nodeStats} />
              </Panel>
              <Panel title="最近任务" subtitle="跨流水线查看执行状态和 SLA">
                <TaskTable tasks={monitor.recentTasks.slice(0, 12)} onOpen={openTask} />
              </Panel>
            </div>
            <aside className="space-y-5">
              <Panel
                title="活动事故"
                subtitle="失联、失败和外部索引不一致统一收敛"
                action={
                  <span className={`rounded-full px-2 py-1 text-[10px] font-bold ${
                    monitor.incidents.length
                      ? "bg-rose-50 text-rose-700"
                      : "bg-emerald-50 text-emerald-700"
                  }`}>
                    {monitor.incidents.length} 条
                  </span>
                }
              >
                <div className="space-y-2">
                  {monitor.incidents.slice(0, 10).map((incident) => (
                    <button
                      key={`${incident.kind}-${incident.id}`}
                      type="button"
                      onClick={() => {
                        const task = tasks.find((item) => item.id === incident.taskId);
                        if (task) void openTask(task);
                      }}
                      className="flex w-full items-start gap-3 rounded-lg border border-slate-100 p-3 text-left hover:border-rose-200 hover:bg-rose-50/30"
                    >
                      <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                        incident.severity === "critical"
                          ? "bg-rose-100 text-rose-700"
                          : "bg-amber-100 text-amber-700"
                      }`}>
                        <AlertTriangle className="h-3.5 w-3.5" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <b className="block truncate text-xs text-slate-800">
                          {incident.title}
                        </b>
                        <small className="mt-1 block line-clamp-2 text-[10px] leading-4 text-slate-400">
                          {incident.detail}
                        </small>
                      </span>
                      {incident.taskId && <ChevronRight className="mt-1 h-3.5 w-3.5 text-slate-300" />}
                    </button>
                  ))}
                  {!monitor.incidents.length && (
                    <EmptyState
                      icon={<CheckCircle2 />}
                      title="当前没有活动事故"
                      detail="任务心跳和外部索引均正常。"
                    />
                  )}
                </div>
              </Panel>
              <Panel title="失败根因" subtitle="近 24 小时异常自动归类">
                <FailureReasons reasons={monitor.failureReasons} />
              </Panel>
              <div className="grid grid-cols-2 gap-3">
                <MiniStat label="失联任务" value={summary?.stuckTasks || 0} danger={(summary?.stuckTasks || 0) > 0} />
                <MiniStat label="调度失败" value={summary?.scheduleErrors || 0} danger={(summary?.scheduleErrors || 0) > 0} />
              </div>
            </aside>
          </div>
        )}

        {tab === "tasks" && (
          <div className="mt-5">
            <Panel
              title="任务追踪"
              subtitle={`${filteredTasks.length} 条任务，支持节点级下钻和失败重跑`}
              action={
                <select
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value)}
                  className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-[10px] outline-none"
                >
                  <option value="all">全部状态</option>
                  <option value="running">运行中</option>
                  <option value="success">成功</option>
                  <option value="error">失败</option>
                  <option value="timeout">超时</option>
                </select>
              }
            >
              <TaskTable tasks={filteredTasks} onOpen={openTask} />
            </Panel>
          </div>
        )}

        {tab === "pipelines" && (
          <div className="mt-5 grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {pipelines.map((pipeline) => (
              <article
                key={pipeline.id}
                className="rounded-xl border border-slate-200 bg-white p-5 shadow-[0_10px_30px_-26px_rgba(15,23,42,.4)]"
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-50 text-indigo-700">
                    <Layers3 className="h-5 w-5" />
                  </span>
                  <StatusBadge status={pipeline.health.lastStatus || (pipeline.enabled ? "ready" : "disabled")} />
                </div>
                <h2 className="mt-4 text-sm font-semibold text-slate-900">{pipeline.name}</h2>
                <p className="mt-1 min-h-8 text-[11px] leading-4 text-slate-400">
                  {pipeline.description || "标准六阶段企业入库流水线"}
                </p>
                <div className="mt-4 flex items-center gap-1 overflow-hidden">
                  {["fetcher", "parser", "chunker", "enricher", "enhancer", "indexer"].map((node, index) => (
                    <div key={node} className="flex min-w-0 flex-1 items-center">
                      <span className="truncate rounded bg-slate-100 px-1.5 py-1 text-[8px] text-slate-500">
                        {nodeLabels[node]}
                      </span>
                      {index < 5 && <ArrowRight className="h-3 w-3 shrink-0 text-slate-300" />}
                    </div>
                  ))}
                </div>
                <div className="mt-5 grid grid-cols-2 gap-3 border-t border-slate-100 pt-4">
                  <div>
                    <p className="font-mono text-base font-semibold text-slate-800">{pipeline.health.runs7d}</p>
                    <p className="text-[9px] text-slate-400">7 天运行</p>
                  </div>
                  <div>
                    <p className="font-mono text-base font-semibold text-slate-800">
                      {pipeline.health.successRate === null ? "—" : formatPercent(pipeline.health.successRate)}
                    </p>
                    <p className="text-[9px] text-slate-400">成功率</p>
                  </div>
                </div>
                <div className="mt-4 flex gap-2">
                  <button
                    type="button"
                    onClick={() => openExecute(pipeline)}
                    disabled={!pipeline.enabled}
                    className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-slate-950 px-3 py-2 text-[10px] font-semibold text-white disabled:opacity-40"
                  >
                    <Play className="h-3 w-3 fill-current" />
                    执行
                  </button>
                  <button
                    type="button"
                    onClick={() => deletePipeline(pipeline)}
                    className="grid w-9 place-items-center rounded-lg border border-slate-200 text-slate-400 hover:border-rose-200 hover:text-rose-600"
                    title="删除"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </article>
            ))}
            {!pipelines.length && (
              <div className="lg:col-span-2 2xl:col-span-3">
                <EmptyState
                  icon={<Layers3 />}
                  title="还没有流水线"
                  detail="创建标准六阶段流水线后即可开始全链路监测。"
                />
              </div>
            )}
          </div>
        )}
      </main>

      {selectedTask && (
        <TaskDrawer
          task={selectedTask}
          onClose={() => setSelectedTask(null)}
          onRetry={() => retryTask(selectedTask)}
        />
      )}
      {showCreate && (
        <Modal title="新建标准流水线" onClose={() => setShowCreate(false)}>
          <p className="mb-4 text-xs leading-5 text-slate-500">
            自动创建获取、解析、切分、增强和索引六个节点，并启用节点级超时保护。
          </p>
          <label className="block text-[10px] font-semibold text-slate-500">
            流水线名称
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm outline-none focus:border-indigo-500"
              placeholder="例如：产品文档标准入库"
              autoFocus
            />
          </label>
          <label className="mt-3 block text-[10px] font-semibold text-slate-500">
            说明
            <textarea
              value={newDescription}
              onChange={(event) => setNewDescription(event.target.value)}
              className="mt-1.5 h-20 w-full resize-none rounded-lg border border-slate-200 p-3 text-sm outline-none focus:border-indigo-500"
              placeholder="描述数据范围和运行目标"
            />
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" onClick={() => setShowCreate(false)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs">取消</button>
            <button type="button" onClick={createPipeline} disabled={!newName.trim()} className="rounded-lg bg-slate-950 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">创建流水线</button>
          </div>
        </Modal>
      )}
      {showExecute && activePipeline && (
        <Modal title={`执行 · ${activePipeline.name}`} onClose={() => setShowExecute(false)}>
          <label className="block text-[10px] font-semibold text-slate-500">
            知识库
            <select
              value={kbId}
              onChange={(event) => changeKb(event.target.value)}
              className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none"
            >
              <option value="">选择知识库</option>
              {kbs.map((kb) => <option key={kb.id} value={kb.id}>{kb.name}</option>)}
            </select>
          </label>
          <label className="mt-3 block text-[10px] font-semibold text-slate-500">
            待处理文档
            <select
              value={docId}
              onChange={(event) => setDocId(event.target.value)}
              disabled={!kbId}
              className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none disabled:bg-slate-50"
            >
              <option value="">选择文档</option>
              {docs.map((doc) => <option key={doc.id} value={doc.id}>{doc.docName}</option>)}
            </select>
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" onClick={() => setShowExecute(false)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs">取消</button>
            <button type="button" onClick={executePipeline} disabled={!docId || executing} className="inline-flex items-center gap-2 rounded-lg bg-slate-950 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">
              {executing && <RefreshCcw className="h-3 w-3 animate-spin" />}
              {executing ? "执行中" : "开始执行"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function Panel({
  title, subtitle, action, children,
}: {
  title: string;
  subtitle: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-[0_10px_30px_-26px_rgba(15,23,42,.45)]">
      <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          <p className="mt-1 text-[11px] text-slate-400">{subtitle}</p>
        </div>
        {action}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

function HealthPulse({ health }: { health: Health }) {
  const color = health === "healthy"
    ? "bg-emerald-400 shadow-emerald-400/60"
    : health === "warning" ? "bg-amber-400 shadow-amber-400/60" : "bg-rose-400 shadow-rose-400/60";
  return <span className={`h-2.5 w-2.5 rounded-full shadow-[0_0_0_5px] ${color}`} />;
}

function HeroMetric({
  label, value, icon, good,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  good: boolean;
}) {
  return (
    <div className="bg-black/10 p-5">
      <span className={`[&>svg]:h-4 [&>svg]:w-4 ${good ? "text-emerald-400" : "text-rose-400"}`}>{icon}</span>
      <p className="mt-5 font-mono text-xl font-semibold text-white">{value}</p>
      <p className="mt-1 text-[9px] text-slate-500">{label}</p>
    </div>
  );
}

function ThroughputChart({ points }: { points: MonitorData["trend"] }) {
  const maxTotal = Math.max(1, ...points.map((point) => point.total));
  return (
    <div>
      <div className="flex h-52 items-end gap-1.5 border-b border-slate-200 px-1">
        {points.map((point) => (
          <div key={point.timestamp} className="group relative flex h-full min-w-0 flex-1 items-end">
            <div
              className="relative w-full overflow-hidden rounded-t bg-slate-100 transition group-hover:bg-slate-200"
              style={{ height: `${Math.max(3, point.total / maxTotal * 100)}%` }}
            >
              <div
                className="absolute inset-x-0 bottom-0 bg-indigo-500"
                style={{ height: `${point.total ? point.success / point.total * 100 : 0}%` }}
              />
              <div
                className="absolute inset-x-0 top-0 bg-rose-400"
                style={{ height: `${point.total ? point.error / point.total * 100 : 0}%` }}
              />
            </div>
            <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 hidden w-32 -translate-x-1/2 rounded-lg bg-slate-950 p-2 text-[9px] text-white shadow-xl group-hover:block">
              <b>{parseApiDateTime(point.timestamp)?.getHours() ?? "—"}:00</b>
              <p className="mt-1 text-slate-400">总量 {point.total} · 成功 {point.success} · 失败 {point.error}</p>
              <p className="text-slate-400">P95 {formatDuration(point.p95DurationMs)}</p>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-center gap-5 text-[9px] text-slate-400">
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-indigo-500" />成功</span>
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-rose-400" />失败</span>
        <span>柱高 = 每小时任务量</span>
      </div>
    </div>
  );
}

function NodeTable({ nodes }: { nodes: MonitorData["nodeStats"] }) {
  if (!nodes.length) return <EmptyState icon={<CircleDot />} title="暂无节点数据" detail="执行流水线后将生成节点性能画像。" />;
  const maxP95 = Math.max(1, ...nodes.map((node) => node.p95DurationMs));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] text-left">
        <thead>
          <tr className="text-[9px] uppercase tracking-[0.12em] text-slate-400">
            <th className="pb-3 font-semibold">节点</th>
            <th className="pb-3 font-semibold">P95 时延</th>
            <th className="pb-3 text-right font-semibold">运行</th>
            <th className="pb-3 text-right font-semibold">错误率</th>
            <th className="pb-3 text-right font-semibold">重试率</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {nodes.map((node) => (
            <tr key={node.nodeType}>
              <td className="py-3 pr-5">
                <span className="text-xs font-semibold text-slate-700">{nodeLabels[node.nodeType] || node.nodeType}</span>
              </td>
              <td className="w-1/2 py-3 pr-5">
                <div className="flex items-center gap-3">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-indigo-500" style={{ width: `${node.p95DurationMs / maxP95 * 100}%` }} />
                  </div>
                  <span className="w-16 text-right font-mono text-[10px] text-slate-600">{formatDuration(node.p95DurationMs)}</span>
                </div>
              </td>
              <td className="py-3 text-right font-mono text-xs text-slate-600">{node.runs}</td>
              <td className={`py-3 text-right font-mono text-xs ${node.errorRate > .05 ? "text-rose-600" : "text-slate-600"}`}>{formatPercent(node.errorRate)}</td>
              <td className={`py-3 text-right font-mono text-xs ${node.retryRate > .1 ? "text-amber-600" : "text-slate-600"}`}>{formatPercent(node.retryRate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TaskTable({
  tasks, onOpen,
}: {
  tasks: TaskItem[];
  onOpen: (task: TaskItem) => void;
}) {
  if (!tasks.length) return <EmptyState icon={<FileText />} title="暂无任务" detail="运行流水线后可在这里查看完整链路。" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[780px] text-left">
        <thead>
          <tr className="text-[9px] uppercase tracking-[0.12em] text-slate-400">
            <th className="pb-3 font-semibold">状态</th>
            <th className="pb-3 font-semibold">流水线 / 来源</th>
            <th className="pb-3 font-semibold">Trace</th>
            <th className="pb-3 text-right font-semibold">分块</th>
            <th className="pb-3 text-right font-semibold">耗时</th>
            <th className="pb-3 text-right font-semibold">开始时间</th>
            <th />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {tasks.map((task) => (
            <tr key={task.id} className="group">
              <td className="py-3 pr-4"><StatusBadge status={task.status} /></td>
              <td className="max-w-xs py-3 pr-4">
                <p className="truncate text-xs font-semibold text-slate-700">{task.pipelineName || task.pipelineId}</p>
                <p className="mt-1 truncate text-[9px] text-slate-400">{task.sourceFileName || task.sourceType} · attempt {task.attempt}</p>
              </td>
              <td className="py-3 pr-4 font-mono text-[9px] text-slate-400">{task.traceId?.slice(0, 10) || "—"}</td>
              <td className="py-3 text-right font-mono text-xs text-slate-600">{task.chunkCount}</td>
              <td className={`py-3 text-right font-mono text-xs ${task.slaBreached ? "text-rose-600" : "text-slate-600"}`}>{formatDuration(task.durationMs)}</td>
              <td className="py-3 text-right text-[10px] text-slate-400">{formatTime(task.startedAt)}</td>
              <td className="py-3 pl-3 text-right">
                <button type="button" onClick={() => onOpen(task)} className="rounded p-1 text-slate-300 hover:bg-slate-100 hover:text-slate-700">
                  <ChevronRight className="h-4 w-4" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FailureReasons({ reasons }: { reasons: MonitorData["failureReasons"] }) {
  if (!reasons.length) return <EmptyState icon={<Check />} title="没有失败根因" detail="近 24 小时未记录异常。" />;
  const total = reasons.reduce((sum, item) => sum + item.count, 0);
  return (
    <div className="space-y-3">
      {reasons.map((item) => (
        <div key={item.reason}>
          <div className="flex justify-between text-[10px]">
            <span className="text-slate-500">{failureLabels[item.reason] || item.reason}</span>
            <span className="font-mono font-semibold text-slate-700">{item.count}</span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div className="h-full rounded-full bg-rose-400" style={{ width: `${item.count / total * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function TaskDrawer({
  task, onClose, onRetry,
}: {
  task: TaskItem;
  onClose: () => void;
  onRetry: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/30" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="h-full w-full max-w-xl overflow-y-auto bg-white shadow-2xl">
        <header className="sticky top-0 z-10 flex items-start justify-between border-b border-slate-200 bg-white px-6 py-5">
          <div>
            <div className="flex items-center gap-2"><StatusBadge status={task.status} /><span className="font-mono text-[9px] text-slate-400">{task.id}</span></div>
            <h2 className="mt-2 text-lg font-semibold text-slate-900">{task.pipelineName || task.pipelineId}</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:bg-slate-100"><X className="h-4 w-4" /></button>
        </header>
        <div className="space-y-6 p-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <DetailStat label="总耗时" value={formatDuration(task.durationMs)} />
            <DetailStat label="SLA" value={formatDuration(task.slaMs)} danger={task.slaBreached} />
            <DetailStat label="分块" value={String(task.chunkCount)} />
            <DetailStat label="尝试" value={String(task.attempt)} />
          </div>
          <section>
            <h3 className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-400">链路标识</h3>
            <div className="mt-2 rounded-lg bg-slate-950 p-3 font-mono text-[10px] leading-5 text-slate-300">
              <p>trace_id: {task.traceId || "—"}</p>
              <p>parent_task: {task.parentTaskId || "—"}</p>
              <p>source: {task.sourceFileName || task.sourceType}</p>
            </div>
          </section>
          <section>
            <h3 className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-400">节点时间线</h3>
            <div className="mt-3">
              {(task.nodes || []).map((node, index) => (
                <div key={node.id || node.nodeId} className="relative flex gap-4 pb-5">
                  {index < (task.nodes || []).length - 1 && <span className="absolute left-[11px] top-6 h-full w-px bg-slate-200" />}
                  <span className={`z-[1] grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                    node.status === "success" ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
                  }`}>
                    {node.status === "success" ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-xs font-semibold text-slate-700">{nodeLabels[node.nodeType] || node.nodeType}</p>
                      <span className="font-mono text-[10px] text-slate-400">{formatDuration(node.durationMs)} · {node.attempt} attempt</span>
                    </div>
                    {(node.errorMessage || node.message) && <p className={`mt-1 text-[10px] leading-4 ${node.errorMessage ? "text-rose-600" : "text-slate-400"}`}>{node.errorMessage || node.message}</p>}
                  </div>
                </div>
              ))}
              {!task.nodes?.length && <EmptyState icon={<ListTree />} title="没有节点记录" detail="旧任务或启动前失败的任务可能没有节点数据。" />}
            </div>
          </section>
          {task.errorMessage && (
            <section className="rounded-lg border border-rose-200 bg-rose-50 p-4">
              <h3 className="text-xs font-semibold text-rose-800">失败原因</h3>
              <p className="mt-2 break-words font-mono text-[10px] leading-5 text-rose-700">{task.errorMessage}</p>
            </section>
          )}
          {["error", "timeout"].includes(task.status) && (
            <button type="button" onClick={onRetry} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-950 py-3 text-xs font-semibold text-white">
              <RotateCcw className="h-3.5 w-3.5" />按原始参数重试
            </button>
          )}
        </div>
      </aside>
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/35 p-4" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
        <div className="mb-4 flex items-center justify-between"><h2 className="text-base font-semibold text-slate-900">{title}</h2><button type="button" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100"><X className="h-4 w-4" /></button></div>
        {children}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    success: "bg-emerald-50 text-emerald-700",
    running: "bg-indigo-50 text-indigo-700",
    error: "bg-rose-50 text-rose-700",
    timeout: "bg-amber-50 text-amber-700",
    ready: "bg-slate-100 text-slate-600",
    disabled: "bg-slate-100 text-slate-400",
  };
  const labels: Record<string, string> = { success: "成功", running: "运行中", error: "失败", timeout: "超时", ready: "就绪", disabled: "已停用" };
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-[9px] font-semibold ${styles[status] || styles.ready}`}>{status === "running" && <Activity className="h-2.5 w-2.5" />}{labels[status] || status}</span>;
}

function MiniStat({ label, value, danger }: { label: string; value: number; danger: boolean }) {
  return <div className={`rounded-xl border p-4 ${danger ? "border-rose-200 bg-rose-50" : "border-slate-200 bg-white"}`}><p className={`font-mono text-2xl font-semibold ${danger ? "text-rose-700" : "text-slate-800"}`}>{value}</p><p className="mt-1 text-[9px] text-slate-400">{label}</p></div>;
}

function DetailStat({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return <div className={`rounded-lg p-3 ${danger ? "bg-rose-50" : "bg-slate-50"}`}><p className={`font-mono text-sm font-semibold ${danger ? "text-rose-700" : "text-slate-800"}`}>{value}</p><p className="mt-1 text-[9px] text-slate-400">{label}</p></div>;
}

function EmptyState({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <div className="grid min-h-36 place-items-center text-center"><div><span className="mx-auto grid h-9 w-9 place-items-center rounded-full bg-slate-100 text-slate-400 [&>svg]:h-4 [&>svg]:w-4">{icon}</span><p className="mt-3 text-xs font-semibold text-slate-600">{title}</p><p className="mt-1 text-[10px] text-slate-400">{detail}</p></div></div>;
}

function formatPercent(value = 0) {
  return `${(value * 100).toFixed(value < .1 ? 1 : 0)}%`;
}

function formatDuration(value = 0) {
  if (!value) return "0ms";
  if (value >= 60_000) return `${(value / 60_000).toFixed(1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function formatTime(value?: string) {
  return formatLocalDateTime(value, {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false,
  });
}
