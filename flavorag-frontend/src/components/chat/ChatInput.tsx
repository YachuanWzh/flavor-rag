import { useState } from "react";

interface Props {
  onSend: (text: string) => void;
  onCancel?: () => void;
  isStreaming: boolean;
  deepThinking: boolean;
  onDeepThinkingChange: (enabled: boolean) => void;
}

export default function ChatInput({
  onSend, onCancel, isStreaming,
  deepThinking, onDeepThinkingChange,
}: Props) {
  const [text, setText] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || isStreaming) return;
    onSend(text);
    setText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t p-3 bg-white"
    >
      <div className="flex gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入问题..."
          rows={1}
          disabled={isStreaming}
          className="flex-1 resize-none border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50"
        />
        {isStreaming ? (
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 bg-red-500 text-white rounded-lg text-sm hover:bg-red-600"
          >
            停止
          </button>
        ) : (
          <button
            type="submit"
            disabled={!text.trim()}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            发送
          </button>
        )}
      </div>

      {/* Deep thinking toggle */}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => onDeepThinkingChange(!deepThinking)}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
            deepThinking
              ? "bg-purple-100 text-purple-700 border border-purple-300"
              : "bg-gray-50 text-gray-500 border border-gray-200 hover:bg-gray-100"
          }`}
        >
          <svg
            className={`w-3.5 h-3.5 ${deepThinking ? "animate-pulse" : ""}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
          深度思考
        </button>
        {deepThinking && (
          <span className="text-xs text-purple-500">
            AI 将展示推理过程
          </span>
        )}
      </div>
    </form>
  );
}
