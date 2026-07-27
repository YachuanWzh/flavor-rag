import { useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  BookOpenText,
  Boxes,
  ChevronDown,
  ExternalLink,
  FileText,
  GitBranch,
  Hash,
  Image as ImageIcon,
  Layers3,
  MapPin,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import type { SourceRef } from "@/types";

interface Props {
  open: boolean;
  sources: SourceRef[];
  onClose: () => void;
}

const CHANNEL_META: Record<string, { label: string; color: string; rail: string }> = {
  vector: { label: "语义", color: "text-cyan-200", rail: "bg-cyan-400" },
  keyword: { label: "关键词", color: "text-amber-200", rail: "bg-amber-400" },
  graph: { label: "图谱", color: "text-violet-200", rail: "bg-violet-400" },
  web_search: { label: "联网", color: "text-emerald-200", rail: "bg-emerald-400" },
};

function channelMeta(name: string) {
  return CHANNEL_META[name] || {
    label: name,
    color: "text-slate-200",
    rail: "bg-slate-400",
  };
}

function scoreText(score?: number | null, digits = 3) {
  return typeof score === "number" && Number.isFinite(score)
    ? score.toFixed(digits)
    : "—";
}

function pageLabel(source: SourceRef) {
  if (!source.pageStart) return "无页码";
  return source.pageEnd && source.pageEnd !== source.pageStart
    ? `${source.pageStart}–${source.pageEnd} 页`
    : `第 ${source.pageStart} 页`;
}

export default function SourcesDrawer({ open, sources, onClose }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  useEffect(() => setExpandedId(null), [sources]);

  const summary = useMemo(() => {
    const documents = new Set(sources.map((source) => source.documentId).filter(Boolean));
    const channels = new Set(
      sources.flatMap((source) =>
        Object.keys(source.channelScores || {}).length
          ? Object.keys(source.channelScores || {})
          : source.matchedChannels || []
      )
    );
    const compensated = sources.filter((source) => source.neighborOf?.length).length;
    return { documents: documents.size, channels: channels.size, compensated };
  }, [sources]);

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="关闭来源面板"
          className="fixed inset-0 z-40 cursor-default bg-slate-950/25 backdrop-blur-[1px]"
          onClick={onClose}
        />
      )}

      <aside
        aria-hidden={!open}
        className={`fixed inset-y-0 right-0 z-50 flex w-[560px] max-w-[96vw] flex-col overflow-hidden border-l border-slate-800 bg-slate-950 text-slate-100 shadow-[-24px_0_70px_rgba(15,23,42,0.28)] transition-transform duration-300 ease-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <header className="relative shrink-0 overflow-hidden border-b border-white/10 px-5 pb-5 pt-5">
          <div className="absolute -right-16 -top-20 h-52 w-52 rounded-full bg-cyan-400/10 blur-3xl" />
          <div className="relative flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-300">
                <Layers3 className="h-3.5 w-3.5" />
                Evidence trail
              </div>
              <h2 className="mt-2 text-xl font-semibold tracking-tight text-white">
                这份回答用了哪些证据
              </h2>
              <p className="mt-1 max-w-md text-xs leading-5 text-slate-400">
                多路召回先由 RRF 合并，再由精排模型判断最终相关性。展开来源可查看每一路的排名和贡献。
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-white/10 bg-white/5 p-2 text-slate-300 transition hover:bg-white/10 hover:text-white focus:outline-none focus:ring-2 focus:ring-cyan-400"
              aria-label="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className={`relative mt-4 grid divide-x divide-white/10 rounded-xl border border-white/10 bg-white/[0.045] ${summary.compensated ? "grid-cols-4" : "grid-cols-3"}`}>
            <SummaryStat label="最终证据" value={sources.length} />
            <SummaryStat label="覆盖文档" value={summary.documents} />
            <SummaryStat label="参与通道" value={summary.channels} />
            {summary.compensated > 0 && (
              <SummaryStat label="邻近补偿" value={summary.compensated} />
            )}
          </div>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto bg-slate-100 p-3 text-slate-900 md:p-4">
          {sources.map((source, index) => {
            const id = source.chunkId || String(index);
            const expanded = expandedId === id;
            const channels = Object.entries(source.channelScores || {});
            const primaryScore = source.rerankScore ?? source.score;
            return (
              <article
                key={id}
                className={`overflow-hidden rounded-2xl border bg-white transition ${
                  expanded
                    ? "border-cyan-300 shadow-[0_12px_36px_rgba(8,145,178,0.12)]"
                    : "border-slate-200 shadow-sm hover:border-slate-300"
                }`}
              >
                <button
                  type="button"
                  className="w-full p-4 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-cyan-500"
                  onClick={() => setExpandedId(expanded ? null : id)}
                  aria-expanded={expanded}
                >
                  <div className="flex items-start gap-3">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-950 font-mono text-xs font-semibold text-cyan-300">
                      {String(index + 1).padStart(2, "0")}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <h3 className="max-w-full truncate text-sm font-semibold text-slate-900">
                          {source.docName || "未命名文档"}
                        </h3>
                        <Pill icon={<MapPin />} text={pageLabel(source)} />
                        <Pill
                          icon={<Boxes />}
                          text={source.blockType || "TEXT"}
                        />
                        {!!source.neighborOf?.length && (
                          <Pill
                            icon={<GitBranch />}
                            text="邻近补偿"
                            tone="emerald"
                          />
                        )}
                      </div>
                      <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-600">
                        {source.content || "该来源没有可展示的文本摘要。"}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                        {source.rerankScore != null ? "精排分" : "RRF 排名分"}
                      </p>
                      <p className="mt-0.5 font-mono text-lg font-semibold text-slate-950">
                        {scoreText(primaryScore)}
                      </p>
                      <ChevronDown
                        className={`ml-auto mt-1 h-4 w-4 text-slate-400 transition-transform ${
                          expanded ? "rotate-180" : ""
                        }`}
                      />
                    </div>
                  </div>

                  <div className="mt-3 rounded-xl bg-slate-950 px-3 py-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                        多路命中轨迹
                      </span>
                      <span className="font-mono text-[10px] text-slate-400">
                        RRF {scoreText(source.fusionScore, 4)}
                      </span>
                    </div>
                    {channels.length ? (
                      <div className="mt-2 grid gap-2 sm:grid-cols-3">
                        {channels.map(([name, detail]) => {
                          const meta = channelMeta(name);
                          const width = Math.max(18, 100 - (detail.rank - 1) * 8);
                          return (
                            <div key={name} className="min-w-0">
                              <div className="flex items-center justify-between text-[10px]">
                                <span className={meta.color}>{meta.label}</span>
                                <span className="font-mono text-slate-400">
                                  #{detail.rank}
                                </span>
                              </div>
                              <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/10">
                                <div
                                  className={`h-full rounded-full ${meta.rail}`}
                                  style={{ width: `${width}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : source.neighborOf?.length ? (
                      <p className="mt-1.5 text-[10px] text-emerald-300">
                        该证据由邻近补偿策略补充，关联命中分块 ×{source.neighborOf.length}
                      </p>
                    ) : (
                      <p className="mt-1.5 text-[10px] text-slate-500">
                        旧消息没有保存通道级得分。
                      </p>
                    )}
                  </div>
                </button>

                {expanded && (
                  <div className="border-t border-slate-100 px-4 pb-4 pt-3">
                    <div className="grid grid-cols-3 gap-2">
                      <Metric
                        icon={<Sparkles />}
                        label="精排"
                        value={scoreText(source.rerankScore)}
                      />
                      <Metric
                        icon={<BarChart3 />}
                        label="RRF 融合"
                        value={scoreText(source.fusionScore, 4)}
                      />
                      <Metric
                        icon={<Hash />}
                        label="分块"
                        value={`#${source.chunkIndex}`}
                      />
                    </div>

                    {!!source.neighborOf?.length && (
                      <div className="mt-2 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-[10px] text-emerald-700">
                        <GitBranch className="h-3 w-3 shrink-0" />
                        <span>
                          邻近补偿证据 · 由命中分块
                          {source.neighborOf
                            .map((pid) => {
                              const parent = sources.find((s) => s.chunkId === pid);
                              return parent ? ` #${parent.chunkIndex}` : "";
                            })
                            .filter(Boolean)
                            .join("、") || ` ×${source.neighborOf.length}`}
                          {' '}的相邻上下文补充
                        </span>
                      </div>
                    )}

                    {!!channels.length && (
                      <section className="mt-4">
                        <SectionTitle icon={<Search />} title="通道明细" />
                        <div className="mt-2 overflow-hidden rounded-xl border border-slate-200">
                          {channels.map(([name, detail], channelIndex) => {
                            const meta = channelMeta(name);
                            return (
                              <div
                                key={name}
                                className={`grid grid-cols-[1fr_auto_auto] items-center gap-3 px-3 py-2 text-xs ${
                                  channelIndex ? "border-t border-slate-100" : ""
                                }`}
                              >
                                <span className="flex items-center gap-2 font-medium text-slate-700">
                                  <span className={`h-2 w-2 rounded-full ${meta.rail}`} />
                                  {meta.label}
                                </span>
                                <span className="font-mono text-slate-500">
                                  原始 {scoreText(detail.rawScore)}
                                </span>
                                <span className="font-mono text-slate-500">
                                  贡献 {scoreText(detail.rrfContribution, 4)}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </section>
                    )}

                    <section className="mt-4">
                      <SectionTitle icon={<BookOpenText />} title="证据原文" />
                      <div className="mt-2 max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
                        {source.content}
                      </div>
                    </section>

                    {!!source.assets?.length && (
                      <section className="mt-4">
                        <SectionTitle icon={<ImageIcon />} title="关联图片" />
                        <div className="mt-2 grid grid-cols-2 gap-2">
                          {source.assets.map((asset, assetIndex) => {
                            const token = localStorage.getItem("token") || "";
                            const qs = token ? `?token=${encodeURIComponent(token)}` : "";
                            const url = asset.assetId
                              ? `/api/assets/${asset.assetId}${qs}`
                              : ((asset.storageUrl || asset.url) || "") + qs;
                            return url ? (
                              <a
                                key={asset.assetId || assetIndex}
                                href={url}
                                target="_blank"
                                rel="noreferrer"
                                className="group overflow-hidden rounded-xl border border-slate-200 bg-slate-50"
                              >
                                <img
                                  src={url}
                                  alt={asset.description || "来源图片"}
                                  className="h-28 w-full object-cover transition group-hover:scale-[1.02]"
                                />
                                <span className="flex items-center justify-between px-2.5 py-2 text-[10px] text-slate-500">
                                  查看原图
                                  <ExternalLink className="h-3 w-3" />
                                </span>
                              </a>
                            ) : null;
                          })}
                        </div>
                      </section>
                    )}

                    <div className="mt-4 grid gap-2 rounded-xl bg-slate-50 p-3 text-[10px] text-slate-500 sm:grid-cols-2">
                      <IdLine icon={<FileText />} label="文档 ID" value={source.documentId} />
                      <IdLine icon={<Hash />} label="分块 ID" value={source.chunkId} />
                    </div>
                  </div>
                )}
              </article>
            );
          })}

          {!sources.length && (
            <div className="flex h-64 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white text-center">
              <FileText className="h-8 w-8 text-slate-300" />
              <p className="mt-3 text-sm font-medium text-slate-700">没有可展示的来源</p>
              <p className="mt-1 text-xs text-slate-400">这条消息可能是闲聊或引导回复。</p>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

function SummaryStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="px-3 py-2.5 text-center">
      <p className="font-mono text-lg font-semibold text-white">{value}</p>
      <p className="text-[9px] uppercase tracking-[0.12em] text-slate-500">{label}</p>
    </div>
  );
}

