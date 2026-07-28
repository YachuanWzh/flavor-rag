import { useEffect, useState, useCallback } from "react";
import {
  Brain, ChevronRight, Search, RefreshCw, Trash2,
  ThumbsUp, ThumbsDown, MessageSquare, Clock, Target,
  TrendingUp, Database, Zap, X,
} from "lucide-react";
import { api } from "@/services/api";

// ─── Types ───

interface ProfileListItem {
  id: string;
  userId: string;
  username: string;
  userRole: string;
  tenantId: string;
  domains: string[];
  expertiseLevel: string | null;
  totalQueries: number;
  totalConversations: number;
  thumbsUp: number;
  thumbsDown: number;
  mem0FactsCount: number;
  profileVersion: number;
  lastActiveTime: string | null;
  updatedAt: string | null;
}

interface ProfileDetail {
  userId: string;
  username: string;
  userRole: string;
  tenantId: string;
  departmentId: string | null;
  userCreateTime: string | null;
  domains: string[];
  expertiseLevel: string | null;
  domainSummary: string | null;
  intentDistribution: Record<string, number>;
  preferredKbs: { kb_id: string; kb_name: string; count: number }[];
  preferredDocTypes: Record<string, number>;
  avgQueryLength: number | null;
  deepThinkingRate: number | null;
  graphRagRate: number | null;
  hydeRate: number | null;
  thumbsUpCount: number;
  thumbsDownCount: number;
  followUpRate: number | null;
  satisfactionTopics: string[];
  mem0FactsCount: number;
  mem0LastSync: string | null;
  totalQueries: number;
  totalConversations: number;
  lastActiveTime: string | null;
  profileVersion: number;
  createdAt: string | null;
  updatedAt: string | null;
}

interface MemoryItem {
  memory_id: string;
  content: string;
  metadata: Record<string, unknown>;
}

// ─── Component ───

