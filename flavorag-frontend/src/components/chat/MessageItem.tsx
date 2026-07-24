import { useState } from "react";
import type { Message } from "@/types";
import MarkdownRenderer from "./MarkdownRenderer";

interface Props {
  message: Message;
  isStreaming?: boolean;
  onViewSources?: (sources: NonNullable<Message["sources"]>) => void;
}

export default function MessageItem({ message, isStreaming, onViewSources }: Props) {
  const isUser = message.role === "user";
  const [showThinking, setShowThinking] = useState(false);

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-xl px-4 py-3 ${
          isUser
            ? "bg-blue-600 text-white"
            : "bg-gray-100 text-gray-900"
        }`}
      >
        {/* Thinking indicator */}
        {message.thinkingContent && (
          <div className="mb-2">
            <button
              onClick={() => setShowThinking(!showThinking)}
              className="text-xs underline opacity-70 hover:opacity-100"
            >
              {showThinking ? "收起思考" : "查看思考过程"}
            </button>
            {showThinking && (
              <p className="mt-1 text-xs opacity-70 whitespace-pre-wrap border-l-2 pl-2">
                {message.thinkingContent}
              </p>
            )}
          </div>
        )}

        {/* Content */}
        {isUser ? (
          <div className="text-sm whitespace-pre-wrap leading-relaxed">
            {message.content}
          </div>
        ) : (
          <MarkdownRenderer
            content={message.content}
            isStreaming={isStreaming}
          />
        )}

        {/* Sources button */}
        {message.sources && message.sources.length > 0 && (
          <div className="mt-3 pt-2 border-t border-gray-200/50">
            <button
              onClick={() => onViewSources?.(message.sources!)}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-blue-600 hover:text-blue-800 bg-blue-50 hover:bg-blue-100 rounded-lg px-3 py-1.5 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              查看来源 ({message.sources.length})
            </button>
          </div>
        )}

        {/* Interrupted indicator */}
        {message.messageStatus === "INTERRUPTED" && (
          <div className="mt-1 text-xs italic opacity-50">已中断</div>
        )}
      </div>
    </div>
  );
}
