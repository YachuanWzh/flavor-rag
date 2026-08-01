import { useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3,
  Check, ChevronDown, Clock3, Download, FlaskConical, Gauge, Layers3,
  Play, RefreshCw, Search, ShieldCheck, Sparkles, Target, ThumbsDown,
  UserRound, X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/services/api";
import { fetchKnowledgeBases } from "@/services/knowledgeService";
import type { KnowledgeBase } from "@/types";

type MetricMap = Record<string, number>;

interface EvalCase {
  id: string;
  question: string;
  category: string;
  difficulty: string;
  tags: string[];
  answerable: boolean;
  active: boolean;
}

interface Gate {
  metric: string;
  operator: string;
  threshold: number;
  value: number;
  passed: boolean;
}

interface RunResult {
  id: string;
  knowledgeBase: { id: string; name: string };
  datasetVersion: string;
  status: string;
  gateStatus: "passed" | "failed" | "pending";
  config: { top_k?: number; label?: string };
  metrics: MetricMap;
  bySlice: {
    category?: Record<string, MetricMap>;
    difficulty?: Record<string, MetricMap>;
  };
  gates: { passed?: number; total?: number; checks?: Gate[] };
  deltas: MetricMap;
  durationMs: number;
  createdAt?: string;
  results?: Array<{
    case_id: string;
    question: string;
    category: string;
    difficulty: string;
    latency_ms: number;
    case_metrics: { passed: boolean; recall: number };
  }>;
}

interface Overview {
  datasetVersion: string;
  caseCount: number;
  activeCaseCount: number;
  negativeCaseCount: number;
  categories: Record<string, number>;
  difficulties: Record<string, number>;
  negativeFeedbackCandidates: number;
  latestRun: RunResult | null;
  cases: EvalCase[];
}

interface TrendData {
  points: Array<{
    id: string;
    timestamp?: string;
    kbName: string;
    gateStatus: string;
    metrics: MetricMap;
  }>;
  alerts: Array<{
    severity: string;
    metric: string;
    delta: number;
    message: string;
  }>;
}

interface FeedbackCandidate {
  id: string;
  question: string;
  suggestedCase: Record<string, unknown>;
}

interface QuestionAsset {
  id: string;
  conversationId: string;
  user: { id: string; username: string };
  question: string;
  answer: { id: string; content: string; sourceCount: number } | null;
  feedback: { vote: number; reason?: string; comment?: string } | null;
  qualityScore: number;
  label: "BAD_CASE" | "GOOD_CASE" | "GOLDEN" | "UNRATED";
  dataset: {
    id: string;
    caseType: "base" | "golden";
    reviewStatus: "generated" | "approved" | "needs_review";
    active: boolean;
  } | null;
  createdAt?: string;
}

interface QuestionAssetPage {
  items: QuestionAsset[];
  page: number;
  pageSize: number;
  total: number;
}

const metricLabels: Record<string, string> = {
  "precision@5": "Precision@5",
  "recall@5": "Recall@5",
  "hit_rate@5": "Hit Rate@5",
  "mrr@5": "MRR@5",
  "map@5": "MAP@5",
  "ndcg@5": "NDCG@5",
  "doc_recall@5": "文档 Recall@5",
  retrieval_coverage: "检索覆盖率",
  refusal_recall: "拒答召回率",
  refusal_precision: "拒答准确率",
  refusal_f1: "拒答 F1",
  answerability_accuracy: "可回答判定",
  pass_rate: "案例通过率",
  stability: "结果稳定性",
  error_rate: "错误率",
  duplicate_rate: "重复率",
  empty_result_rate: "空结果率",
  latency_p95_ms: "P95 时延",
  quality_score: "综合质量分",
  acl_leakage_count: "越权泄漏",
};

const categoryLabels: Record<string, string> = {
  direct: "直接问答",
  lexical: "关键词",
  paraphrase: "语义改写",
  multi_hop: "多跳检索",
  cross_kb: "跨库综合",
  semantic: "语义检索",
  scenario: "场景推理",
  numeric: "数值查询",
  unanswerable: "不可回答",
  acl_denied: "权限隔离",
  adversarial: "对抗攻击",
  ambiguous: "歧义问题",
  visual: "多模态",
  production: "线上问题",
};

export default function EvaluationPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState("");
  const [runs, setRuns] = useState<RunResult[]>([]);
  const [trend, setTrend] = useState<TrendData>({ points: [], alerts: [] });
  const [feedback, setFeedback] = useState<FeedbackCandidate[]>([]);
  const [questionAssets, setQuestionAssets] = useState<QuestionAssetPage>({
    items: [], page: 1, pageSize: 100, total: 0,
  });
  const [selectedRun, setSelectedRun] = useState<RunResult | null>(null);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"overview" | "slices" | "cases" | "questions">("overview");
  const [topK, setTopK] = useState(5);
  const [concurrency, setConcurrency] = useState(4);
  const [repetitions, setRepetitions] = useState(1);
  const [caseQuery, setCaseQuery] = useState("");
  const [caseCategory, setCaseCategory] = useState("all");
  const [questionQuery, setQuestionQuery] = useState("");
  const [questionUser, setQuestionUser] = useState("all");
  const [questionLabel, setQuestionLabel] = useState("all");
  const [promotingId, setPromotingId] = useState<string | null>(null);

  const load = async (preferredKbId?: string) => {
    setLoading(true);
    try {
      const requestedKbId = preferredKbId || kbId || "";
      const overviewSuffix = requestedKbId
        ? `?kb_id=${encodeURIComponent(requestedKbId)}`
        : "";
      const [summary, candidates, kbList, questions] = await Promise.all([
        api.get(`/api/admin/evaluation/overview${overviewSuffix}`),
        api.get("/api/admin/evaluation/feedback-candidates?limit=20"),
        fetchKnowledgeBases(),
        api.get("/api/admin/evaluation/questions?page_size=100"),
      ]) as unknown as [Overview, FeedbackCandidate[], KnowledgeBase[], QuestionAssetPage];
      const nextKbId = requestedKbId && (requestedKbId === "*"
        || kbList.some((item) => item.id === requestedKbId))
        ? requestedKbId
        : kbList[0]?.id || "";
      const suffix = nextKbId ? `&kb_id=${nextKbId}` : "";
      const [history, trendData] = await Promise.all([
        api.get(`/api/admin/evaluation/runs?limit=30${suffix}`),
        api.get(`/api/admin/evaluation/trend?limit=30${suffix}`),
      ]) as unknown as [RunResult[], TrendData];
      setOverview(summary);
      setFeedback(candidates);
      setQuestionAssets(questions);
      setKbs(kbList);
      setKbId(nextKbId);
      setRuns(history);
      setTrend(trendData);
      const latest = history[0] || summary.latestRun;
      setSelectedRun((current) =>
        current && history.some((item) => item.id === current.id) ? current : latest
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "评测数据加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeKb = (value: string) => {
    setKbId(value);
    setSelectedRun(null);
    void load(value);
  };

  const runEvaluation = async () => {
    if (!kbId || running) return;
    setRunning(true);
    try {
      const queued = await api.post("/api/admin/evaluation/run", {
        kb_id: kbId,
        top_k: topK,
        concurrency,
        repetitions,
        timeout_seconds: 120,
        categories: [],
        graph_rag: false,
        label: `手动评测 · Top ${topK}`,
      }) as unknown as RunResult;
      setSelectedRun(queued);
      toast.success("评测任务已进入后台队列");
      let data = queued;
      for (let attempt = 0; attempt < 600; attempt += 1) {
        if (["completed", "failed"].includes(data.status)) break;
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        data = await api.get(
          `/api/admin/evaluation/runs/${queued.id}`,
        ) as RunResult;
        setSelectedRun(data);
      }
      if (!["completed", "failed"].includes(data.status)) {
        throw new Error("评测仍在后台运行，请稍后在历史记录中查看");
      }
      toast.success(
        data.status === "completed" && data.gateStatus === "passed"
          ? "评测完成，质量门禁通过"
          : "评测完成，发现质量门禁未通过",
      );
      await load(kbId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "评测运行失败");
    } finally {
      setRunning(false);
    }
  };

  const selectRun = async (run: RunResult) => {
    try {
      const detail = await api.get(`/api/admin/evaluation/runs/${run.id}`) as RunResult;
      setSelectedRun(detail);
      setTab("overview");
    } catch {
      setSelectedRun(run);
    }
  };

  const downloadFeedback = () => {
    const content = feedback
      .map((item) => JSON.stringify(item.suggestedCase))
      .join("\n");
    const url = URL.createObjectURL(
      new Blob([content], { type: "application/x-ndjson;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "feedback-review.jsonl";
    link.click();
    URL.revokeObjectURL(url);
  };

  const generateGolden = async (item: QuestionAsset) => {
    if (!item.answer || promotingId) return;
    setPromotingId(item.id);
    try {
      const result = await api.post(
        `/api/admin/evaluation/questions/${item.id}/golden`,
      ) as unknown as { active: boolean; reviewStatus: string };
      toast.success(
        result.active
          ? "已生成 Golden Case，并加入回归测试集"
          : "已保存为 Golden 候选；Bad Case 需补标后才会启用",
      );
      await load(kbId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Golden Case 生成失败");
    } finally {
      setPromotingId(null);
    }
  };

  const filteredCases = useMemo(
    () => (overview?.cases || []).filter((item) =>
      (caseCategory === "all" || item.category === caseCategory)
      && (
        !caseQuery
        || item.question.toLowerCase().includes(caseQuery.toLowerCase())
        || item.id.includes(caseQuery)
      )
    ),
    [overview?.cases, caseCategory, caseQuery],
  );

  const failedCases = useMemo(
    () => (selectedRun?.results || [])
      .filter((item) => !item.case_metrics.passed)
      .sort((a, b) => a.case_metrics.recall - b.case_metrics.recall),
    [selectedRun],
  );

  const questionUsers = useMemo(
    () => Array.from(
      new Map(questionAssets.items.map((item) => [item.user.id, item.user])).values(),
    ),
    [questionAssets.items],
  );

  const filteredQuestionAssets = useMemo(
    () => questionAssets.items.filter((item) => {
      const matchesQuery = !questionQuery
        || item.question.toLowerCase().includes(questionQuery.toLowerCase())
        || item.answer?.content.toLowerCase().includes(questionQuery.toLowerCase());
      const matchesUser = questionUser === "all" || item.user.id === questionUser;
      const matchesLabel = questionLabel === "all"
        || (questionLabel === "bad" && item.label === "BAD_CASE")
        || (questionLabel === "good" && item.label === "GOOD_CASE")
        || (questionLabel === "golden" && item.label === "GOLDEN")
        || (questionLabel === "unrated" && item.label === "UNRATED");
      return matchesQuery && matchesUser && matchesLabel;
    }),
    [questionAssets.items, questionLabel, questionQuery, questionUser],
  );

  const metrics = selectedRun?.metrics || {};
  const rankedK = selectedRun?.config.top_k || 5;
  const rankedMetric = (name: string) => `${name}@${rankedK}`;
  const qualityScore = metrics.quality_score || 0;
  const scoreTone = qualityScore >= 0.8
    ? "text-emerald-300"
    : qualityScore >= 0.6 ? "text-amber-300" : "text-rose-300";

  return (
    <div className="min-h-screen bg-[#f5f7f8] text-slate-900">
      <header className="border-b border-slate-200 bg-white px-5 py-5 lg:px-8">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-end justify-between gap-5">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.22em] text-teal-700">
              <span className="h-px w-7 bg-teal-600" />
              Retrieval quality control
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-slate-950">
              检索评测驾驶舱
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              离线回放、质量门禁与线上反馈构成同一条改进链路。
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="评测知识库">
              <select
                value={kbId}
                onChange={(event) => changeKb(event.target.value)}
                className="h-9 min-w-44 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium outline-none focus:border-teal-500"
              >
                <option value="*">全部知识库（跨库评测）</option>
                {kbs.map((kb) => (
                  <option key={kb.id} value={kb.id}>{kb.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Top K">
              <select
                value={topK}
                onChange={(event) => setTopK(Number(event.target.value))}
                className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none"
              >
                {[3, 5, 10, 20].map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="并发">
              <select
                value={concurrency}
                onChange={(event) => setConcurrency(Number(event.target.value))}
                className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none"
              >
                {[1, 2, 4, 8].map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="重复">
              <select
                value={repetitions}
                onChange={(event) => setRepetitions(Number(event.target.value))}
                className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none"
              >
                {[1, 2, 3, 5].map((value) => (
                  <option key={value} value={value}>{value} 次</option>
                ))}
              </select>
            </Field>
            <button
              type="button"
              onClick={runEvaluation}
              disabled={!kbId || running}
              className="inline-flex h-9 items-center gap-2 rounded-lg bg-slate-950 px-4 text-xs font-semibold text-white shadow-sm transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running
                ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                : <Play className="h-3.5 w-3.5 fill-current" />}
              {running ? "正在回放" : "运行评测"}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-5 py-6 lg:px-8">
        {loading && !overview ? <LoadingSkeleton /> : (
          <>
            <section className="grid overflow-hidden rounded-2xl bg-slate-950 shadow-[0_22px_60px_-34px_rgba(15,23,42,0.65)] lg:grid-cols-[280px_1fr_330px]">
              <div className="border-b border-white/10 p-6 lg:border-b-0 lg:border-r">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500">
                  Latest quality score
                </p>
                <div className="mt-5 flex items-end gap-2">
                  <span className={`font-mono text-6xl font-semibold leading-none tracking-[-0.08em] ${scoreTone}`}>
                    {selectedRun ? Math.round(qualityScore * 100) : "—"}
                  </span>
                  <span className="mb-1 text-sm text-slate-500">/ 100</span>
                </div>
                <div className="mt-5 flex items-center gap-2">
                  <StatusBadge status={selectedRun?.gateStatus} />
                  <span className="text-xs text-slate-400">
                    {selectedRun ? formatTime(selectedRun.createdAt) : "尚未运行评测"}
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-px bg-white/10 sm:grid-cols-4">
                <DarkMetric label={`Recall@${rankedK}`} value={metrics[rankedMetric("recall")]} delta={selectedRun?.deltas[rankedMetric("recall")]} icon={<Target />} />
                <DarkMetric label={`NDCG@${rankedK}`} value={metrics[rankedMetric("ndcg")]} delta={selectedRun?.deltas[rankedMetric("ndcg")]} icon={<BarChart3 />} />
                <DarkMetric label="拒答 F1" value={metrics.refusal_f1} delta={selectedRun?.deltas.refusal_f1} icon={<ShieldCheck />} />
                <DarkMetric label="P95 时延" value={metrics.latency_p95_ms} delta={selectedRun?.deltas.latency_p95_ms} icon={<Clock3 />} latency />
              </div>

              <div className="border-t border-white/10 p-6 lg:border-l lg:border-t-0">
                <div className="flex items-center justify-between">
                  <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500">
                    Dataset coverage
                  </p>
                  <span className="font-mono text-xs text-slate-400">
                    {overview?.datasetVersion}
                  </span>
                </div>
                <div className="mt-5 grid grid-cols-3 gap-3">
                  <MiniValue value={overview?.activeCaseCount || 0} label="启用案例" />
                  <MiniValue value={Object.keys(overview?.categories || {}).length} label="场景切片" />
                  <MiniValue value={overview?.negativeCaseCount || 0} label="负样本" />
                </div>
                <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full bg-teal-400"
                    style={{
                      width: `${overview ? overview.activeCaseCount / overview.caseCount * 100 : 0}%`,
                    }}
                  />
                </div>
                <p className="mt-2 text-[10px] text-slate-500">
                  {overview?.activeCaseCount || 0} / {overview?.caseCount || 0} 案例已进入回归集
                </p>
              </div>
            </section>

            {trend.alerts.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {trend.alerts.map((alert) => (
                  <div
                    key={`${alert.metric}-${alert.message}`}
                    className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"
                  >
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {alert.message}
                  </div>
                ))}
              </div>
            )}

            <nav className="mt-6 flex items-center gap-1 border-b border-slate-200">
              {([
                ["overview", "质量总览", Gauge],
                ["slices", "切片诊断", Layers3],
                ["cases", "案例资产", FlaskConical],
                ["questions", "用户问题", UserRound],
              ] as const).map(([value, label, Icon]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setTab(value)}
                  className={`relative flex items-center gap-2 px-4 py-3 text-xs font-semibold transition ${
                    tab === value ? "text-teal-800" : "text-slate-500 hover:text-slate-900"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                  {tab === value && (
                    <span className="absolute inset-x-2 bottom-[-1px] h-0.5 bg-teal-600" />
                  )}
                </button>
              ))}
            </nav>

            {tab === "overview" && (
              <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_360px]">
                <div className="space-y-5">
                  <Panel
                    title="质量趋势"
                    subtitle="综合质量、Recall 与 NDCG 的历史变化"
                    action={<span className="text-[10px] text-slate-400">最近 {trend.points.length} 次运行</span>}
                  >
                    <TrendChart points={trend.points} />
                  </Panel>
                  <Panel
                    title="指标矩阵"
                    subtitle="一次运行同时观察相关性、覆盖、安全、稳定性与成本"
                  >
                    <div className="grid gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200 sm:grid-cols-2 lg:grid-cols-4">
                      {[
                        rankedMetric("precision"), rankedMetric("hit_rate"),
                        rankedMetric("mrr"), rankedMetric("map"),
                        rankedMetric("doc_recall"), "retrieval_coverage", "answerability_accuracy",
                        "stability", "pass_rate", "refusal_recall", "duplicate_rate",
                        "error_rate",
                      ].map((name) => (
                        <MetricCell
                          key={name}
                          name={name}
                          value={metrics[name]}
                          delta={selectedRun?.deltas[name]}
                        />
                      ))}
                    </div>
                  </Panel>
                  {failedCases.length > 0 && (
                    <Panel
                      title="失败案例下钻"
                      subtitle="按召回率排序，优先处理完全未命中的案例"
                      action={<span className="rounded-full bg-rose-50 px-2 py-1 text-[10px] font-semibold text-rose-700">{failedCases.length} 个待处理</span>}
                    >
                      <div className="divide-y divide-slate-100">
                        {failedCases.slice(0, 8).map((item) => (
                          <div
                            key={item.case_id}
                            className="grid gap-3 py-3 sm:grid-cols-[1fr_90px_90px] sm:items-center"
                          >
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium text-slate-800">
                                {item.question}
                              </p>
                              <p className="mt-1 text-[10px] text-slate-400">
                                {categoryLabels[item.category] || item.category} · {item.case_id}
                              </p>
                            </div>
                            <span className="text-xs text-slate-500">
                              Recall <b className="font-mono text-slate-800">{formatPercent(item.case_metrics.recall)}</b>
                            </span>
                            <span className="text-xs text-slate-500">{item.latency_ms} ms</span>
                          </div>
                        ))}
                      </div>
                    </Panel>
                  )}
                </div>

                <aside className="space-y-5">
                  <Panel
                    title="质量门禁"
                    subtitle="发布前必须满足的硬性阈值"
                    action={<span className="font-mono text-[10px] text-slate-400">{selectedRun?.gates.passed || 0}/{selectedRun?.gates.total || 8}</span>}
                  >
                    <div className="space-y-2">
                      {(selectedRun?.gates.checks || []).map((gate) => (
                        <div
                          key={gate.metric}
                          className="flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-2.5"
                        >
                          <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full ${
                            gate.passed
                              ? "bg-emerald-100 text-emerald-700"
                              : "bg-rose-100 text-rose-700"
                          }`}>
                            {gate.passed
                              ? <Check className="h-3 w-3" />
                              : <X className="h-3 w-3" />}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-xs font-medium text-slate-700">
                              {metricLabels[gate.metric] || gate.metric}
                            </p>
                            <p className="font-mono text-[10px] text-slate-400">
                              {formatGateValue(gate.metric, gate.value)} {gate.operator}{" "}
                              {formatGateValue(gate.metric, gate.threshold)}
                            </p>
                          </div>
                        </div>
                      ))}
                      {!selectedRun?.gates.checks?.length && (
                        <Empty text="运行一次评测后生成质量门禁" />
                      )}
                    </div>
                  </Panel>
                  <Panel title="运行历史" subtitle="点击切换基线与结果">
                    <div className="space-y-1">
                      {runs.slice(0, 10).map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => selectRun(item)}
                          className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition ${
                            selectedRun?.id === item.id
                              ? "bg-teal-50 ring-1 ring-teal-200"
                              : "hover:bg-slate-50"
                          }`}
                        >
                          <span className={`h-2 w-2 rounded-full ${
                            item.gateStatus === "passed"
                              ? "bg-emerald-500"
                              : item.gateStatus === "failed"
                                ? "bg-rose-500"
                                : "bg-slate-300"
                          }`} />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-xs font-medium text-slate-700">
                              {item.config.label || `Top ${item.config.top_k || 5} 回放`}
                            </p>
                            <p className="mt-0.5 text-[10px] text-slate-400">
                              {formatTime(item.createdAt)} · {formatDuration(item.durationMs)}
                            </p>
                          </div>
                          <span className="font-mono text-sm font-semibold text-slate-700">
                            {Math.round((item.metrics.quality_score || 0) * 100)}
                          </span>
                        </button>
                      ))}
                      {!runs.length && <Empty text="暂无历史运行" />}
                    </div>
                  </Panel>
                  <button
                    type="button"
                    onClick={downloadFeedback}
                    disabled={!feedback.length}
                    className="flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-teal-300 disabled:opacity-50"
                  >
                    <span className="flex items-center gap-3">
                      <span className="grid h-9 w-9 place-items-center rounded-lg bg-rose-50 text-rose-600">
                        <ThumbsDown className="h-4 w-4" />
                      </span>
                      <span>
                        <b className="block text-xs text-slate-800">线上差评待标注</b>
                        <small className="mt-1 block text-[10px] text-slate-400">
                          {overview?.negativeFeedbackCandidates || 0} 条候选样本
                        </small>
                      </span>
                    </span>
                    <Download className="h-4 w-4 text-slate-400" />
                  </button>
                </aside>
              </div>
            )}

            {tab === "slices" && (
              <div className="mt-5 space-y-5">
                <Panel
                  title="场景切片热力图"
                  subtitle="横向比较不同问题类型，颜色越深表示表现越好"
                >
                  <SliceMatrix slices={selectedRun?.bySlice.category || {}} topK={rankedK} />
                </Panel>
                <Panel title="难度分层" subtitle="防止总体均值掩盖困难案例退化">
                  <SliceMatrix slices={selectedRun?.bySlice.difficulty || {}} topK={rankedK} />
                </Panel>
              </div>
            )}

            {tab === "cases" && (
              <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_300px]">
                <Panel
                  title="案例资产库"
                  subtitle={`${filteredCases.length} 条案例 · 版本 ${overview?.datasetVersion || "—"}`}
                >
                  <div className="mb-4 flex flex-wrap gap-2">
                    <label className="relative min-w-56 flex-1">
                      <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
                      <input
                        value={caseQuery}
                        onChange={(event) => setCaseQuery(event.target.value)}
                        placeholder="搜索问题或 Case ID"
                        className="h-9 w-full rounded-lg border border-slate-200 pl-9 pr-3 text-xs outline-none focus:border-teal-500"
                      />
                    </label>
                    <label className="relative">
                      <select
                        value={caseCategory}
                        onChange={(event) => setCaseCategory(event.target.value)}
                        className="h-9 appearance-none rounded-lg border border-slate-200 bg-white pl-3 pr-8 text-xs outline-none"
                      >
                        <option value="all">全部场景</option>
                        {Object.keys(overview?.categories || {}).map((category) => (
                          <option key={category} value={category}>
                            {categoryLabels[category] || category}
                          </option>
                        ))}
                      </select>
                      <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
                    </label>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[720px] text-left">
                      <thead>
                        <tr className="border-b border-slate-200 text-[10px] uppercase tracking-[0.12em] text-slate-400">
                          <th className="pb-2 font-semibold">问题</th>
                          <th className="pb-2 font-semibold">场景</th>
                          <th className="pb-2 font-semibold">难度</th>
                          <th className="pb-2 font-semibold">标签</th>
                          <th className="pb-2 text-right font-semibold">状态</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {filteredCases.map((item) => (
                          <tr key={item.id}>
                            <td className="max-w-xl py-3 pr-5">
                              <p className="truncate text-xs font-medium text-slate-700">
                                {item.question}
                              </p>
                              <p className="mt-1 font-mono text-[9px] text-slate-400">
                                {item.id}
                              </p>
                            </td>
                            <td className="py-3 pr-4 text-xs text-slate-500">
                              {categoryLabels[item.category] || item.category}
                            </td>
                            <td className="py-3 pr-4">
                              <Difficulty value={item.difficulty} />
                            </td>
                            <td className="py-3 pr-4">
                              <div className="flex max-w-52 flex-wrap gap-1">
                                {item.tags.slice(0, 2).map((tag) => (
                                  <span key={tag} className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-500">
                                    {tag}
                                  </span>
                                ))}
                              </div>
                            </td>
                            <td className="py-3 text-right">
                              <span className={`rounded-full px-2 py-1 text-[9px] font-semibold ${
                                item.active
                                  ? item.answerable
                                    ? "bg-teal-50 text-teal-700"
                                    : "bg-amber-50 text-amber-700"
                                  : "bg-slate-100 text-slate-400"
                              }`}>
                                {item.active ? item.answerable ? "应回答" : "应拒答" : "未启用"}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Panel>
                <aside className="space-y-5">
                  <Panel title="资产健康度" subtitle="案例结构与负样本占比">
                    <div className="space-y-4">
                      <Progress
                        label="已启用"
                        value={(overview?.activeCaseCount || 0) / Math.max(overview?.caseCount || 1, 1)}
                      />
                      <Progress
                        label="困难案例"
                        value={(overview?.difficulties.hard || 0) / Math.max(overview?.activeCaseCount || 1, 1)}
                      />
                      <Progress
                        label="负样本"
                        value={(overview?.negativeCaseCount || 0) / Math.max(overview?.activeCaseCount || 1, 1)}
                      />
                    </div>
                  </Panel>
                  <Panel title="场景分布" subtitle="回归集覆盖面">
                    <div className="space-y-2">
                      {Object.entries(overview?.categories || {})
                        .sort((a, b) => b[1] - a[1])
                        .map(([name, count]) => (
                          <div key={name} className="flex items-center justify-between text-xs">
                            <span className="text-slate-500">
                              {categoryLabels[name] || name}
                            </span>
                            <span className="font-mono font-semibold text-slate-700">
                              {count}
                            </span>
                          </div>
                        ))}
                    </div>
                  </Panel>
                </aside>
              </div>
            )}

            {tab === "questions" && (
              <div className="mt-5">
                <Panel
                  title="用户问题资产"
                  subtitle={`共 ${questionAssets.total} 条 · 回答完成后自动生成 Base Case`}
                  action={(
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-50 px-2.5 py-1 text-[10px] font-semibold text-teal-700">
                      <Sparkles className="h-3 w-3" />
                      Golden 闭环
                    </span>
                  )}
                >
                  <div className="mb-4 flex flex-wrap gap-2">
                    <label className="relative min-w-64 flex-1">
                      <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
                      <input
                        value={questionQuery}
                        onChange={(event) => setQuestionQuery(event.target.value)}
                        placeholder="搜索问题或回答"
                        className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-xs outline-none transition focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
                      />
                    </label>
                    <label className="relative">
                      <select
                        value={questionUser}
                        onChange={(event) => setQuestionUser(event.target.value)}
                        className="h-9 min-w-36 appearance-none rounded-lg border border-slate-200 bg-white pl-3 pr-8 text-xs outline-none focus:border-teal-500"
                      >
                        <option value="all">全部用户</option>
                        {questionUsers.map((owner) => (
                          <option key={owner.id} value={owner.id}>{owner.username}</option>
                        ))}
                      </select>
                      <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
                    </label>
                    <label className="relative">
                      <select
                        value={questionLabel}
                        onChange={(event) => setQuestionLabel(event.target.value)}
                        className="h-9 min-w-36 appearance-none rounded-lg border border-slate-200 bg-white pl-3 pr-8 text-xs outline-none focus:border-teal-500"
                      >
                        <option value="all">全部标签</option>
                        <option value="bad">Bad Case</option>
                        <option value="good">正向反馈</option>
                        <option value="golden">Golden</option>
                        <option value="unrated">未评价</option>
                      </select>
                      <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
                    </label>
                  </div>

                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[1080px] text-left">
                      <thead>
                        <tr className="border-b border-slate-200 text-[10px] uppercase tracking-[0.12em] text-slate-400">
                          <th className="pb-2 font-semibold">用户 / 时间</th>
                          <th className="pb-2 font-semibold">问题与回答</th>
                          <th className="pb-2 font-semibold">反馈标签</th>
                          <th className="pb-2 font-semibold">质量分</th>
                          <th className="pb-2 font-semibold">测试资产</th>
                          <th className="pb-2 text-right font-semibold">操作</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {filteredQuestionAssets.map((item) => {
                          const isBad = item.label === "BAD_CASE";
                          const isGolden = item.dataset?.caseType === "golden";
                          return (
                            <tr key={item.id} className={isBad ? "bg-rose-50/45" : "hover:bg-slate-50/60"}>
                              <td className="w-36 py-3 pr-4 align-top">
                                <div className="flex items-center gap-2">
                                  <span className="grid h-6 w-6 place-items-center rounded-full bg-slate-100 text-[10px] font-bold text-slate-600">
                                    {item.user.username.slice(0, 1).toUpperCase()}
                                  </span>
                                  <span className="text-xs font-semibold text-slate-700">
                                    {item.user.username}
                                  </span>
                                </div>
                                <p className="mt-2 font-mono text-[9px] text-slate-400">
                                  {formatTime(item.createdAt)}
                                </p>
                              </td>
                              <td className="max-w-xl py-3 pr-6 align-top">
                                <p className="text-xs font-semibold leading-5 text-slate-800">
                                  {item.question}
                                </p>
                                <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-400">
                                  {item.answer?.content || "回答尚未完成"}
                                </p>
                                {item.answer && (
                                  <span className="mt-1.5 inline-block text-[9px] text-slate-400">
                                    {item.answer.sourceCount} 条召回证据
                                  </span>
                                )}
                              </td>
                              <td className="w-32 py-3 pr-5 align-top">
                                <CaseLabel value={item.label} />
                                {(item.feedback?.reason || item.feedback?.comment) && (
                                  <p
                                    className="mt-2 max-w-32 truncate text-[9px] text-slate-400"
                                    title={item.feedback.comment || item.feedback.reason}
                                  >
                                    {item.feedback.comment || item.feedback.reason}
                                  </p>
                                )}
                              </td>
                              <td className="w-32 py-3 pr-5 align-top">
                                <QualityScore value={item.qualityScore} bad={isBad} />
                              </td>
                              <td className="w-36 py-3 pr-5 align-top">
                                <p className="text-[10px] font-semibold text-slate-600">
                                  {isGolden ? "Golden Case" : item.dataset ? "Base Case" : "等待回答"}
                                </p>
                                <p className={`mt-1 text-[9px] ${
                                  item.dataset?.reviewStatus === "needs_review"
                                    ? "font-semibold text-rose-600"
                                    : item.dataset?.active
                                      ? "text-teal-600"
                                      : "text-slate-400"
                                }`}>
                                  {item.dataset?.reviewStatus === "needs_review"
                                    ? "待补标 · 未启用"
                                    : item.dataset?.active
                                      ? "已进入回归集"
                                      : item.dataset
                                        ? "自动生成 · 待提升"
                                        : "尚未生成"}
                                </p>
                              </td>
                              <td className="w-36 py-3 text-right align-top">
                                <button
                                  type="button"
                                  onClick={() => generateGolden(item)}
                                  disabled={!item.answer || promotingId === item.id || isGolden}
                                  className={`inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-[10px] font-semibold transition focus:outline-none focus:ring-2 focus:ring-offset-1 disabled:cursor-not-allowed ${
                                    isBad && !isGolden
                                      ? "border border-rose-200 bg-white text-rose-700 hover:bg-rose-50 focus:ring-rose-200"
                                      : "bg-slate-900 text-white hover:bg-teal-800 focus:ring-teal-300 disabled:bg-slate-100 disabled:text-slate-400"
                                  }`}
                                >
                                  {promotingId === item.id
                                    ? <RefreshCw className="h-3 w-3 animate-spin" />
                                    : <Sparkles className="h-3 w-3" />}
                                  {isGolden ? "已生成" : isBad ? "保存待复核" : "生成 Golden"}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {!filteredQuestionAssets.length && (
                    <Empty text="暂无符合筛选条件的用户问题" />
                  )}
                </Panel>
              </div>
            )}
          </>
        )}
      </main>
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
    <section className="rounded-xl border border-slate-200 bg-white shadow-[0_10px_30px_-26px_rgba(15,23,42,0.45)]">
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label>
      <span className="mb-1.5 block text-[9px] font-bold uppercase tracking-[0.12em] text-slate-400">
        {label}
      </span>
      {children}
    </label>
  );
}

function StatusBadge({ status }: { status?: string }) {
  if (!status) {
    return (
      <span className="rounded-full bg-white/10 px-2 py-1 text-[10px] font-semibold text-slate-400">
        无基线
      </span>
    );
  }
  const passed = status === "passed";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-bold ${
      passed
        ? "bg-emerald-400/15 text-emerald-300"
        : "bg-rose-400/15 text-rose-300"
    }`}>
      {passed ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
      {passed ? "门禁通过" : "门禁失败"}
    </span>
  );
}

function DarkMetric({
  label, value, delta, icon, latency = false,
}: {
  label: string;
  value?: number;
  delta?: number;
  icon: React.ReactNode;
  latency?: boolean;
}) {
  const goodDelta = latency ? (delta || 0) <= 0 : (delta || 0) >= 0;
  return (
    <div className="bg-slate-950 p-5">
      <div className="flex items-center justify-between text-slate-500">
        <span className="[&>svg]:h-4 [&>svg]:w-4">{icon}</span>
        {delta !== undefined && (
          <span className={`flex items-center text-[10px] ${
            goodDelta ? "text-emerald-400" : "text-rose-400"
          }`}>
            {delta >= 0
              ? <ArrowUpRight className="h-3 w-3" />
              : <ArrowDownRight className="h-3 w-3" />}
            {latency ? `${Math.abs(delta).toFixed(0)}ms` : formatPercent(Math.abs(delta))}
          </span>
        )}
      </div>
      <p className="mt-5 font-mono text-2xl font-semibold text-white">
        {value === undefined ? "—" : latency ? `${Math.round(value)}ms` : formatPercent(value)}
      </p>
      <p className="mt-1 text-[10px] text-slate-500">{label}</p>
    </div>
  );
}

function MiniValue({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <p className="font-mono text-lg font-semibold text-white">{value}</p>
      <p className="mt-0.5 text-[9px] text-slate-500">{label}</p>
    </div>
  );
}

function MetricCell({
  name, value, delta,
}: {
  name: string;
  value?: number;
  delta?: number;
}) {
  const inverse = ["error_rate", "duplicate_rate", "empty_result_rate"].includes(name);
  const good = inverse ? (delta || 0) <= 0 : (delta || 0) >= 0;
  return (
    <div className="bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[10px] text-slate-500">{metricLabels[name] || name}</p>
        {delta !== undefined && (
          <span className={`font-mono text-[9px] ${
            good ? "text-emerald-600" : "text-rose-600"
          }`}>
            {delta > 0 ? "+" : ""}{formatPercent(delta)}
          </span>
        )}
      </div>
      <p className="mt-3 font-mono text-xl font-semibold text-slate-900">
        {value === undefined ? "—" : formatPercent(value)}
      </p>
    </div>
  );
}

function TrendChart({ points }: { points: TrendData["points"] }) {
  if (!points.length) {
    return <Empty text="完成两次以上评测后可观察质量趋势" />;
  }
  const width = 820;
  const height = 220;
  const padX = 36;
  const padY = 20;
  const x = (index: number) => points.length === 1
    ? width / 2
    : padX + index * (width - padX * 2) / (points.length - 1);
  const y = (value: number) =>
    padY + (1 - Math.max(0, Math.min(1, value))) * (height - padY * 2);
  const series = [
    { key: "quality_score", color: "#0f766e", label: "综合质量" },
    { key: "recall@", color: "#6366f1", label: "Recall@K" },
    { key: "ndcg@", color: "#f59e0b", label: "NDCG@K" },
  ];
  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-56 w-full"
        role="img"
        aria-label="评测质量趋势图"
      >
        {[0, .25, .5, .75, 1].map((tick) => (
          <g key={tick}>
            <line
              x1={padX}
              x2={width - padX}
              y1={y(tick)}
              y2={y(tick)}
              stroke="#e2e8f0"
              strokeWidth="1"
              strokeDasharray="3 5"
            />
            <text x="2" y={y(tick) + 3} fontSize="9" fill="#94a3b8">
              {Math.round(tick * 100)}
            </text>
          </g>
        ))}
        {series.map((item) => {
          const coordinates = points
            .map((point, index) => `${x(index)},${y(trendMetric(point.metrics, item.key))}`)
            .join(" ");
          return (
            <g key={item.key}>
              <polyline
                fill="none"
                stroke={item.color}
                strokeWidth="2.5"
                strokeLinejoin="round"
                strokeLinecap="round"
                points={coordinates}
              />
              {points.map((point, index) => (
                <circle
                  key={point.id}
                  cx={x(index)}
                  cy={y(trendMetric(point.metrics, item.key))}
                  r="3"
                  fill="white"
                  stroke={item.color}
                  strokeWidth="2"
                />
              ))}
            </g>
          );
        })}
      </svg>
      <div className="flex flex-wrap justify-center gap-5">
        {series.map((item) => (
          <span key={item.key} className="flex items-center gap-2 text-[10px] text-slate-500">
            <i className="h-0.5 w-5" style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function SliceMatrix({
  slices, topK,
}: {
  slices: Record<string, MetricMap>;
  topK: number;
}) {
  const entries = Object.entries(slices);
  const columns = [
    `recall@${topK}`, `ndcg@${topK}`, `mrr@${topK}`,
    `hit_rate@${topK}`, "pass_rate", "latency_p95_ms",
  ];
  if (!entries.length) {
    return <Empty text="选择包含切片结果的运行后查看诊断" />;
  }
  const difficultyLabels: Record<string, string> = {
    easy: "简单",
    medium: "中等",
    hard: "困难",
  };
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.1em] text-slate-400">
            <th className="pb-3 font-semibold">切片</th>
            {columns.map((name) => (
              <th key={name} className="pb-3 text-center font-semibold">
                {metricLabels[name] || name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {entries.map(([name, metrics]) => (
            <tr key={name}>
              <td className="py-3 text-xs font-semibold text-slate-700">
                {categoryLabels[name] || difficultyLabels[name] || name}
                <span className="ml-2 font-mono text-[9px] text-slate-400">
                  {metrics.evaluated_cases || 0} cases
                </span>
              </td>
              {columns.map((metric) => (
                <td key={metric} className="px-1 py-2 text-center">
                  <HeatValue
                    value={metrics[metric]}
                    latency={metric.includes("latency")}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeatValue({
  value = 0, latency = false,
}: {
  value?: number;
  latency?: boolean;
}) {
  const score = latency ? Math.max(0, 1 - value / 5000) : value;
  const tone = score >= .8
    ? "bg-teal-100 text-teal-900"
    : score >= .6
      ? "bg-amber-100 text-amber-900"
      : "bg-rose-100 text-rose-900";
  return (
    <span className={`inline-block min-w-16 rounded-md px-2 py-1.5 font-mono text-[10px] font-semibold ${tone}`}>
      {latency ? `${Math.round(value)}ms` : formatPercent(value)}
    </span>
  );
}

function CaseLabel({ value }: { value: QuestionAsset["label"] }) {
  const styles: Record<QuestionAsset["label"], [string, string]> = {
    BAD_CASE: ["BAD CASE", "border-rose-200 bg-rose-100 text-rose-800"],
    GOOD_CASE: ["正向反馈", "border-emerald-200 bg-emerald-50 text-emerald-700"],
    GOLDEN: ["GOLDEN", "border-amber-200 bg-amber-50 text-amber-700"],
    UNRATED: ["未评价", "border-slate-200 bg-slate-50 text-slate-500"],
  };
  const [label, tone] = styles[value];
  return (
    <span className={`inline-flex rounded-md border px-2 py-1 text-[9px] font-extrabold tracking-[0.08em] ${tone}`}>
      {label}
    </span>
  );
}

function QualityScore({ value, bad }: { value: number; bad: boolean }) {
  const tone = bad || value < 40
    ? "bg-rose-500 text-rose-700"
    : value >= 85
      ? "bg-emerald-500 text-emerald-700"
      : "bg-amber-500 text-amber-700";
  return (
    <div className="w-24" title="基于反馈、回答完整度和召回证据的自动分流分">
      <div className="flex items-baseline justify-between">
        <b className={`font-mono text-sm ${tone.split(" ")[1]}`}>{value}</b>
        <span className="text-[9px] text-slate-400">/100</span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${tone.split(" ")[0]}`}
          style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
        />
      </div>
    </div>
  );
}

function Difficulty({ value }: { value: string }) {
  const map: Record<string, [string, string]> = {
    easy: ["简单", "text-emerald-600"],
    medium: ["中等", "text-amber-600"],
    hard: ["困难", "text-rose-600"],
  };
  const [label, color] = map[value] || [value, "text-slate-500"];
  return <span className={`text-[10px] font-semibold ${color}`}>{label}</span>;
}

function Progress({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="flex justify-between text-[10px]">
        <span className="text-slate-500">{label}</span>
        <span className="font-mono font-semibold text-slate-700">
          {formatPercent(value)}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-teal-600"
          style={{ width: `${Math.min(100, value * 100)}%` }}
        />
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="grid min-h-32 place-items-center text-center">
      <div>
        <Activity className="mx-auto h-5 w-5 text-slate-300" />
        <p className="mt-2 text-xs text-slate-400">{text}</p>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="animate-pulse space-y-5">
      <div className="h-48 rounded-2xl bg-slate-200" />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="h-72 rounded-xl bg-slate-200" />
        <div className="h-72 rounded-xl bg-slate-200" />
      </div>
    </div>
  );
}

function formatPercent(value?: number) {
  if (value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(Math.abs(value) < .1 ? 1 : 0)}%`;
}

function trendMetric(metrics: MetricMap, key: string) {
  if (!key.endsWith("@")) return metrics[key] || 0;
  const matched = Object.keys(metrics).find((name) => name.startsWith(key));
  return matched ? metrics[matched] : 0;
}

function formatGateValue(metric: string, value: number) {
  if (metric.includes("latency")) return `${value.toFixed(0)}ms`;
  if (metric.includes("count")) return value.toFixed(0);
  return formatPercent(value);
}

function formatTime(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(value = 0) {
  return value >= 60_000
    ? `${(value / 60_000).toFixed(1)} 分钟`
    : `${(value / 1000).toFixed(1)} 秒`;
}
