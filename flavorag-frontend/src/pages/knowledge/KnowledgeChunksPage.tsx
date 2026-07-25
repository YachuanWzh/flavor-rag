import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { fetchChunks } from "@/services/knowledgeService";
import type { KnowledgeChunk } from "@/types";

const PAGE_SIZE = 10;

const enabledLabel = (enabled?: number | null) =>
  enabled === 1 ? "启用" : "禁用";

interface DocRouteState {
  docName?: string;
  chunkCount?: number;
}

export default function KnowledgeChunksPage() {
  const { kbId, docId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const docState = (location.state as DocRouteState) || {};
  const docName = docState.docName || docId || "";
  const docChunkCount = docState.chunkCount ?? 0;

  const [allChunks, setAllChunks] = useState<KnowledgeChunk[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageNo, setPageNo] = useState(1);
  const [enabledFilter, setEnabledFilter] = useState<number | undefined>();

  const load = async () => {
    if (!docId) return;
    setLoading(true);
    try {
      const data = await fetchChunks(docId);
      setAllChunks(data);
    } catch {
      console.error("加载分块列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [docId]);

  // 前端筛选 + 分页
  const filtered = useMemo(() => {
    if (enabledFilter === undefined) return allChunks;
    return allChunks.filter((c) => c.enabled === enabledFilter);
  }, [allChunks, enabledFilter]);

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.min(pageNo, totalPages);
  const chunks = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, currentPage]);

  const handleFilterChange = (value: string) => {
    setPageNo(1);
    setEnabledFilter(value === "all" ? undefined : Number(value));
  };

  // 刷新时保持当前筛选和页码
  const handleRefresh = () => {
    load();
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate(`/knowledge/${kbId}`)}
              className="p-1 hover:bg-gray-200 rounded"
              title="返回文档列表"
            >
              <ArrowLeft size={20} />
            </button>
            <div>
              <h1 className="text-xl font-bold">分块管理</h1>
              <p className="text-sm text-gray-500 mt-0.5">
                {docName}
                <span className="mx-1">·</span>
                {docChunkCount || total} 个分块
              </p>
            </div>
          </div>
          <button
            onClick={handleRefresh}
            className="flex items-center gap-1 px-3 py-2 text-sm border rounded-lg hover:bg-gray-100"
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>

        {/* Filter bar */}
        <div className="mb-4 flex items-center gap-3">
          <select
            value={enabledFilter === undefined ? "all" : String(enabledFilter)}
            onChange={(e) => handleFilterChange(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm bg-white"
          >
            <option value="all">全部状态</option>
            <option value="1">已启用</option>
            <option value="0">已禁用</option>
          </select>
          <span className="text-sm text-gray-400">共 {total} 条</span>
        </div>

        {/* Chunk list */}
        {loading ? (
          <div className="text-center text-gray-400 py-12">加载中...</div>
        ) : chunks.length === 0 ? (
          <div className="text-center text-gray-400 py-12">暂无分块</div>
        ) : (
          <div className="space-y-2">
            {chunks.map((chunk) => (
              <div
                key={chunk.id}
                className="bg-white border rounded-lg p-4 hover:border-gray-400 transition-colors"
              >
                {/* Chunk header */}
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-mono bg-gray-100 px-2 py-0.5 rounded">
                    #{chunk.chunkIndex}
                  </span>
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${
                      chunk.enabled === 1
                        ? "bg-green-50 text-green-600"
                        : "bg-gray-100 text-gray-400"
                    }`}
                  >
                    {enabledLabel(chunk.enabled)}
                  </span>
                  <span className="text-xs text-gray-400">
                    {chunk.charCount ?? "-"} 字符
                  </span>
                  {chunk.tokenCount !== undefined && (
                    <span className="text-xs text-gray-400">
                      ~{chunk.tokenCount} tokens
                    </span>
                  )}
                </div>
                {/* Chunk content */}
                <p className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed break-all">
                  {chunk.content}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="mt-6 flex items-center justify-between text-sm text-gray-500">
            <span>
              第 {currentPage} / {totalPages} 页
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPageNo((p) => Math.max(1, p - 1))}
                disabled={currentPage <= 1}
                className="px-3 py-1.5 border rounded hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed text-sm"
              >
                上一页
              </button>
              <button
                onClick={() => setPageNo((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage >= totalPages}
                className="px-3 py-1.5 border rounded hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed text-sm"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
