import { useEffect, useState } from "react";
import { Activity, Download, FlaskConical, Play, RefreshCw, ThumbsDown } from "lucide-react";
import { api } from "@/services/api";
import { fetchKnowledgeBases } from "@/services/knowledgeService";
import type { KnowledgeBase } from "@/types";

interface Overview {
  caseCount: number;
  activeCaseCount: number;
  categories: Record<string, number>;
  negativeFeedbackCandidates: number;
  cases: Array<{
    id: string;
    question: string;
    category: string;
    answerable: boolean;
    active: boolean;
  }>;
}

interface RunResult {
  knowledgeBase: { id: string; name: string };
  metrics: Record<string, number>;
  byCategory: Record<string, Record<string, number>>;
}

interface FeedbackCandidate {
  id: string;
  question: string;
  reason?: string;
  comment?: string;
  suggestedCase: Record<string, unknown>;
}

export default function EvaluationPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState("");
  const [result, setResult] = useState<RunResult | null>(null);
  const [feedback, setFeedback] = useState<FeedbackCandidate[]>([]);
  const [running, setRunning] = useState(false);

  const load = () =>
    Promise.all([
      api.get("/api/admin/evaluation/overview"),
      api.get("/api/admin/evaluation/feedback-candidates?limit=20"),
      fetchKnowledgeBases(),
    ]).then(([data, candidates, kbList]: any[]) => {
      setOverview(data);
      setFeedback(candidates);
      setKbs(kbList);
      setKbId((current) => current || kbList[0]?.id || "");
    });

  useEffect(() => {
    load();
  }, []);

  const run = async () => {
    if (!kbId || running) return;
    setRunning(true);
    try {
      const data = await api.post("/api/admin/evaluation/run", {
        kb_id: kbId,
        top_k: 5,
        categories: [],
      });
      setResult(data as unknown as RunResult);
    } finally {
      setRunning(false);
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

  return (
    <div className="p-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-700">
            Retrieval lab
          </p>
          <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">检索评测</h2>
          <p className="mt-1 text-sm text-slate-500">
            用固定问题集回放真实检索，并把点踩回答转成待标注样本。
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-slate-200 bg-white p-2 text-slate-500 hover:text-slate-900"
          title="刷新"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <Stat icon={<FlaskConical />} label="启用样本" value={overview?.activeCaseCount || 0} />
        <Stat icon={<Activity />} label="覆盖场景" value={Object.keys(overview?.categories || {}).length} />
        <Stat icon={<ThumbsDown />} label="待复盘点踩" value={overview?.negativeFeedbackCandidates || 0} accent />
      </div>

      <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-64 flex-1">
            <span className="text-xs font-medium text-slate-600">评测知识库</span>
            <select
              value={kbId}
              onChange={(event) => setKbId(event.target.value)}
              className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-cyan-500"
            >
              {kbs.map((kb) => (
                <option key={kb.id} value={kb.id}>{kb.name}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={run}
            disabled={!kbId || running}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-950 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            <Play className="h-4 w-4" />
            {running ? "正在回放…" : "运行评测"}
          </button>
        </div>

        {result && (
          <div className="mt-5">
            <p className="text-xs text-slate-500">最近结果 · {result.knowledgeBase.name}</p>
            <div className="mt-3 grid gap-2 sm:grid-cols-4">
              {Object.entries(result.metrics).map(([name, value]) => (
                <div key={name} className="rounded-xl bg-slate-50 p-3">
                  <p className="truncate text-[10px] uppercase tracking-[0.1em] text-slate-400">{name}</p>
                  <p className="mt-1 font-mono text-lg font-semibold text-slate-900">
                    {typeof value === "number" && value <= 1 ? value.toFixed(3) : value}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">负反馈样本候选</h3>
            <p className="mt-0.5 text-xs text-slate-500">审核后可并入离线评测集，形成数据闭环。</p>
          </div>
          <button
            type="button"
            onClick={downloadFeedback}
            disabled={!feedback.length}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 disabled:opacity-40"
          >
            <Download className="h-3.5 w-3.5" />
            导出 JSONL
          </button>
        </div>
        <div className="divide-y divide-slate-100">
          {feedback.map((item) => (
            <div key={item.id} className="grid gap-2 px-5 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
              <p className="text-sm text-slate-700">{item.question}</p>
              <div className="flex flex-wrap gap-1.5 text-[10px]">
                {item.reason && (
                  <span className="rounded-full bg-rose-50 px-2 py-0.5 text-rose-700">{item.reason}</span>
                )}
                {item.comment && (
                  <span className="max-w-56 truncate rounded-full bg-slate-100 px-2 py-0.5 text-slate-500">
                    {item.comment}
                  </span>
                )}
              </div>
            </div>
          ))}
          {!feedback.length && (
            <p className="px-5 py-8 text-center text-sm text-slate-400">暂无待复盘样本</p>
          )}
        </div>
      </section>

      <section className="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-5 py-4">
          <h3 className="text-sm font-semibold text-slate-900">问题集</h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {Object.entries(overview?.categories || {}).map(([name, count]) => (
              <span key={name} className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] text-slate-600">
                {name} · {count}
              </span>
            ))}
          </div>
        </div>
        <div className="divide-y divide-slate-100">
          {overview?.cases.map((item) => (
            <div key={item.id} className="grid gap-2 px-5 py-3 sm:grid-cols-[1fr_auto_auto] sm:items-center">
              <p className="text-sm text-slate-700">{item.question}</p>
              <span className="text-[10px] font-medium uppercase tracking-[0.1em] text-slate-400">
                {item.category}
              </span>
              <span className={`w-fit rounded-full px-2 py-0.5 text-[10px] ${
                item.active ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-400"
              }`}>
                {item.active ? (item.answerable ? "应回答" : "应拒答") : "未启用"}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  accent = false,
}: {
  icon: React.ReactElement;
  label: string;
  value: number;
  accent?: boolean;
}) {
  return (
    <div className={`rounded-2xl border p-4 ${accent ? "border-rose-200 bg-rose-50" : "border-slate-200 bg-white"}`}>
      <div className={`[&>svg]:h-4 [&>svg]:w-4 ${accent ? "text-rose-500" : "text-cyan-700"}`}>{icon}</div>
      <p className="mt-4 font-mono text-2xl font-semibold text-slate-950">{value}</p>
      <p className="text-xs text-slate-500">{label}</p>
    </div>
  );
}
