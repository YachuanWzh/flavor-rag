import { useState } from "react";
import { X, ChevronDown, ChevronUp, FileText, Hash, Tag, BarChart3 } from "lucide-react";
import type { SourceRef } from "@/types";

interface Props {
  open: boolean;
  sources: SourceRef[];
  onClose: () => void;
}

export default function SourcesDrawer({ open, sources, onClose }: Props) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  const toggleExpand = (idx: number) => {
    setExpandedIndex(expandedIndex === idx ? null : idx);
  };

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/20 z-40 transition-opacity"
          onClick={onClose}
        />
      )}

      {/* Drawer */}
      <div
        className={`fixed top-0 right-0 h-full w-[420px] max-w-[90vw] bg-white shadow-2xl z-50 transform transition-transform duration-300 ease-in-out flex flex-col ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b shrink-0">
          <div>
            <h2 className="text-base font-semibold text-gray-900">引用来源</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              共 {sources.length} 条检索结果
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
          >
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </div>

        {/* Source Cards */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {sources.map((source, idx) => {
            const isExpanded = expandedIndex === idx;
            const scorePercent = ((source.score || 0) * 100).toFixed(0);

            return (
              <div
                key={source.chunkId || idx}
                className="border border-gray-200 rounded-xl overflow-hidden transition-shadow hover:shadow-md"
              >
                {/* Card Header */}
                <button
                  onClick={() => toggleExpand(idx)}
                  className="w-full text-left px-4 py-3 bg-gray-50 hover:bg-gray-100 transition-colors flex items-start justify-between gap-3"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-100 text-blue-700 text-xs font-bold shrink-0">
                        {idx + 1}
                      </span>
                      <span className="text-sm font-medium text-gray-900 truncate">
                        {source.docName || "未知文档"}
                      </span>
                      <span className="text-xs text-gray-400">
                        #{source.chunkIndex}
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 line-clamp-2 leading-relaxed">
                      {source.content}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="inline-flex items-center gap-1 text-xs font-mono text-green-700 bg-green-50 rounded-full px-2 py-0.5">
                      <BarChart3 className="w-3 h-3" />
                      {scorePercent}%
                    </span>
                    {isExpanded ? (
                      <ChevronUp className="w-4 h-4 text-gray-400" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-gray-400" />
                    )}
                  </div>
                </button>

                {/* Expanded Detail */}
                {isExpanded && (
                  <div className="px-4 py-3 space-y-3 border-t border-gray-100 bg-white">
                    {/* Metadata grid */}
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                      <DetailItem
                        icon={<FileText className="w-3.5 h-3.5" />}
                        label="文档ID"
                        value={source.documentId || "-"}
                      />
                      <DetailItem
                        icon={<Hash className="w-3.5 h-3.5" />}
                        label="分块ID"
                        value={source.chunkId || "-"}
                      />
                      <DetailItem
                        icon={<FileText className="w-3.5 h-3.5" />}
                        label="文档名称"
                        value={source.docName || "-"}
                      />
                      <DetailItem
                        icon={<Tag className="w-3.5 h-3.5" />}
                        label="分块序号"
                        value={String(source.chunkIndex)}
                      />
                      <DetailItem
                        icon={<BarChart3 className="w-3.5 h-3.5" />}
                        label="相关度"
                        value={`${scorePercent}%`}
                      />
                    </div>

                    {/* Full content */}
                    <div>
                      <h4 className="text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">
                        完整内容
                      </h4>
                      <div className="bg-gray-50 rounded-lg p-3 text-xs text-gray-800 leading-relaxed whitespace-pre-wrap max-h-48 overflow-y-auto border border-gray-100">
                        {source.content}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {sources.length === 0 && (
            <div className="flex items-center justify-center py-12 text-sm text-gray-400">
              暂无来源信息
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t shrink-0">
          <button
            onClick={onClose}
            className="w-full py-2 text-sm text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
          >
            关闭
          </button>
        </div>
      </div>
    </>
  );
}

function DetailItem({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-start gap-1.5 min-w-0">
      <span className="text-gray-400 mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-[10px] text-gray-400 uppercase">{label}</p>
        <p className="text-xs text-gray-800 font-mono truncate" title={value}>
          {value}
        </p>
      </div>
    </div>
  );
}
