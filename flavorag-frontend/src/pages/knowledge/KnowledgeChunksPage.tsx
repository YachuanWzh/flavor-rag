import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Loader2,
  PauseCircle,
  RefreshCw,
} from "lucide-react";
import {
  fetchChunks,
  updateChunkStatus,
} from "@/services/knowledgeService";
import type { KnowledgeChunk } from "@/types";

const PAGE_SIZE = 10;
type StatusFilter = "all" | "enabled" | "disabled";

interface DocRouteState {
  docName?: string;
  chunkCount?: number;
}

const blockTypeLabels: Record<string, string> = {
  paragraph: "正文",
  text: "正文",
  table: "表格",
  image: "图片",
  heading: "标题",
  list: "列表",
};

function pageLabel(chunk: KnowledgeChunk) {
  if (chunk.pageStart == null) return null;
  if (chunk.pageEnd != null && chunk.pageEnd !== chunk.pageStart) {
    return `第 ${chunk.pageStart}–${chunk.pageEnd} 页`;
  }
  return `第 ${chunk.pageStart} 页`;
}

export default function KnowledgeChunksPage() {
  const { kbId, docId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const docState = (location.state as DocRouteState) || {};
  const docName = docState.docName || docId || "";

  const [allChunks, setAllChunks] = useState<KnowledgeChunk[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageNo, setPageNo] = useState(1);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [savingIds, setSavingIds] = useState<Set<string>>(new Set());
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");

  const load = async () => {
    if (!docId) return;
    setLoading(true);
    setError("");
    try {
      setAllChunks(await fetchChunks(docId));
    } catch (err: any) {
      setError(err?.message || "加载切片列表失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [docId]);

  const enabledCount = useMemo(
    () => allChunks.filter((chunk) => chunk.enabled !== 0).length,
    [allChunks],
  );
  const disabledCount = allChunks.length - enabledCount;

  const filtered = useMemo(() => {
    if (statusFilter === "enabled") {
      return allChunks.filter((chunk) => chunk.enabled !== 0);
    }
    if (statusFilter === "disabled") {
      return allChunks.filter((chunk) => chunk.enabled === 0);
    }
    return allChunks;
  }, [allChunks, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(pageNo, totalPages);
  const chunks = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, currentPage]);

  const handleFilterChange = (filter: StatusFilter) => {
    setStatusFilter(filter);
    setPageNo(1);
  };

  const handleToggle = async (chunk: KnowledgeChunk) => {
    if (!docId || savingIds.has(chunk.id)) return;

    const previousEnabled = chunk.enabled;
    const nextEnabled = previousEnabled === 0;
    setError("");
    setSavingIds((current) => new Set(current).add(chunk.id));
    setAllChunks((current) =>
      current.map((item) =>
        item.id === chunk.id ? { ...item, enabled: nextEnabled ? 1 : 0 } : item,
      ),
    );

    try {
      const updated = await updateChunkStatus(docId, chunk.id, nextEnabled);
      setAllChunks((current) =>
        current.map((item) =>
          item.id === chunk.id
            ? {
                ...item,
                enabled: updated.enabled ?? (nextEnabled ? 1 : 0),
                updateTime: updated.updateTime,
              }
            : item,
        ),
      );
    } catch (err: any) {
      setAllChunks((current) =>
        current.map((item) =>
          item.id === chunk.id ? { ...item, enabled: previousEnabled } : item,
        ),
      );
      setError(err?.message || "状态更新失败，已恢复原状态");
    } finally {
      setSavingIds((current) => {
        const next = new Set(current);
        next.delete(chunk.id);
        return next;
      });
    }
  };

  const toggleExpanded = (chunkId: string) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(chunkId)) next.delete(chunkId);
      else next.add(chunkId);
      return next;
    });
  };

  const filters: Array<{
    value: StatusFilter;
    label: string;
    count: number;
  }> = [
    { value: "all", label: "全部", count: allChunks.length },
    { value: "enabled", label: "已启用", count: enabledCount },
    { value: "disabled", label: "已禁用", count: disabledCount },
  ];

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
        <header className="mb-6">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex min-w-0 items-start gap-3">
              <button
                type="button"
                onClick={() => navigate(`/knowledge/${kbId}`)}
                className="mt-0.5 rounded-md border border-slate-200 bg-white p-2 text-slate-500 shadow-sm transition hover:border-slate-300 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
                title="返回文档列表"
                aria-label="返回文档列表"
              >
                <ArrowLeft size={18} />
              </button>
              <div className="min-w-0">
                <div className="mb-1 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.16em] text-blue-600">
                  <span className="h-px w-5 bg-blue-500" />
                  Knowledge slices
                </div>
                <h1 className="text-2xl font-semibold tracking-tight text-slate-950">
                  分块管理
                </h1>
                <p className="mt-1 truncate text-sm text-slate-500" title={docName}>
                  {docName}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="inline-flex h-9 items-center justify-center gap-2 self-start rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-wait disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
            >
              <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
              刷新
            </button>
          </div>
        </header>

        <section className="mb-5 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="grid divide-y divide-slate-100 sm:grid-cols-[1fr_auto] sm:divide-x sm:divide-y-0">
            <div className="flex min-w-0 items-center gap-4 px-5 py-4">
              <div className="hidden h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600 sm:flex">
                <FileText size={20} />
              </div>
              <div>
                <p className="text-sm font-medium text-slate-800">
                  选择哪些内容参与知识检索
                </p>
                <p className="mt-0.5 text-xs leading-5 text-slate-500">
                  禁用后该切片会立即从问答召回结果中排除，无需重新解析文档。
                </p>
              </div>
            </div>
            <div className="flex items-center gap-5 bg-slate-50/70 px-5 py-3 sm:min-w-[260px] sm:justify-center">
              <div>
                <p className="text-lg font-semibold tabular-nums text-emerald-700">
                  {enabledCount}
                </p>
                <p className="text-xs text-slate-500">参与检索</p>
              </div>
              <div className="h-8 w-px bg-slate-200" />
              <div>
                <p className="text-lg font-semibold tabular-nums text-slate-500">
                  {disabledCount}
                </p>
                <p className="text-xs text-slate-500">已排除</p>
              </div>
            </div>
          </div>
        </section>

        {error && (
          <div
            role="alert"
            className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
          >
            <span>{error}</span>
            <button
              type="button"
              onClick={() => setError("")}
              className="shrink-0 font-medium underline underline-offset-2"
            >
              关闭
            </button>
          </div>
        )}

        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div
            className="inline-flex self-start rounded-lg border border-slate-200 bg-white p-1 shadow-sm"
            aria-label="切片状态筛选"
          >
            {filters.map((filter) => (
              <button
                key={filter.value}
                type="button"
                onClick={() => handleFilterChange(filter.value)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                  statusFilter === filter.value
                    ? "bg-slate-900 text-white shadow-sm"
                    : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"
                }`}
              >
                {filter.label}
                <span
                  className={`ml-1.5 tabular-nums ${
                    statusFilter === filter.value
                      ? "text-slate-300"
                      : "text-slate-400"
                  }`}
                >
                  {filter.count}
                </span>
              </button>
            ))}
          </div>
          <p className="text-xs text-slate-400">
            当前显示 {filtered.length} 个切片
          </p>
        </div>

        {loading && allChunks.length === 0 ? (
          <div className="flex min-h-64 items-center justify-center rounded-xl border border-slate-200 bg-white text-sm text-slate-400">
            <Loader2 className="mr-2 animate-spin" size={18} />
            正在加载切片
          </div>
        ) : chunks.length === 0 ? (
          <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white px-6 text-center">
            <FileText className="mb-3 text-slate-300" size={34} />
            <p className="text-sm font-medium text-slate-600">
              这个状态下没有切片
            </p>
            <p className="mt-1 text-xs text-slate-400">
              切换上方筛选条件查看其他内容
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {chunks.map((chunk) => {
              const isEnabled = chunk.enabled !== 0;
              const isSaving = savingIds.has(chunk.id);
              const isExpanded = expandedIds.has(chunk.id);
              const isLong =
                chunk.content.length > 520 ||
                chunk.content.split("\n").length > 8;
              const locationText = pageLabel(chunk);

              return (
                <article
                  key={chunk.id}
                  className={`group grid overflow-hidden rounded-xl border bg-white shadow-sm transition sm:grid-cols-[84px_minmax(0,1fr)] ${
                    isEnabled
                      ? "border-slate-200 hover:border-blue-200 hover:shadow-md"
                      : "border-slate-200 bg-slate-50/70"
                  }`}
                >
                  <div
                    className={`flex items-center justify-between border-b px-4 py-3 sm:flex-col sm:items-start sm:justify-start sm:border-b-0 sm:border-r sm:px-5 sm:py-5 ${
                      isEnabled
                        ? "border-blue-100 bg-blue-50/70"
                        : "border-slate-200 bg-slate-100/80"
                    }`}
                  >
                    <span
                      className={`text-[10px] font-semibold uppercase tracking-[0.14em] ${
                        isEnabled ? "text-blue-500" : "text-slate-400"
                      }`}
                    >
                      Slice
                    </span>
                    <span
                      className={`mt-0 font-mono text-xl font-semibold tabular-nums sm:mt-2 ${
                        isEnabled ? "text-blue-950" : "text-slate-500"
                      }`}
                    >
                      {String(chunk.chunkIndex + 1).padStart(2, "0")}
                    </span>
                  </div>

                  <div className="min-w-0 px-4 py-4 sm:px-5">
                    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium ${
                          isEnabled
                            ? "bg-emerald-50 text-emerald-700"
                            : "bg-slate-200/70 text-slate-500"
                        }`}
                      >
                        {isEnabled ? (
                          <CheckCircle2 size={13} />
                        ) : (
                          <PauseCircle size={13} />
                        )}
                        {isEnabled ? "参与检索" : "不参与检索"}
                      </span>
                      {chunk.blockType && (
                        <span className="text-xs text-slate-500">
                          {blockTypeLabels[chunk.blockType.toLowerCase()] ||
                            chunk.blockType}
                        </span>
                      )}
                      {locationText && (
                        <span className="text-xs text-slate-500">
                          {locationText}
                        </span>
                      )}
                      <span className="text-xs tabular-nums text-slate-400">
                        {(chunk.charCount ?? chunk.content.length).toLocaleString()}
                        {" 字符"}
                      </span>
                      {chunk.tokenCount != null && (
                        <span className="text-xs tabular-nums text-slate-400">
                          约 {chunk.tokenCount.toLocaleString()} tokens
                        </span>
                      )}
                    </div>

                    <div className="relative">
                      <div
                        className={`whitespace-pre-wrap break-words text-sm leading-7 ${
                          isEnabled ? "text-slate-700" : "text-slate-500"
                        } ${
                          isLong && !isExpanded
                            ? "max-h-40 overflow-hidden"
                            : ""
                        }`}
                      >
                        {chunk.content}
                      </div>
                      {isLong && !isExpanded && (
                        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-white to-transparent" />
                      )}
                    </div>

                    <div className="mt-4 flex min-h-8 items-end justify-between gap-3 border-t border-slate-100 pt-3">
                      <div>
                        {isLong && (
                          <button
                            type="button"
                            onClick={() => toggleExpanded(chunk.id)}
                            className="inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
                          >
                            {isExpanded ? (
                              <>
                                收起内容 <ChevronUp size={14} />
                              </>
                            ) : (
                              <>
                                展开全文 <ChevronDown size={14} />
                              </>
                            )}
                          </button>
                        )}
                      </div>
                      <div className="inline-flex items-center gap-2.5">
                        <span className="text-xs font-medium text-slate-500">
                          {isSaving
                            ? "保存中"
                            : isEnabled
                              ? "已启用"
                              : "已禁用"}
                        </span>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={isEnabled}
                          aria-label={`${isEnabled ? "禁用" : "启用"}切片 ${
                            chunk.chunkIndex + 1
                          }`}
                          disabled={isSaving}
                          onClick={() => void handleToggle(chunk)}
                          className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-wait disabled:opacity-60 ${
                            isEnabled ? "bg-blue-600" : "bg-slate-300"
                          }`}
                        >
                          <span
                            className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                              isEnabled ? "translate-x-6" : "translate-x-1"
                            }`}
                          />
                        </button>
                      </div>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {totalPages > 1 && (
          <nav
            className="mt-6 flex items-center justify-between border-t border-slate-200 pt-5 text-sm text-slate-500"
            aria-label="切片分页"
          >
            <span className="tabular-nums">
              第 {currentPage} / {totalPages} 页
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPageNo((page) => Math.max(1, page - 1))}
                disabled={currentPage <= 1}
                className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                上一页
              </button>
              <button
                type="button"
                onClick={() =>
                  setPageNo((page) => Math.min(totalPages, page + 1))
                }
                disabled={currentPage >= totalPages}
                className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                下一页
              </button>
            </div>
          </nav>
        )}
      </main>
    </div>
  );
}
