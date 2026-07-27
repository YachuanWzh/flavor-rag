import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, FileText, Loader2, X } from "lucide-react";
import * as pdfjsLib from "pdfjs-dist";

// Configure worker — use CDN matching installed version
pdfjsLib.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjsLib.version}/build/pdf.worker.min.mjs`;

interface Props {
  open: boolean;
  documentId: string;
  docName: string;
  fileType?: string;
  pageStart?: number | null;
  bboxes?: Array<Record<string, number>>;
  /** Source chunk content to highlight and scroll to */
  sourceContent?: string;
  onClose: () => void;
}

type LoadState = "loading" | "ready" | "error";

export default function DocumentPreviewModal({
  open,
  documentId,
  docName,
  fileType,
  pageStart,
  bboxes,
  sourceContent,
  onClose,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const pdfDocRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null);
  const renderTaskRef = useRef<pdfjsLib.RenderTask | null>(null);
  const highlightRef = useRef<HTMLSpanElement>(null);

  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [errorMsg, setErrorMsg] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [pageDims, setPageDims] = useState<{ width: number; height: number } | null>(null);

  const isPdf = (fileType || "").toLowerCase() === "pdf";

  const buildUrl = useCallback(() => {
    const token = localStorage.getItem("token") || "";
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";
    return `/api/knowledge-base/docs/${documentId}/preview${qs}`;
  }, [documentId]);

  // Load document when modal opens
  useEffect(() => {
    if (!open || !documentId) return;

    setLoadState("loading");
    setErrorMsg("");
    setTextContent(null);
    setPageDims(null);
    pdfDocRef.current = null;

    const targetPage = pageStart && pageStart >= 1 ? pageStart : 1;
    setCurrentPage(targetPage);

    if (!isPdf) {
      fetch(buildUrl())
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.text();
        })
        .then((text) => {
          setTextContent(text);
          setLoadState("ready");
        })
        .catch((err) => {
          setErrorMsg(err?.message || "加载失败");
          setLoadState("error");
        });
      return;
    }

    const loadingTask = pdfjsLib.getDocument({
      url: buildUrl(),
      withCredentials: false,
    });

    loadingTask.promise
      .then((pdf) => {
        pdfDocRef.current = pdf;
        setTotalPages(pdf.numPages);
        setLoadState("ready");
      })
      .catch((err) => {
        setErrorMsg(err?.message || "PDF 加载失败");
        setLoadState("error");
      });

    return () => {
      loadingTask.destroy();
    };
  }, [open, documentId, isPdf, buildUrl, pageStart]);

  // Render current page for PDF
  useEffect(() => {
    if (!open || !isPdf || loadState !== "ready" || !pdfDocRef.current) return;

    const pdf = pdfDocRef.current;
    const canvas = canvasRef.current;
    if (!canvas) return;

    if (renderTaskRef.current) {
      renderTaskRef.current.cancel();
      renderTaskRef.current = null;
    }

    let cancelled = false;

    pdf.getPage(currentPage).then((page) => {
      if (cancelled) return;

      const containerWidth = containerRef.current?.clientWidth || 700;
      const unscaledVp = page.getViewport({ scale: 1 });
      setPageDims({ width: unscaledVp.width, height: unscaledVp.height });

      const scale = Math.min(2.5, (containerWidth - 32) / unscaledVp.width);
      const viewport = page.getViewport({ scale });

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      canvas.width = viewport.width;
      canvas.height = viewport.height;

      const task = page.render({ canvas, canvasContext: ctx, viewport });
      renderTaskRef.current = task;

      task.promise.catch(() => {});
    });

    return () => {
      cancelled = true;
    };
  }, [open, isPdf, loadState, currentPage]);

  // Auto-scroll to highlighted text for non-PDF
  useEffect(() => {
    if (!open || isPdf || loadState !== "ready" || !textContent || !sourceContent) return;
    const timer = setTimeout(() => {
      highlightRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 200);
    return () => clearTimeout(timer);
  }, [open, isPdf, loadState, textContent, sourceContent]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  // Compute bbox overlays for current page
  const bboxOverlays =
    isPdf && bboxes && bboxes.length > 0 && pageDims
      ? bboxes.filter((b) => {
          const bboxPage = b.page ?? b.page_no;
          return bboxPage == null || Number(bboxPage) === currentPage;
        })
      : [];

  // For non-PDF: find source content in text and split for highlighting
  let textParts: { before: string; match: string; after: string } | null = null;
  if (!isPdf && textContent && sourceContent) {
    const needle = sourceContent.trim();
    if (needle.length >= 10) {
      let idx = textContent.indexOf(needle);
      if (idx === -1 && needle.length > 150) {
        idx = textContent.indexOf(needle.slice(0, 150));
      }
      if (idx === -1 && needle.length > 150) {
        idx = textContent.indexOf(needle.slice(-150));
      }
      if (idx !== -1) {
        const matchLen = idx + needle.length <= textContent.length
          ? needle.length
          : textContent.length - idx;
        textParts = {
          before: textContent.slice(0, idx),
          match: textContent.slice(idx, idx + matchLen),
          after: textContent.slice(idx + matchLen),
        };
      }
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <button
        type="button"
        aria-label="关闭预览"
        className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative z-10 flex h-[85vh] w-[90vw] max-w-4xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
        <header className="flex shrink-0 items-center justify-between border-b border-slate-700/60 px-5 py-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <FileText className="h-4 w-4 shrink-0 text-cyan-400" />
            <h3 className="truncate text-sm font-semibold text-white">
              {docName || "文档预览"}
            </h3>
            {isPdf && totalPages > 0 && (
              <span className="shrink-0 rounded-full bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400">
                {currentPage} / {totalPages}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-400 transition hover:bg-slate-700 hover:text-white"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div ref={containerRef} className="relative flex-1 overflow-auto bg-slate-800/50 p-4">
          {loadState === "loading" && (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-400">
              <Loader2 className="h-8 w-8 animate-spin text-cyan-400" />
              <p className="text-sm">正在加载文档…</p>
            </div>
          )}

          {loadState === "error" && (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-400">
              <FileText className="h-8 w-8 text-slate-600" />
              <p className="text-sm font-medium text-slate-300">原文暂不可用</p>
              <p className="text-xs text-slate-500">{errorMsg}</p>
            </div>
          )}

          {loadState === "ready" && isPdf && (
            <div className="relative mx-auto w-fit">
              <canvas ref={canvasRef} className="rounded-lg shadow-lg" />
              {bboxOverlays.map((b, i) => {
                if (!pageDims) return null;
                const x0 = Number(b.x0 ?? b.x ?? 0);
                const top = Number(b.top ?? b.y ?? 0);
                const x1 = Number(b.x1 ?? (b.x0 ?? 0) + (b.width ?? 0));
                const bottom = Number(b.bottom ?? (b.top ?? 0) + (b.height ?? 0));
                const left = (x0 / (pageDims.width || 1)) * 100;
                const topPct = (top / (pageDims.height || 1)) * 100;
                const width = ((x1 - x0) / (pageDims.width || 1)) * 100;
                const height = ((bottom - top) / (pageDims.height || 1)) * 100;
                if (width <= 0 || height <= 0) return null;
                return (
                  <div
                    key={i}
                    className="pointer-events-none absolute rounded border-2 border-cyan-400/80 bg-cyan-400/20 transition-all animate-pulse"
                    style={{
                      left: `${left}%`,
                      top: `${topPct}%`,
                      width: `${width}%`,
                      height: `${height}%`,
                    }}
                  />
                );
              })}
            </div>
          )}

          {loadState === "ready" && !isPdf && textContent != null && (
            <div className="mx-auto max-w-3xl whitespace-pre-wrap rounded-xl bg-slate-900 p-6 font-mono text-xs leading-6 text-slate-300">
              {textParts ? (
                <>
                  {textParts.before}
                  <span
                    ref={highlightRef}
                    className="rounded bg-cyan-500/20 px-0.5 text-cyan-200 ring-1 ring-cyan-400/50"
                  >
                    {textParts.match}
                  </span>
                  {textParts.after}
                </>
              ) : (
                textContent
              )}
            </div>
          )}
        </div>

        {isPdf && loadState === "ready" && totalPages > 1 && (
          <footer className="flex shrink-0 items-center justify-center gap-4 border-t border-slate-700/60 px-5 py-2.5">
            <button
              type="button"
              disabled={currentPage <= 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              上一页
            </button>
            <span className="text-xs text-slate-400">
              第 {currentPage} 页 / 共 {totalPages} 页
            </span>
            <button
              type="button"
              disabled={currentPage >= totalPages}
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              下一页
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </footer>
        )}
      </div>
    </div>
  );
}
