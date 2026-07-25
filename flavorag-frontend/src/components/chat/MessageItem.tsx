import { useState, useCallback } from "react";
import type { Message } from "@/types";
import MarkdownRenderer from "./MarkdownRenderer";
import ThinkingIndicator from "./ThinkingIndicator";
import { submitFeedback } from "@/services/feedbackService";

interface Props {
  message: Message;
  isStreaming?: boolean;
  onViewSources?: (sources: NonNullable<Message["sources"]>) => void;
}

export default function MessageItem({ message, isStreaming, onViewSources }: Props) {
  const isUser = message.role === "user";
  const [feedbackVote, setFeedbackVote] = useState<number>(0); // 0=none, 1=up, -1=down
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);

  const handleFeedback = useCallback(async (vote: number) => {
    if (feedbackSubmitting || !message.id || message.id.startsWith("asst_")) return;
    try {
      setFeedbackSubmitting(true);
      // Toggle if same vote
      const newVote = feedbackVote === vote ? 0 : vote;
      await submitFeedback({ message_id: message.id, vote: newVote });
      setFeedbackVote(newVote);
    } catch {
      // Silently fail for feedback
    } finally {
      setFeedbackSubmitting(false);
    }
  }, [feedbackSubmitting, feedbackVote, message.id]);

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
          <ThinkingIndicator
            content={message.thinkingContent}
            isThinking={!!isStreaming && !message.content}
          />
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

        {/* Feedback buttons (only for assistant messages) */}
        {!isUser && !isStreaming && message.content && (
          <div className="mt-2 flex items-center gap-1">
            <button
              onClick={() => handleFeedback(1)}
              disabled={feedbackSubmitting}
              title="回答有帮助"
              className={`p-1 rounded transition-colors ${
                feedbackVote === 1
                  ? "text-green-600 bg-green-50"
                  : "text-gray-400 hover:text-green-600 hover:bg-green-50"
              }`}
            >
              <svg className="w-4 h-4" fill={feedbackVote === 1 ? "currentColor" : "none"} viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5" />
              </svg>
            </button>
            <button
              onClick={() => handleFeedback(-1)}
              disabled={feedbackSubmitting}
              title="回答不够好"
              className={`p-1 rounded transition-colors ${
                feedbackVote === -1
                  ? "text-red-600 bg-red-50"
                  : "text-gray-400 hover:text-red-600 hover:bg-red-50"
              }`}
            >
              <svg className="w-4 h-4" fill={feedbackVote === -1 ? "currentColor" : "none"} viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018a2 2 0 01.485.06l3.76.94m-7 10v5a2 2 0 002 2h.096c.5 0 .905-.405.905-.904 0-.715.211-1.413.608-2.008L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5" />
              </svg>
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
