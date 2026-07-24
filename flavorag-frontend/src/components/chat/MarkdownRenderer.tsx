import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface Props {
  content: string;
  isStreaming?: boolean;
  className?: string;
}

/**
 * Escape trailing incomplete markdown tokens so the streaming content
 * always renders correctly without swallowing characters.
 * Unclosed tokens like **, *, _, `, ~~, [, etc. at the tail are escaped.
 */
function sanitizeStreamingMarkdown(input: string): string {
  if (!input) return input;

  // ---- Handle unclosed fenced code blocks (```) ----
  const fenceMatches = input.match(/```/g);
  if (fenceMatches && fenceMatches.length % 2 !== 0) {
    const lastIdx = input.lastIndexOf("```");
    const before = lastIdx > 0 ? input[lastIdx - 1] : "\n";
    if (before === "\n" || lastIdx === 0) {
      return input.slice(0, lastIdx) + "\\`\\`\\`";
    }
  }

  // ---- Handle trailing inline tokens ----
  let trailLen = 0;
  const len = input.length;
  let i = len - 1;

  while (i >= 0) {
    const ch = input[i];
    if (ch === "*" || ch === "_" || ch === "`" || ch === "~" || ch === "[" || ch === "]") {
      trailLen++;
      i--;
      continue;
    }
    if (ch === "!" && i + 1 < len && input[i + 1] === "[") {
      trailLen = trailLen + 2;
      i -= 2;
      continue;
    }
    break;
  }

  if (trailLen === 0) return input;

  const trail = input.slice(len - trailLen);
  const rest = input.slice(0, len - trailLen);
  const escaped = trail.replace(/[*_`~\[\]!]/g, "\\$&");
  return rest + escaped;
}

export default function MarkdownRenderer({ content, isStreaming, className }: Props) {
  const rendered = useMemo(() => {
    if (!content && !isStreaming) return null;

    // Always render markdown — even during streaming.
    // Trailing incomplete syntax is escaped so characters aren't swallowed.
    const safeContent = isStreaming ? sanitizeStreamingMarkdown(content || "") : content;

    return (
      <div>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            pre({ children, ...props }) {
              return (
                <pre
                  {...props}
                  className="bg-gray-800 text-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs leading-relaxed"
                />
              );
            },
            code({ className: codeClass, children, ...props }) {
              const isInline = !codeClass;
              return isInline ? (
                <code
                  {...props}
                  className="bg-gray-200 text-red-600 rounded px-1 py-0.5 text-xs font-mono"
                />
              ) : (
                <code {...props} className={codeClass}>
                  {children}
                </code>
              );
            },
            table({ children }) {
              return (
                <div className="overflow-x-auto my-2">
                  <table className="min-w-full border-collapse border border-gray-300 text-xs">
                    {children}
                  </table>
                </div>
              );
            },
            th({ children }) {
              return (
                <th className="border border-gray-300 px-2 py-1 bg-gray-100 font-semibold text-left">
                  {children}
                </th>
              );
            },
            td({ children }) {
              return (
                <td className="border border-gray-300 px-2 py-1">{children}</td>
              );
            },
            blockquote({ children }) {
              return (
                <blockquote className="border-l-3 border-blue-400 pl-3 my-2 italic text-gray-600 text-xs">
                  {children}
                </blockquote>
              );
            },
            a({ children, href }) {
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 underline hover:text-blue-800"
                >
                  {children}
                </a>
              );
            },
            p({ children }) {
              return <p className="my-1 leading-relaxed">{children}</p>;
            },
            ul({ children }) {
              return <ul className="list-disc pl-5 my-1 space-y-0.5">{children}</ul>;
            },
            ol({ children }) {
              return <ol className="list-decimal pl-5 my-1 space-y-0.5">{children}</ol>;
            },
            li({ children }) {
              return <li className="text-sm">{children}</li>;
            },
            h1({ children }) {
              return <h1 className="text-lg font-bold mt-3 mb-1">{children}</h1>;
            },
            h2({ children }) {
              return <h2 className="text-base font-semibold mt-2 mb-1">{children}</h2>;
            },
            h3({ children }) {
              return <h3 className="text-sm font-semibold mt-2 mb-1">{children}</h3>;
            },
            hr() {
              return <hr className="my-2 border-gray-300" />;
            },
          }}
        >
          {safeContent}
        </ReactMarkdown>
        {isStreaming && (
          <span className="inline-block w-2 h-4 bg-blue-500 animate-pulse align-middle ml-0.5" />
        )}
      </div>
    );
  }, [content, isStreaming]);

  return (
    <div className={cn("text-sm leading-relaxed", className)}>
      {rendered}
    </div>
  );
}