export default function UserProfilePage() {
  const [list, setList] = useState<ProfileListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [pageSize] = useState(20);

  // Detail drawer
  const [detail, setDetail] = useState<ProfileDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Memories
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [memoriesLoading, setMemoriesLoading] = useState(false);
  const [showMemories, setShowMemories] = useState(false);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get(
        `/api/admin/profiles?search=${encodeURIComponent(search)}&page=${page}&page_size=${pageSize}`
      ) as any;
      setList(data.items || []);
      setTotal(data.total || 0);
    } catch {
      setList([]);
    } finally {
      setLoading(false);
    }
  }, [search, page, pageSize]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openDetail = async (userId: string) => {
    setDetailLoading(true);
    setShowMemories(false);
    try {
      const data = await api.get(`/api/admin/profiles/${userId}`) as any;
      setDetail(data);
    } catch {
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const loadMemories = async (userId: string) => {
    setMemoriesLoading(true);
    setShowMemories(true);
    try {
      const data = await api.get(`/api/admin/profiles/${userId}/memories?limit=200`) as any;
      setMemories(data.memories || []);
    } catch {
      setMemories([]);
    } finally {
      setMemoriesLoading(false);
    }
  };

  const deleteMemory = async (userId: string, memoryId: string) => {
    try {
      await api.delete(`/api/admin/profiles/${userId}/memories/${memoryId}`);
      setMemories(prev => prev.filter(m => m.memory_id !== memoryId));
      if (detail) {
        setDetail({ ...detail, mem0FactsCount: Math.max(0, detail.mem0FactsCount - 1) });
      }
    } catch {
      // ignore
    }
  };

  const rebuildProfile = async (userId: string) => {
    try {
      await api.post(`/api/admin/profiles/${userId}/rebuild`);
      await openDetail(userId);
    } catch {
      // ignore
    }
  };

  const totalPages = Math.ceil(total / pageSize) || 1;

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">Memory</p>
          <h2 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">用户画像</h2>
          <p className="mt-1 text-base text-slate-500">基于 mem0 长期记忆与行为统计的七维用户画像管理。</p>
        </div>
      </div>

      {/* Search bar */}
      <div className="mt-5 flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
          <input
            type="text"
            placeholder="搜索用户名..."
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1); }}
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm outline-none focus:border-cyan-500"
          />
        </div>
        <button
          onClick={() => loadList()}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          刷新
        </button>
      </div>

      {/* Table */}
      <div className="mt-4 overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="hidden grid-cols-[1fr_140px_80px_70px_90px_70px_110px_24px] gap-2 border-b border-slate-100 bg-slate-50/70 px-4 py-3 text-xs font-semibold uppercase tracking-[0.12em] text-slate-400 md:grid">
          <span>用户</span><span>领域</span><span className="text-center">等级</span><span className="text-center">提问</span><span className="text-center">赞/踩</span><span className="text-center">记忆</span><span className="text-center">更新时间</span><span />
        </div>
        <div className="divide-y divide-slate-100">
          {loading ? (
            <p className="py-10 text-center text-sm text-slate-400">加载中...</p>
          ) : list.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-400">暂无画像数据</p>
          ) : list.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => openDetail(item.userId)}
              className="grid w-full gap-2 px-4 py-3.5 text-left transition hover:bg-cyan-50/40 md:grid-cols-[1fr_140px_80px_70px_90px_70px_110px_24px] md:items-center"
            >
              <div className="min-w-0">
                <p className="truncate text-[15px] font-medium text-slate-800">{item.username}</p>
                <p className="mt-0.5 truncate text-xs text-slate-400">{item.tenantId} · {item.userRole}</p>
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {(item.domains || []).slice(0, 2).map(d => (
                  <span key={d} className="whitespace-nowrap rounded-full bg-cyan-50 px-2 py-0.5 text-[11px] text-cyan-700">{d}</span>
                ))}
              </div>
              <div className="flex justify-center">
                <span className="flex h-[22px] items-center justify-center rounded-full px-2.5 text-xs font-medium bg-slate-100 text-slate-600 whitespace-nowrap">
                  {item.expertiseLevel || "—"}
                </span>
              </div>
              <span className="text-center font-mono text-sm tabular-nums text-slate-600">{item.totalQueries}</span>
              <span className="text-center font-mono text-sm tabular-nums text-slate-600 whitespace-nowrap">
                <span className="text-emerald-600">{item.thumbsUp}</span> / <span className="text-rose-600">{item.thumbsDown}</span>
              </span>
              <span className="text-center font-mono text-sm tabular-nums text-slate-600">{item.mem0FactsCount}</span>
              <span className="text-center text-xs text-slate-400 tabular-nums whitespace-nowrap">{item.updatedAt?.slice(5, 16) || "—"}</span>
              <ChevronRight className="h-4 w-4 text-slate-300" />
            </button>
          ))}
        </div>

        {/* Pagination */}
        {total > pageSize && (
          <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2.5">
            <span className="text-xs text-slate-400">共 {total} 条</span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-40"
              >上一页</button>
              <span className="text-xs text-slate-500">{page} / {totalPages}</span>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-40"
              >下一页</button>
            </div>
          </div>
        )}
      </div>

      {/* Detail Drawer */}
      {detail && (
        <ProfileDetailDrawer
          detail={detail}
          loading={detailLoading}
          onClose={() => setDetail(null)}
          onRebuild={() => rebuildProfile(detail.userId)}
          onShowMemories={() => loadMemories(detail.userId)}
          memories={memories}
          memoriesLoading={memoriesLoading}
          showMemories={showMemories}
          onDeleteMemory={(mid) => deleteMemory(detail.userId, mid)}
        />
      )}
    </div>
  );
}

// ─── Detail Drawer Component ───