function Pill({ icon, text, tone }: { icon: React.ReactElement; text: string; tone?: "default" | "emerald" }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[9px] font-medium ${
        tone === "emerald"
          ? "bg-emerald-100 text-emerald-700"
          : "bg-slate-100 text-slate-500"
      }`}
    >
      <span className="[&>svg]:h-2.5 [&>svg]:w-2.5">{icon}</span>
      {text}
    </span>
  );
}

function Metric({
  icon,
  label,
  value,
}: {
  icon: React.ReactElement;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-2.5">
      <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.12em] text-slate-400 [&>svg]:h-3 [&>svg]:w-3">
        {icon}
        {label}
      </div>
      <p className="mt-1.5 font-mono text-sm font-semibold text-slate-800">{value}</p>
    </div>
  );
}

function SectionTitle({ icon, title }: { icon: React.ReactElement; title: string }) {
  return (
    <h4 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500 [&>svg]:h-3.5 [&>svg]:w-3.5">
      {icon}
      {title}
    </h4>
  );
}

function IdLine({
  icon,
  label,
  value,
}: {
  icon: React.ReactElement;
  label: string;
  value?: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="text-slate-400 [&>svg]:h-3 [&>svg]:w-3">{icon}</span>
      <span className="shrink-0">{label}</span>
      <span className="truncate font-mono text-slate-700" title={value || "—"}>
        {value || "—"}
      </span>
    </div>
  );
}
