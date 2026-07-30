import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  History,
  LoaderCircle,
  Quote,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import type { SourceRef } from "@/types";
import InterviewRadar from "./InterviewRadar";
import { lowestRatedQuestions } from "./interviewUtils";
import type {
  InterviewHistoryData,
  InterviewHistoryItem,
  InterviewSession,
} from "./types";

interface Props {
  history: InterviewHistoryData | null;
  selected: InterviewSession | null;
  selectedId?: string;
  loading: boolean;
  error: string;
  onSelect: (id: string) => void;
  onOpenFullReview: () => void;
  onClear: () => Promise<void>;
  onBack: () => void;
  onClose: () => void;
  onViewSources: (sources: SourceRef[]) => void;
}

const difficultyLabels = {
  mid: "中级",
  senior: "高级",
  expert: "专家",
} as const;

function formatCompletedAt(value: string): { date: string; time: string } {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { date: value, time: "" };
  return {
    date: new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(date),
    time: new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date),
  };
}

function scoreTone(score: number): string {
  if (score >= 8) return "border-emerald-400 bg-emerald-500 text-white";
  if (score >= 6) return "border-cyan-400 bg-cyan-500 text-white";
  return "border-amber-400 bg-amber-400 text-slate-950";
}

export default function InterviewHistoryTimeline({
  history,
  selected,
  selectedId,
  loading,
  error,
  onSelect,
  onOpenFullReview,
  onClear,
  onBack,
  onClose,
  onViewSources,
}: Props) {
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const selectedSummary = history?.items.find((item) => item.id === selected?.id);
  const lowRated = selected ? lowestRatedQuestions(selected.questions, 3) : [];

  return (
    <div className="relative z-10 flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="border-b border-slate-200 bg-white/95 px-5 py-4 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-7xl items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-950 text-cyan-300">
            <History className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-700">
              Interview archive
            </p>
            <h2 className="truncate text-lg font-semibold text-slate-950">面试历史回溯</h2>
          </div>
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50"
          >
            <ArrowLeft className="h-4 w-4" />
            返回
          </button>
          {!!history?.items.length && (
            <button
              type="button"
              disabled={loading}
              onClick={() => setShowClearConfirm(true)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              清空历史
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="hidden rounded-xl px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-100 sm:block"
          >
            返回对话
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!history && loading ? (
          <div className="grid min-h-[420px] place-items-center">
            <div className="text-center">
              <LoaderCircle className="mx-auto h-7 w-7 animate-spin text-cyan-600" />
              <p className="mt-3 text-sm text-slate-500">正在整理面试档案…</p>
            </div>
          </div>
        ) : !history?.items.length ? (
          <div className="mx-auto grid min-h-[520px] max-w-lg place-items-center px-6 text-center">
            <div>
              <div className="mx-auto grid h-16 w-16 place-items-center rounded-3xl bg-slate-100 text-slate-400">
                <CalendarDays className="h-7 w-7" />
              </div>
              <h3 className="mt-5 text-xl font-semibold text-slate-950">
                {error ? "面试历史加载失败" : "还没有可回溯的面试"}
              </h3>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                {error || "完成第一次模拟后，这里会记录总分、能力维度和需要重点复盘的问题。"}
              </p>
              <button
                type="button"
                onClick={onBack}
                className="mt-5 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800"
              >
                返回面试模块
              </button>
            </div>
          </div>
        ) : (
          <div className="mx-auto grid w-full max-w-7xl gap-6 px-5 py-6 lg:grid-cols-[310px_minmax(0,1fr)] lg:px-8 lg:py-9">
            <Timeline
              items={history.items}
              selectedId={selectedId}
              onSelect={onSelect}
            />

            <main className="min-w-0">
              {error && (
                <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                  {error}
                </div>
              )}
              {selected && selectedSummary ? (
                <div className="space-y-5">
                  <section className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-[0_24px_70px_-46px_rgba(15,23,42,0.5)]">
                    <div className="border-b border-slate-100 bg-slate-950 px-5 py-5 text-white md:px-7">
                      <div className="flex flex-wrap items-start justify-between gap-4">
                        <div>
                          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-300">
                            Performance snapshot
                          </p>
                          <h3 className="mt-1 text-xl font-semibold">{selected.targetRole}</h3>
                          <p className="mt-1 text-xs text-slate-400">
                            {selected.kbName} · {difficultyLabels[selected.difficulty]}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="font-mono text-4xl font-semibold tracking-tight">
                            {(selected.overallScore || 0).toFixed(1)}
                          </p>
                          <p className="text-[10px] uppercase tracking-[0.18em] text-slate-400">
                            Overall / 10
                          </p>
                        </div>
                      </div>
                      <p className="mt-4 max-w-3xl text-sm leading-6 text-slate-300">
                        {selected.summary || "本次面试暂无总评。"}
                      </p>
                      <div className="mt-5 flex justify-end">
                        <button
                          type="button"
                          onClick={onOpenFullReview}
                          className="inline-flex items-center gap-2 rounded-xl bg-cyan-300 px-4 py-2.5 text-sm font-semibold text-slate-950 shadow-sm transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                        >
                          查看完整复盘
                          <ArrowUpRight className="h-4 w-4" />
                        </button>
                      </div>
                    </div>

                    <div className="grid gap-4 p-5 md:grid-cols-[minmax(0,0.9fr)_minmax(260px,1.1fr)] md:p-7">
                      <InterviewRadar
                        dimensions={selected.scoreDimensions || history.scoreDimensions}
                        scores={selected.dimensionScores || {}}
                        overallScore={selected.overallScore}
                        compact
                      />
                      <div className="grid content-center gap-3 sm:grid-cols-2">
                        {(selected.scoreDimensions || history.scoreDimensions).map((dimension) => {
                          const score = selected.dimensionScores?.[dimension.key] || 0;
                          return (
                            <div key={dimension.key} className="rounded-2xl border border-slate-200 bg-slate-50 p-3.5">
                              <div className="flex items-center justify-between gap-3">
                                <span className="text-xs font-medium text-slate-600">{dimension.label}</span>
                                <span className="font-mono text-sm font-bold text-slate-950">{score.toFixed(1)}</span>
                              </div>
                              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
                                <div
                                  className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600"
                                  style={{ width: `${score * 10}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </section>

                  <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm md:p-7">
                    <div className="flex flex-wrap items-end justify-between gap-3">
                      <div>
                        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-amber-600">
                          Priority review
                        </p>
                        <h3 className="mt-1 text-xl font-semibold text-slate-950">评价较低的问题</h3>
                      </div>
                      <p className="text-xs text-slate-500">按单题得分从低到高，最多展示 3 题</p>
                    </div>

                    <div className="mt-5 space-y-4">
                      {lowRated.map((question) => (
                        <article
                          key={question.id}
                          className="overflow-hidden rounded-2xl border border-amber-200 bg-amber-50/40"
                        >
                          <div className="flex items-start gap-3 border-b border-amber-100 bg-white px-4 py-4 md:px-5">
                            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-amber-100 font-mono text-xs font-bold text-amber-800">
                              {question.sequence}
                            </span>
                            <div className="min-w-0 flex-1">
                              <p className="text-sm font-semibold leading-6 text-slate-900">{question.question}</p>
                            </div>
                            <span className="shrink-0 rounded-lg bg-slate-950 px-2.5 py-1.5 font-mono text-sm font-bold text-white">
                              {(question.score || 0).toFixed(1)}
                            </span>
                          </div>
                          <div className="grid gap-4 p-4 md:grid-cols-2 md:p-5">
                            <HistoryReviewBlock
                              icon={<Quote className="h-4 w-4 text-cyan-700" />}
                              title="用户原回答"
                            >
                              <p className={`whitespace-pre-wrap ${
                                question.category === "algorithm" ? "font-mono text-[12px]" : ""
                              }`}>
                                {question.skipped ? "本题已跳过" : question.answer || "未作答"}
                              </p>
                            </HistoryReviewBlock>
                            <HistoryReviewBlock
                              icon={<AlertTriangle className="h-4 w-4 text-amber-600" />}
                              title="面试官分析"
                            >
                              <p>{question.analysis || "暂无分析。"}</p>
                            </HistoryReviewBlock>
                            <div className="md:col-span-2">
                              <HistoryReviewBlock
                                icon={<CheckCircle2 className="h-4 w-4 text-emerald-600" />}
                                title="改进建议"
                              >
                                {(question.improvements || []).length ? (
                                  <ul className="space-y-1.5">
                                    {(question.improvements || []).map((item) => (
                                      <li key={item} className="flex gap-2">
                                        <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-amber-500" />
                                        <span>{item}</span>
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p>暂无改进建议。</p>
                                )}
                              </HistoryReviewBlock>
                            </div>
                          </div>
                          {question.source && (
                            <div className="flex justify-end border-t border-amber-100 bg-white px-4 py-3">
                              <button
                                type="button"
                                onClick={() => onViewSources([question.source as SourceRef])}
                                className="inline-flex items-center gap-1.5 text-xs font-semibold text-cyan-700 hover:text-cyan-900"
                              >
                                <BookOpen className="h-3.5 w-3.5" />
                                查看知识来源
                                <ArrowUpRight className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          )}
                        </article>
                      ))}
                    </div>
                  </section>
                </div>
              ) : (
                <div className="grid min-h-[420px] place-items-center rounded-[28px] border border-slate-200 bg-white">
                  <LoaderCircle className={`h-7 w-7 text-cyan-600 ${loading ? "animate-spin" : ""}`} />
                </div>
              )}
            </main>
          </div>
        )}
      </div>
      {showClearConfirm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="clear-history-title"
          className="absolute inset-0 z-50 grid place-items-center bg-slate-950/55 p-5 backdrop-blur-sm"
        >
          <div className="w-full max-w-md rounded-[28px] border border-white/70 bg-white p-6 shadow-2xl md:p-7">
            <div className="flex items-start gap-4">
              <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-rose-50 text-rose-700">
                <Trash2 className="h-5 w-5" />
              </div>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-rose-600">
                  Clear archive
                </p>
                <h2 id="clear-history-title" className="mt-1 text-xl font-semibold text-slate-950">
                  确认清空全部面试历史？
                </h2>
                <p className="mt-2 text-sm leading-6 text-slate-600">
                  将永久删除 {history?.total || 0} 次历史面试的题目、回答和评分，并重置面试能力画像。进行中的面试、个人简历和岗位 JD 不会受影响。
                </p>
                <p className="mt-2 text-xs font-semibold text-rose-600">此操作不可恢复。</p>
              </div>
            </div>
            <div className="mt-6 flex gap-3">
              <button
                type="button"
                disabled={loading}
                onClick={() => setShowClearConfirm(false)}
                className="flex-1 rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                disabled={loading}
                onClick={async () => {
                  await onClear();
                  setShowClearConfirm(false);
                }}
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-3 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-50"
              >
                {loading && <LoaderCircle className="h-4 w-4 animate-spin" />}
                确认清空
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Timeline({
  items,
  selectedId,
  onSelect,
}: {
  items: InterviewHistoryItem[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <aside className="min-w-0">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-slate-900">完成记录</h3>
        <span className="font-mono text-xs text-slate-400">{items.length} 次</span>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-3 lg:block lg:space-y-0 lg:overflow-visible lg:pb-0">
        {items.map((item, index) => {
          const date = formatCompletedAt(item.completedAt);
          const selected = item.id === selectedId;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(item.id)}
              className="group relative flex min-w-[250px] shrink-0 items-start gap-3 text-left lg:min-w-0 lg:w-full lg:pb-5"
              aria-current={selected ? "step" : undefined}
            >
              <span className="relative z-10 mt-1 shrink-0">
                <span className={`grid h-11 w-11 place-items-center rounded-full border-4 border-slate-50 font-mono text-xs font-bold shadow-sm transition ${
                  selected ? "ring-4 ring-cyan-100 " : ""
                }${scoreTone(item.overallScore || 0)}`}>
                  {(item.overallScore || 0).toFixed(1)}
                </span>
                {index < items.length - 1 && (
                  <span className="absolute left-1/2 top-11 hidden h-10 w-px -translate-x-1/2 bg-slate-200 lg:block" />
                )}
              </span>
              <span className={`min-w-0 flex-1 rounded-2xl border px-3.5 py-3 transition ${
                selected
                  ? "border-cyan-300 bg-white shadow-[0_12px_35px_-24px_rgba(8,145,178,0.8)]"
                  : "border-slate-200 bg-white/70 group-hover:border-slate-300 group-hover:bg-white"
              }`}>
                <span className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-slate-900">{date.date}</span>
                  <span className="font-mono text-[10px] text-slate-400">{date.time}</span>
                </span>
                <span className="mt-1 block truncate text-sm font-medium text-slate-700">{item.targetRole}</span>
                <span className="mt-1 block truncate text-[11px] text-slate-400">
                  {item.kbName} · {difficultyLabels[item.difficulty]}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function HistoryReviewBlock({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="flex items-center gap-2 text-xs font-semibold text-slate-700">
        {icon}
        {title}
      </h4>
      <div className="mt-2 text-sm leading-6 text-slate-600">{children}</div>
    </div>
  );
}