function ProfileDetailDrawer({
  detail, loading, onClose, onRebuild, onShowMemories,
  memories, memoriesLoading, showMemories, onDeleteMemory,
}: {
  detail: ProfileDetail;
  loading: boolean;
  onClose: () => void;
  onRebuild: () => void;
  onShowMemories: () => void;
  memories: MemoryItem[];
  memoriesLoading: boolean;
  showMemories: boolean;
  onDeleteMemory: (memoryId: string) => void;
}) {
  const intentLabels: Record<string, string> = {
    factual: "事实查询", analysis: "对比分析", guidance: "操作指引",
    troubleshooting: "故障排查", general: "通用",
  };

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop */}
      <div className="flex-1 bg-black/30" onClick={onClose} />

      {/* Panel */}
      <div className="flex w-[640px] flex-col bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-full bg-cyan-50">
              <Brain className="h-5 w-5 text-cyan-600" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-slate-900">{detail.username}</h3>
              <p className="text-xs text-slate-400">{detail.tenantId} · {detail.userRole} · v{detail.profileVersion}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onRebuild} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">
              <RefreshCw className="mr-1 inline h-3.5 w-3.5" />重建
            </button>
            <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {loading ? (
            <p className="py-20 text-center text-sm text-slate-400">加载中...</p>
          ) : (
            <>
              {/* D1: Basic Info */}
              <Section title="基础信息" icon={<Database className="h-4 w-4" />}>
                <div className="grid grid-cols-3 gap-3">
                  <Metric label="总提问" value={String(detail.totalQueries)} icon={<MessageSquare className="h-3.5 w-3.5" />} />
                  <Metric label="总会话" value={String(detail.totalConversations)} icon={<MessageSquare className="h-3.5 w-3.5" />} />
                  <Metric label="最后活跃" value={detail.lastActiveTime?.slice(5, 16) || "—"} icon={<Clock className="h-3.5 w-3.5" />} />
                </div>
              </Section>

              {/* D2: Professional Domain */}
              <Section title="专业领域画像" icon={<Target className="h-4 w-4" />}>
                <div className="flex flex-wrap items-center gap-1.5 mb-2">
                  {detail.domains.length > 0 ? detail.domains.map(d => (
                    <span key={d} className="whitespace-nowrap rounded-full bg-cyan-50 px-2.5 py-1 text-xs font-medium text-cyan-700">{d}</span>
                  )) : <span className="text-xs text-slate-400">暂无领域标签</span>}
                </div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs uppercase text-slate-400">专业水平</span>
                  <span className={`flex h-[24px] items-center justify-center rounded-full px-2.5 text-xs font-medium whitespace-nowrap ${
                    detail.expertiseLevel === "expert" ? "bg-emerald-100 text-emerald-700" :
                    detail.expertiseLevel === "mid" ? "bg-amber-100 text-amber-700" :
                    "bg-slate-100 text-slate-600"
                  }`}>
                    {detail.expertiseLevel || "未知"}
                  </span>
                </div>
                {detail.domainSummary && (
                  <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-600">{detail.domainSummary}</p>
                )}
              </Section>

              {/* D3: Intent Distribution */}
              <Section title="意图偏好分布" icon={<Zap className="h-4 w-4" />}>
                <div className="space-y-2">
                  {Object.entries(detail.intentDistribution).length > 0 ? (
                    Object.entries(detail.intentDistribution)
                      .sort((a, b) => b[1] - a[1])
                      .map(([key, val]) => (
                        <div key={key} className="flex items-center gap-2">
                          <span className="w-20 shrink-0 text-xs text-slate-500">{intentLabels[key] || key}</span>
                          <div className="flex-1 h-5 rounded-full bg-slate-100 overflow-hidden">
                            <div className="h-full bg-cyan-400 rounded-full" style={{ width: `${val * 100}%` }} />
                          </div>
                          <span className="w-10 text-right font-mono text-xs text-slate-400 tabular-nums">{(val * 100).toFixed(0)}%</span>
                        </div>
                      ))
                  ) : <span className="text-xs text-slate-400">暂无意图数据</span>}
                </div>
              </Section>

              {/* D4: KB Preference */}
              <Section title="知识库偏好" icon={<Database className="h-4 w-4" />}>
                <div className="space-y-1.5">
                  {detail.preferredKbs.length > 0 ? detail.preferredKbs.map(kb => (
                    <div key={kb.kb_id} className="flex items-center justify-between text-xs">
                      <span className="truncate text-slate-600">{kb.kb_name}</span>
                      <span className="font-mono tabular-nums text-slate-400">{kb.count} 次</span>
                    </div>
                  )) : <span className="text-xs text-slate-400">暂无知识库偏好</span>}
                </div>
                {Object.keys(detail.preferredDocTypes).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {Object.entries(detail.preferredDocTypes).map(([t, r]) => (
                      <span key={t} className="whitespace-nowrap rounded bg-slate-50 px-2 py-0.5 text-[11px] text-slate-500">{t}: {(r * 100).toFixed(0)}%</span>
                    ))}
                  </div>
                )}
              </Section>

              {/* D5: Query Style */}
              <Section title="查询风格画像" icon={<TrendingUp className="h-4 w-4" />}>
                <div className="grid grid-cols-4 gap-3">
                  <Metric label="平均长度" value={String(detail.avgQueryLength?.toFixed(0) || "—")} />
                  <Metric label="深度思考" value={`${((detail.deepThinkingRate || 0) * 100).toFixed(0)}%`} />
                  <Metric label="GraphRAG" value={`${((detail.graphRagRate || 0) * 100).toFixed(0)}%`} />
                  <Metric label="HyDE" value={`${((detail.hydeRate || 0) * 100).toFixed(0)}%`} />
                </div>
              </Section>

              {/* D6: Feedback Signals */}
              <Section title="反馈信号画像" icon={<ThumbsUp className="h-4 w-4" />}>
                <div className="grid grid-cols-3 gap-3">
                  <Metric label="赞" value={String(detail.thumbsUpCount)} icon={<ThumbsUp className="h-3.5 w-3.5 text-emerald-500" />} />
                  <Metric label="踩" value={String(detail.thumbsDownCount)} icon={<ThumbsDown className="h-3.5 w-3.5 text-rose-500" />} />
                  <Metric label="追问率" value={`${((detail.followUpRate || 0) * 100).toFixed(0)}%`} />
                </div>
              </Section>

              {/* D7: Memory Facts */}
              <Section
                title="记忆事实摘要 (mem0)"
                icon={<Brain className="h-4 w-4" />}
                action={
                  <button onClick={onShowMemories} className="text-xs text-cyan-600 hover:underline">
                    {showMemories ? "隐藏" : "查看全部"}
                  </button>
                }
              >
                <div className="flex items-center gap-4 mb-2">
                  <span className="text-xs text-slate-500">记忆条目</span>
                  <span className="font-mono text-lg font-semibold text-slate-800 tabular-nums">{detail.mem0FactsCount}</span>
                  <span className="text-xs text-slate-400">最后同步：{detail.mem0LastSync?.slice(5, 16) || "—"}</span>
                </div>
                {showMemories && (
                  <div className="space-y-2 max-h-60 overflow-y-auto">
                    {memoriesLoading ? (
                      <p className="text-xs text-slate-400">加载中...</p>
                    ) : memories.length === 0 ? (
                      <p className="text-xs text-slate-400">暂无记忆事实</p>
                    ) : memories.map(m => (
                      <div key={m.memory_id} className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2">
                        <p className="flex-1 text-xs leading-relaxed text-slate-600">{m.content}</p>
                        <button
                          onClick={() => onDeleteMemory(m.memory_id)}
                          className="shrink-0 rounded p-1 text-slate-300 hover:bg-rose-50 hover:text-rose-500"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </Section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Helper Components ───

function Section({ title, icon, action, children }: {
  title: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-100 bg-white">
      <header className="flex items-center justify-between border-b border-slate-50 px-4 py-3">
        <div className="flex items-center gap-1.5 text-slate-500">
          {icon}
          <span className="text-xs font-semibold uppercase tracking-wider">{title}</span>
        </div>
        {action}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function Metric({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <div className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-slate-400">
        {icon}
        {label}
      </div>
      <p className="mt-0.5 font-mono text-base font-semibold text-slate-700 tabular-nums">{value}</p>
    </div>
  );
}
