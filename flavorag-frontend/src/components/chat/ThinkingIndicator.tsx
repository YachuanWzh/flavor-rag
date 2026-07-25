import { useState } from "react";

interface Props {
  /** The accumulated thinking text (may grow during streaming). */
  content: string;
  /** Whether the model is still thinking. */
  isThinking: boolean;
}

/** Animated thinking indicator that shows real-time reasoning progress. */
export default function ThinkingIndicator({ content, isThinking }: Props) {
  const [expanded, setExpanded] = useState(true);

  if (!content && !isThinking) return null;

  return (
    <div className="mb-2 rounded-lg overflow-hidden border border-purple-200 bg-purple-50/50">
      {/* Header toggle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-purple-700 hover:bg-purple-100/50 transition-colors"
      >
        <span className="flex items-center gap-1.5 font-medium">
          {isThinking ? (
            <>
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-purple-500" />
              </span>
              正在思考...
            </>
          ) : (
            <>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
              思考过程
              <span className="text-purple-400 font-normal">
                ({content.length} 字)
              </span>
            </>
          )}
        </span>
        <svg
          className={`w-3 h-3 transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Content (collapsible) */}
      {expanded && content && (
        <div className="px-3 pb-2">
          <p className="text-xs text-gray-600 whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto font-mono">
            {content}
            {isThinking && <span className="inline-block w-1.5 h-4 bg-purple-500 animate-pulse ml-0.5 align-middle" />}
          </p>
        </div>
      )}
    </div>
  );
}
