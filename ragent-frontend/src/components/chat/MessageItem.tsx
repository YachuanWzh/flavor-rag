import { useState } from "react";
import type { Message, SourceRef } from "@/types";
import { useChatStore } from "@/stores/chatStore";

interface Props {
  message: Message;
}

export default function MessageItem({ message }: Props) {
  const isUser = message.role === "user";
  const [showThinking, setShowThinking] = useState(false);
  const openedSourceId = useChatStore((s) => s.openedSourceMessageId);
  const toggleSources = useChatStore((s) => s.toggleSourcesPanel);
  const sourcesOpen = openedSourceId === message.id;

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
        <div className="text-sm whitespace-pre-wrap leading-relaxed">
          {message.content || (
            <span className="italic opacity-50">思考中...</span>
          )}
        </div>

        {/* Sources */}
        {message.sources && message.sources.length > 0 && (
          <div className="mt-2 pt-2 border-t border-gray-200/50">
            <button
              onClick={() => toggleSources(message.id)}
              className="text-xs underline opacity-70 hover:opacity-100"
            >
              {sourcesOpen ? "收起来源" : `查看来源 (${message.sources.length})`}
            </button>
            {sourcesOpen && (
              <div className="mt-1 space-y-1">
                {message.sources.map((s, i) => (
                  <div key={i} className="text-xs opacity-70 bg-gray-50 rounded p-1">
                    <span className="font-medium">[{i + 1}]</span> {s.content?.substring(0, 120)}
                    {s.score !== undefined && (
                      <span className="ml-1 text-gray-400">({(s.score * 100).toFixed(0)}%)</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
