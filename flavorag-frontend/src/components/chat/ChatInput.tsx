import { useState } from "react";
import {
  Layers,
  Network,
  Orbit,
  Send,
  Sparkles,
  Square,
} from "lucide-react";

interface Props {
  onSend: (text: string) => void;
  onCancel?: () => void;
  isStreaming: boolean;
  deepThinking: boolean;
  onDeepThinkingChange: (enabled: boolean) => void;
  agenticRag: boolean;
  onAgenticRagChange: (enabled: boolean) => void;
  graphRag: boolean;
  onGraphRagChange: (enabled: boolean) => void;
  neighborExpansion: boolean;
  onNeighborExpansionChange: (enabled: boolean) => void;
  hyde: boolean;
  onHydeChange: (enabled: boolean) => void;
}

interface ModeToggleProps {
  active: boolean;
  label: string;
  description: string;
  icon: React.ReactNode;
  tone: "violet" | "amber" | "cyan" | "emerald" | "rose";
  onClick: () => void;
}

const toneClasses = {
  violet: {
    active: "border-violet-300 bg-violet-50 text-violet-700 shadow-[0_0_0_1px_rgba(139,92,246,0.05)]",
    dot: "bg-violet-500",
  },
  amber: {
    active: "border-amber-300 bg-amber-50 text-amber-800 shadow-[0_0_0_1px_rgba(245,158,11,0.05)]",
    dot: "bg-amber-500",
  },
  cyan: {
    active: "border-cyan-300 bg-cyan-50 text-cyan-800 shadow-[0_0_0_1px_rgba(6,182,212,0.05)]",
    dot: "bg-cyan-500",
  },
  emerald: {
    active: "border-emerald-300 bg-emerald-50 text-emerald-800 shadow-[0_0_0_1px_rgba(16,185,129,0.05)]",
    dot: "bg-emerald-500",
  },
  rose: {
    active: "border-rose-300 bg-rose-50 text-rose-800 shadow-[0_0_0_1px_rgba(244,63,94,0.05)]",
    dot: "bg-rose-500",
  },
};

function ModeToggle({
  active,
  label,
  description,
  icon,
  tone,
  onClick,
}: ModeToggleProps) {
  const palette = toneClasses[tone];
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={description}
      className={`group inline-flex min-h-8 items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 ${
        active
          ? palette.active
          : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-800"
      }`}
    >
      <span className={active ? "opacity-100" : "opacity-60"}>{icon}</span>
      <span>{label}</span>
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full transition ${
          active ? palette.dot : "bg-slate-300"
        }`}
      />
    </button>
  );
}

export default function ChatInput({
  onSend,
  onCancel,
  isStreaming,
  deepThinking,
  onDeepThinkingChange,
  agenticRag,
  onAgenticRagChange,
  graphRag,
  onGraphRagChange,
  neighborExpansion,
  onNeighborExpansionChange,
  hyde,
  onHydeChange,
}: Props) {
  const [text, setText] = useState("");

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!text.trim() || isStreaming) return;
    onSend(text);
    setText("");
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSubmit(event);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t border-slate-200 bg-white px-3 py-3 sm:px-5"
    >
      <div className="mx-auto max-w-3xl rounded-2xl border border-slate-200 bg-white p-2 shadow-[0_12px_34px_rgba(15,23,42,0.08)] transition focus-within:border-slate-300 focus-within:shadow-[0_16px_42px_rgba(15,23,42,0.11)]">
        <div className="flex items-end gap-2">
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="向知识库提问…"
            rows={2}
            disabled={isStreaming}
            className="min-h-[52px] flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm leading-6 text-slate-900 outline-none placeholder:text-slate-400 disabled:text-slate-400"
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={onCancel}
              aria-label="停止生成"
              className="mb-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white transition hover:bg-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
            >
              <Square className="h-4 w-4 fill-current" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!text.trim()}
              aria-label="发送问题"
              className="mb-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white transition hover:bg-cyan-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-100 px-1 pt-2">
          <ModeToggle
            active={agenticRag}
            label="Agentic RAG"
            description="让 Agent 在步数预算内规划检索与只读工具调用"
            icon={<Orbit className="h-3.5 w-3.5" />}
            tone="amber"
            onClick={() => onAgenticRagChange(!agenticRag)}
          />
          <ModeToggle
            active={graphRag}
            label="Graph RAG"
            description="把知识图谱证据并入召回，并打开关系图"
            icon={<Network className="h-3.5 w-3.5" />}
            tone="cyan"
            onClick={() => onGraphRagChange(!graphRag)}
          />
          {/* 深度思考按钮已隐藏，默认开启 */}
          <ModeToggle
            active={neighborExpansion}
            label="近邻补偿"
            description="召回每个chunk前后各两段作为上下文补偿"
            icon={<Layers className="h-3.5 w-3.5" />}
            tone="emerald"
            onClick={() => onNeighborExpansionChange(!neighborExpansion)}
          />
          <ModeToggle
            active={hyde}
            label="HyDE"
            description="生成假设性答案文档辅助向量检索，提升召回率"
            icon={<Sparkles className="h-3.5 w-3.5" />}
            tone="rose"
            onClick={() => onHydeChange(!hyde)}
          />
          <span className="ml-auto hidden text-[11px] text-slate-400 sm:inline">
            Enter 发送 · Shift + Enter 换行
          </span>
        </div>
      </div>
    </form>
  );
}
