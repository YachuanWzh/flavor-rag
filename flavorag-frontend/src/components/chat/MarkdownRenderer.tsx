import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface Props {
  content: string;
  isStreaming?: boolean;
  className?: string;
  sourceCount?: number;
  onSourceClick?: (index: number) => void;
}

/**
 * During streaming, the only truly destructive case is an unclosed fenced
 * code block (```), which would swallow all subsequent content as code.
 *
 * Incomplete inline tokens like **, *, _, `, ~~ are deliberately left
 * alone — they may render as stray literal characters momentarily, but
 * that is far better than silently stripping real content.
 */
function safeForStreaming(input: string): string {
  if (!input) return input;

  const fenceMatches = input.match(/```/g);
  if (fenceMatches && fenceMatches.length % 2 !== 0) {
    const lastIdx = input.lastIndexOf("```");
    const before = lastIdx > 0 ? input[lastIdx - 1] : "\n";
    if (before === "\n" || lastIdx === 0) {
      // Strip the unclosed fence; it will reappear once the block completes
      return input.slice(0, lastIdx);
    }
  }

  // Pass through everything else — no stripping, no escaping
  return input;
}

export default function MarkdownRenderer({
  content,
  isStreaming,
  className,
  sourceCount = 0,
  onSourceClick,
}: Props) {
  const streamedContent = isStreaming ? safeForStreaming(content || "") : content;
  const safeContent = sourceCount
    ? streamedContent.replace(
        /\[(\d+)\](?:\([^)]*\))?/g,
        (whole, value) => Number(value) <= sourceCount ? `[${value}](source:${value})` : whole,
      )
    : streamedContent;

  if (!content && !isStreaming) return null;

  return (
    <div className={cn("text-sm leading-relaxed", className)}>
      <div>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            pre({ children, ...props }) {
              return (
                <pre
                  {...props}
                  className="bg-gray-800 text-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs leading-relaxed"
                >
                  {children}
                </pre>
              );
            },
            code({ className: codeClass, children, ...props }) {
              const isInline = !codeClass;
              return isInline ? (
                <code
                  {...props}
                  className="bg-gray-200 text-red-600 rounded px-1 py-0.5 text-xs font-mono"
                >
                  {children}
                </code>
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
            img({ src, alt }) {
              // Auto-append token for proxy asset URLs so <img> can authenticate
              let finalSrc = src;
              if (typeof src === "string" && src.startsWith("/api/assets/") && !src.includes("token=")) {
                const token = localStorage.getItem("token") || "";
                if (token) {
                  finalSrc = src + (src.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
                }
              }
              return (
                <img
                  src={finalSrc}
                  alt={alt}
                  className="max-w-full rounded-lg my-2 border border-slate-200"
                  loading="lazy"
                />
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
              if (href?.startsWith("source:")) {
                const index = Number(href.slice("source:".length)) - 1;
                return (
                  <button
                    type="button"
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); onSourceClick?.(index); }}
                    className="mx-0.5 inline-flex min-w-5 items-center justify-center rounded bg-cyan-50 px-1 font-mono text-[10px] font-semibold text-cyan-700 ring-1 ring-inset ring-cyan-200 hover:bg-cyan-100"
                    title={`查看来源 ${index + 1}`}
                  >
                    {children}
                  </button>
                );
              }
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
    </div>
  );
}
