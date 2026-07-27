import { useState, useCallback } from "react";
import type { Message } from "@/types";
import MarkdownRenderer from "./MarkdownRenderer";
import ThinkingIndicator from "./ThinkingIndicator";
import { submitFeedback } from "@/services/feedbackService";
import { ArrowUpRight, ChevronDown, ChevronUp, Network, Orbit } from "lucide-react";

interface Props {
  message: Message;
  isStreaming?: boolean;
  progressMessage?: string | null;
  onViewSources?: (sources: NonNullable<Message["sources"]>) => void;
  onRecommendedQuestion?: (question: string) => void;
}

export default function MessageItem({
  message,
  isStreaming,
  progressMessage,
  onViewSources,
  onRecommendedQuestion,
}: Props) {
  const isUser = message.role === "user";
  const [feedbackVote, setFeedbackVote] = useState<number>(0); // 0=none, 1=up, -1=down
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [showFeedbackReasons, setShowFeedbackReasons] = useState(false);
  const [showMappings, setShowMappings] = useState(false);

  const handleFeedback = useCallback(async (vote: number, reason?: string) => {
    if (feedbackSubmitting || !message.id || message.id.startsWith("asst_")) return;
    try {
      setFeedbackSubmitting(true);
      // Toggle if same vote
      const newVote = feedbackVote === vote ? 0 : vote;
      await submitFeedback({ message_id: message.id, vote: newVote, reason });
      setFeedbackVote(newVote);
      setShowFeedbackReasons(false);
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

        {/* Retrieval progress indicator (TTFT feedback) */}
        {!isUser && isStreaming && !message.content && !message.thinkingContent && progressMessage && (
          <div className="flex items-center gap-2 py-1">
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
            </span>
            <span className="text-sm text-gray-500 animate-pulse">{progressMessage}</span>
          </div>
        )}

        {/* Default loading dots when no progress message */}
        {!isUser && isStreaming && !message.content && !message.thinkingContent && !progressMessage && (
          <div className="flex items-center gap-1 py-1">
            <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
            <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
            <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
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
            sourceCount={message.sources?.length || 0}
            onSourceClick={() => message.sources && onViewSources?.(message.sources)}
          />
        )}

        {!isUser && (message.ragModes?.agenticRag || message.ragModes?.graphRag) && (
          <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-200/70 pt-2">
            {message.ragModes.agenticRag && (
              <span
                title={message.agentSteps?.map((step) => step.tool).join(" → ")}
                className="inline-flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-800"
              >
                <Orbit className="h-3 w-3" />
                Agent {message.agentSteps?.length || 0} 步
              </span>
            )}
            {message.ragModes.graphRag && (
              <span
                title={
                  message.retrievalChannels?.graph?.error
                    ? String(message.retrievalChannels.graph.error)
                    : "Graph RAG 已参与本次召回"
                }
                className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] font-medium ${
                  message.retrievalChannels?.graph?.status === "error" ||
                  message.retrievalChannels?.graph?.status === "timeout"
                    ? "border-rose-200 bg-rose-50 text-rose-700"
                    : "border-cyan-200 bg-cyan-50 text-cyan-800"
                }`}
              >
                <Network className="h-3 w-3" />
                Graph {Number(message.retrievalChannels?.graph?.count || 0)} 条证据
              </span>
            )}
          </div>
        )}

        {/* Applied term mappings */}
        {!isUser && message.appliedMappings && message.appliedMappings.length > 0 && (
          <div className={`mt-2 ${(message.ragModes?.agenticRag || message.ragModes?.graphRag) ? "" : "border-t border-slate-200/70 pt-2"}`}>
            {message.appliedMappings.length === 1 ? (
              <span className="inline-flex items-center gap-1.5 rounded-md border border-violet-200 bg-violet-50 px-2 py-1 text-[10px] font-medium text-violet-800">
                <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M7 7h10M7 11h4m-4 4h8m-8 4h6" />
                </svg>
                已映射: {message.appliedMappings[0].source} → {message.appliedMappings[0].target}
              </span>
            ) : (
              <div>
                <button
                  onClick={() => setShowMappings(!showMappings)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-violet-200 bg-violet-50 px-2 py-1 text-[10px] font-medium text-violet-800 hover:bg-violet-100 transition-colors"
                >
                  <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M7 7h10M7 11h4m-4 4h8m-8 4h6" />
                  </svg>
                  已映射 {message.appliedMappings.length} 项
                  {showMappings ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                </button>
                {showMappings && (
                  <div className="mt-1.5 space-y-0.5">
                    {message.appliedMappings.map((m, i) => (
                      <div key={i} className="text-[10px] text-violet-700 pl-5">
                        {m.source} → {m.target}
                        <span className="ml-1.5 text-violet-400">({m.type})</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
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

        {!isUser && !isStreaming && !!message.recommendedQuestions?.length && (
          <div className="mt-3 space-y-1.5 border-t border-slate-200/70 pt-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
              接着了解
            </p>
            <div className="flex flex-wrap gap-1.5">
              {message.recommendedQuestions.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => onRecommendedQuestion?.(question)}
                  className="group inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-left text-xs text-slate-600 shadow-sm transition hover:border-cyan-300 hover:text-cyan-800"
                >
                  {question}
                  <ArrowUpRight className="h-3 w-3 opacity-40 transition group-hover:opacity-100" />
                </button>
              ))}
            </div>
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
              onClick={() => setShowFeedbackReasons((value) => !value)}
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
            {showFeedbackReasons && (
              <div className="ml-1 flex flex-wrap gap-1">
                {["检索不相关", "答案不准确", "引用不足", "表达不清"].map((reason) => (
                  <button
                    key={reason}
                    type="button"
                    onClick={() => handleFeedback(-1, reason)}
                    className="rounded-full border border-rose-200 bg-white px-2 py-1 text-[10px] text-rose-700 hover:bg-rose-50"
                  >
                    {reason}
                  </button>
                ))}
              </div>
            )}
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
