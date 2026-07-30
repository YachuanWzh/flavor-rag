import { useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft, Upload, Trash2, FileText, Eye, Loader2, Link as LinkIcon, Globe,
  Files, CheckCircle, XCircle, AlertCircle, RefreshCw, ClipboardPaste,
} from "lucide-react";
import {
  fetchDocuments,
  uploadDocument,
  pasteClipboardDocument,
  uploadDocumentFromUrl,
  batchUploadDocuments,
  deleteDocument,
  reindexIfChanged,
  type ChunkOptions,
} from "@/services/knowledgeService";
import type { KnowledgeDocument, BatchFileResult } from "@/types";

interface ClipboardImage {
  id: string;
  file?: File;
  sourceUrl?: string;
  sourceUrls?: string[];
  alt: string;
  previewUrl: string;
  previewFailed?: boolean;
}

function richImageSourceCandidates(image: HTMLImageElement): string[] {
  const rawCandidates = [
    image.getAttribute("data-src"),
    image.getAttribute("data-original"),
    image.getAttribute("data-original-src"),
    image.getAttribute("data-lazy-src"),
    image.getAttribute("data-url"),
    image.getAttribute("src"),
  ];
  for (const attribute of ["data-srcset", "srcset"]) {
    const srcset = image.getAttribute(attribute);
    if (srcset) {
      rawCandidates.push(
        ...srcset.split(",").map((item) => item.trim().split(/\s+/)[0]),
      );
    }
  }
  const normalized = rawCandidates
    .filter((value): value is string => Boolean(value))
    .map((value) => value.trim())
    .map((value) => value.startsWith("//") ? `https:${value}` : value)
    .filter((value) => /^(?:https?:|data:image\/|blob:)/i.test(value));
  const unique = Array.from(new Set(normalized));
  const priority = (value: string) => {
    if (/^data:image\//i.test(value)) return 0;
    if (/^https?:/i.test(value)) return 1;
    return 2;
  };
  return unique.sort((left, right) => priority(left) - priority(right));
}

function clipboardImageMarker(image: ClipboardImage): string {
  const safeAlt = image.alt.replace(/[\[\]\r\n]/g, " ").trim() || "粘贴图片";
  return `![${safeAlt}](clipboard-image://${image.id})`;
}

function renderRichClipboardText(
  document: Document,
  imageEntries: Map<HTMLImageElement, ClipboardImage>,
): string {
  const render = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) {
      return node.textContent || "";
    }
    if (!(node instanceof HTMLElement)) return "";
    if (
      ["STYLE", "SCRIPT", "NOSCRIPT", "TEMPLATE", "META", "LINK", "HEAD"].includes(node.tagName)
      || node.getAttribute("aria-hidden") === "true"
      || /(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)/i.test(
        node.getAttribute("style") || "",
      )
    ) {
      return "";
    }
    if (node.tagName === "IMG") {
      const entry = imageEntries.get(node as HTMLImageElement);
      return entry ? `\n\n${clipboardImageMarker(entry)}\n\n` : (node.getAttribute("alt") || "");
    }
    if (node.tagName === "BR") return "\n";
    if (node.tagName === "TABLE") {
      const rows = Array.from(node.querySelectorAll("tr"))
        .filter((row) => row.closest("table") === node)
        .map((row) => Array.from(row.children)
          .filter((cell) => ["TD", "TH"].includes(cell.tagName))
          .map((cell) => Array.from(cell.childNodes).map(render).join("")
            .replace(/\s+/g, " ")
            .replace(/\|/g, "\\|")
            .trim()));
      if (rows.length === 0) return "";
      const columnCount = Math.max(...rows.map((row) => row.length));
      const normalizedRows = rows.map((row) => [
        ...row,
        ...Array(Math.max(0, columnCount - row.length)).fill(""),
      ]);
      const markdownRows = normalizedRows.map((row) => `| ${row.join(" | ")} |`);
      const separator = `| ${Array(columnCount).fill("---").join(" | ")} |`;
      return `\n\n${markdownRows[0]}\n${separator}\n${markdownRows.slice(1).join("\n")}\n\n`;
    }

    const body = Array.from(node.childNodes).map(render).join("");
    if (/^H[1-6]$/.test(node.tagName)) {
      const level = Number(node.tagName.slice(1));
      return `\n\n${"#".repeat(level)} ${body.trim()}\n\n`;
    }
    if (node.tagName === "LI") return `\n- ${body.trim()}\n`;
    if (node.tagName === "PRE") return `\n\n\`\`\`\n${body.trim()}\n\`\`\`\n\n`;
    if (node.tagName === "BLOCKQUOTE") {
      return `\n\n${body.split("\n").map((line) => line ? `> ${line}` : line).join("\n")}\n\n`;
    }
    if (["P", "DIV", "SECTION", "ARTICLE", "FIGURE", "FIGCAPTION"].includes(node.tagName)) {
      return `\n${body}\n`;
    }
    return body;
  };

  return render(document.body)
    .replace(/\u00a0/g, " ")
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trimEnd())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export default function KnowledgeBaseDetailPage() {
  const { kbId } = useParams<{ kbId: string }>();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // Chunking config state
  const [chunkStrategy, setChunkStrategy] = useState("FIXED_WINDOW");
  const [chunkSize, setChunkSize] = useState(512);
  const [chunkOverlap, setChunkOverlap] = useState(128);

  // Upload mode: file / URL / batch / pasted Markdown
  const [uploadMode, setUploadMode] = useState<"file" | "url" | "batch" | "paste">("batch");
  const [urlInput, setUrlInput] = useState("");
  const [urlDocName, setUrlDocName] = useState("");
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [markdownContent, setMarkdownContent] = useState("");
  const [markdownDocName, setMarkdownDocName] = useState("");
  const [clipboardImages, setClipboardImages] = useState<ClipboardImage[]>([]);

  // Batch upload state
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [batchResults, setBatchResults] = useState<BatchFileResult[] | null>(null);
  const [batchSummary, setBatchSummary] = useState("");

  const loadDocs = async () => {
    if (!kbId) return;
    try {
      setLoading(true);
      const data = await fetchDocuments(kbId);
      setDocs(data);
    } catch {
      setError("加载文档列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadDocs(); }, [kbId]);

  // Poll while any document is still queued/running (async ingestion)
  useEffect(() => {
    const hasPending = docs.some((d) => d.status === "queued" || d.status === "running");
    if (!hasPending || !kbId) return;
    const timer = setInterval(async () => {
      try {
        const data = await fetchDocuments(kbId);
        setDocs(data);
      } catch {
        /* keep previous list on transient errors */
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [docs, kbId]);

  const handleUrlUpload = async () => {
    if (!kbId || !urlInput.trim()) return;
    try {
      setUploading(true);
      setError("");
      setSuccessMsg("");
      const result = await uploadDocumentFromUrl(kbId, {
        url: urlInput.trim(),
        docName: urlDocName.trim() || undefined,
        scheduleEnabled,
      });
      setSuccessMsg(`URL文档「${result.docName}」添加成功`);
      setUrlInput("");
      setUrlDocName("");
      await loadDocs();
    } catch (err: any) {
      setError(err?.message || "URL添加失败");
    } finally {
      setUploading(false);
    }
  };

  const handleClipboardContentPaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pastedFiles = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    const richHtml = event.clipboardData.getData("text/html");
    const htmlDocument = richHtml
      ? new DOMParser().parseFromString(richHtml, "text/html")
      : null;
    const htmlImages = htmlDocument
      ? Array.from(htmlDocument.querySelectorAll("img"))
          .map((image) => ({
            element: image,
            sourceUrls: richImageSourceCandidates(image),
            alt: image.getAttribute("alt") || image.getAttribute("title") || "",
          }))
      : [];
    if (
      pastedFiles.length === 0
      && !htmlImages.some((image) => image.sourceUrls.length > 0)
    ) return;

    event.preventDefault();
    const clipboardText = event.clipboardData.getData("text/plain");
    const fileEntries: ClipboardImage[] = pastedFiles.map((source, index) => {
      const id = `paste-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`;
      const subtype = source.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
      const file = new File([source], `${id}.${subtype}`, { type: source.type });
      return {
        id,
        file,
        alt: `粘贴图片 ${clipboardImages.length + index + 1}`,
        previewUrl: URL.createObjectURL(file),
      };
    });
    const positionedEntries = new Map<HTMLImageElement, ClipboardImage>();
    const entries: ClipboardImage[] = [];
    htmlImages.forEach((image, index) => {
      const fileEntry = fileEntries[index];
      if (fileEntry) {
        const entry = {
          ...fileEntry,
          alt: image.alt || fileEntry.alt,
        };
        positionedEntries.set(image.element, entry);
        entries.push(entry);
      } else if (image.sourceUrls.length > 0) {
        const entry: ClipboardImage = {
          id: `rich-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`,
          sourceUrl: image.sourceUrls[0],
          sourceUrls: image.sourceUrls,
          alt: image.alt || `富文本图片 ${clipboardImages.length + index + 1}`,
          previewUrl: image.sourceUrls[0],
        };
        positionedEntries.set(image.element, entry);
        entries.push(entry);
      }
    });
    const extraFileEntries = fileEntries.slice(htmlImages.length);
    entries.push(...extraFileEntries);

    const positionedText = htmlDocument && positionedEntries.size > 0
      ? renderRichClipboardText(htmlDocument, positionedEntries)
      : clipboardText;
    const extraImageMarkdown = extraFileEntries
      .map(clipboardImageMarker)
      .join("\n\n");
    const insertion = [positionedText, extraImageMarkdown].filter(Boolean).join("\n\n");
    const start = event.currentTarget.selectionStart;
    const end = event.currentTarget.selectionEnd;
    setMarkdownContent((previous) =>
      `${previous.slice(0, start)}${insertion}${previous.slice(end)}`,
    );
    setClipboardImages((previous) => [...previous, ...entries]);
  };

  const removeClipboardImage = (id: string) => {
    setClipboardImages((previous) => {
      const removed = previous.find((image) => image.id === id);
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return previous.filter((image) => image.id !== id);
    });
    setMarkdownContent((previous) =>
      previous
        .split("\n")
        .filter((line) => !line.includes(`clipboard-image://${id}`))
        .join("\n")
        .replace(/\n{3,}/g, "\n\n"),
    );
  };

  const handleClipboardContentChange = (
    event: React.ChangeEvent<HTMLTextAreaElement>,
  ) => {
    const nextContent = event.target.value;
    setMarkdownContent(nextContent);
    setClipboardImages((previous) => previous.filter((image) => {
      const retained = nextContent.includes(`clipboard-image://${image.id}`);
      if (!retained) URL.revokeObjectURL(image.previewUrl);
      return retained;
    }));
  };

  const handleClipboardPreviewError = (id: string) => {
    setClipboardImages((previous) => previous.map((image) => {
      if (image.id !== id || !image.sourceUrls) return image;
      const currentIndex = image.sourceUrls.indexOf(image.previewUrl);
      const nextUrl = image.sourceUrls[currentIndex + 1];
      return nextUrl
        ? { ...image, previewUrl: nextUrl }
        : { ...image, previewFailed: true };
    }));
  };

  const handleClipboardImport = async () => {
    if (!kbId || (!markdownContent.trim() && clipboardImages.length === 0)) return;
    try {
      setUploading(true);
      setError("");
      setSuccessMsg("");
      const resolvedImages = await Promise.all(clipboardImages.map(async (image) => {
        if (image.file) {
          return { file: image.file };
        }
        if (!image.sourceUrl && !image.sourceUrls?.length) {
          throw new Error(`图片「${image.alt}」缺少可读取的数据`);
        }
        const candidates = image.sourceUrls || [image.sourceUrl!];
        for (const candidate of candidates) {
          const controller = new AbortController();
          const timeout = window.setTimeout(() => controller.abort(), 8000);
          try {
            const response = await fetch(candidate, {
              credentials: "include",
              signal: controller.signal,
            });
            if (!response.ok) continue;
            const blob = await response.blob();
            if (!blob.type.startsWith("image/")) continue;
            const subtype = blob.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
            return {
              file: new File([blob], `${image.id}.${subtype}`, { type: blob.type }),
            };
          } catch {
            // Try the next clipboard-provided source.
          } finally {
            window.clearTimeout(timeout);
          }
        }
        const remoteCandidates = candidates.filter((url) => /^https?:/i.test(url));
        if (remoteCandidates.length > 0) {
          return {
            reference: {
              id: image.id,
              url: remoteCandidates[0],
              urls: remoteCandidates,
              alt: image.alt,
            },
          };
        }
        throw new Error(
          `无法读取图片「${image.alt}」：源应用只提供了不可跨页面访问的临时图片地址`,
        );
      }));
      const result = await pasteClipboardDocument(
        kbId,
        {
          content: markdownContent,
          docName: markdownDocName.trim() || undefined,
          images: resolvedImages.flatMap((image) => image.file ? [image.file] : []),
          imageReferences: resolvedImages.flatMap((image) =>
            image.reference ? [image.reference] : [],
          ),
        },
        {
          strategy: chunkStrategy,
          chunkSize,
          overlap: chunkOverlap,
        },
      );
      if (result.isDuplicate) {
        setSuccessMsg(`内容已存在于文档「${result.existingDocName || "已有文档"}」，未重复导入`);
      } else {
        setSuccessMsg(`剪贴板文档「${result.docName}」已提交处理`);
        clipboardImages.forEach((image) => URL.revokeObjectURL(image.previewUrl));
        setMarkdownContent("");
        setMarkdownDocName("");
        setClipboardImages([]);
      }
      await loadDocs();
    } catch (err: any) {
      setError(err?.message || "剪贴板内容导入失败");
    } finally {
      setUploading(false);
    }
  };

  // Single-file upload (backward compat)
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !kbId) return;

    const supported = ["txt", "md", "pdf", "docx", "xlsx", "csv", "pptx", "html"];
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!ext || !supported.includes(ext)) {
      setError(`不支持的文件类型: .${ext}，支持: ${supported.join(", ")}`);
      return;
    }

    try {
      setUploading(true);
      setError("");
      setSuccessMsg("");
      const chunkOptions: ChunkOptions = {
        strategy: chunkStrategy,
        chunkSize,
        overlap: chunkOverlap,
      };
      await uploadDocument(kbId, file, chunkOptions);
      setSuccessMsg(`「${file.name}」上传成功`);
      await loadDocs();
    } catch (err: any) {
      setError(err?.message || "上传失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // Batch file selection
  const handleBatchFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files || []);
    if (selected.length === 0) return;

    const supported = ["txt", "md", "pdf", "docx", "xlsx", "csv", "pptx", "html"];
    const valid: File[] = [];
    const rejected: string[] = [];
    for (const f of selected) {
      const ext = f.name.split(".").pop()?.toLowerCase();
      if (ext && supported.includes(ext)) {
        valid.push(f);
      } else {
        rejected.push(f.name);
      }
    }
    if (rejected.length > 0) {
      setError(`不支持的文件类型: ${rejected.join(", ")}`);
    }
    setBatchFiles(valid);
    setBatchResults(null);
    setBatchSummary("");
  };

  // Execute batch upload
  const handleBatchUpload = async () => {
    if (!kbId || batchFiles.length === 0) return;
    try {
      setUploading(true);
      setError("");
      setSuccessMsg("");
      setBatchResults(null);
      setBatchSummary("");

      const result = await batchUploadDocuments(kbId, batchFiles, {
        strategy: chunkStrategy,
        chunkSize,
        overlap: chunkOverlap,
      });

      setBatchResults(result.perFile);

      const parts: string[] = [];
      if (result.success > 0) parts.push(`成功 ${result.success} 个`);
      if (result.skippedDuplicates > 0) parts.push(`跳过重复 ${result.skippedDuplicates} 个`);
      if (result.failed > 0) parts.push(`失败 ${result.failed} 个`);
      setBatchSummary(parts.join("，"));
      setBatchFiles([]);
      await loadDocs();
    } catch (err: any) {
      setError(err?.message || "批量上传失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // Remove a file from batch selection
  const removeBatchFile = (index: number) => {
    setBatchFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleDelete = async (docId: string, docName: string) => {
    if (!confirm(`确定要删除文档「${docName}」吗？`)) return;
    try {
      await deleteDocument(docId);
      await loadDocs();
    } catch (err: any) {
      setError(err?.message || "删除失败");
    }
  };

  const handleViewChunks = (doc: KnowledgeDocument) => {
    navigate(`/knowledge/${kbId}/docs/${doc.id}`, {
      state: { docName: doc.docName, chunkCount: doc.chunkCount },
    });
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const statusLabel = (s: string) => {
    const map: Record<string, string> = {
      queued: "排队中",
      running: "处理中",
      success: "已完成",
      failed: "失败",
    };
    return map[s] || s;
  };

  const statusColor = (s: string) => {
    const map: Record<string, string> = {
      queued: "text-blue-600 bg-blue-50",
      running: "text-yellow-600 bg-yellow-50",
      success: "text-green-600 bg-green-50",
      failed: "text-red-600 bg-red-50",
    };
    return map[s] || "text-gray-500 bg-gray-50";
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-5xl mx-auto p-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => navigate("/knowledge")}
            className="p-1 hover:bg-gray-200 rounded"
            title="返回知识库列表"
          >
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-xl font-bold">文档管理</h1>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
            {error}
            <button className="ml-2 underline" onClick={() => setError("")}>关闭</button>
          </div>
        )}

        {successMsg && (
          <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded-lg text-sm">
            {successMsg}
            <button className="ml-2 underline" onClick={() => setSuccessMsg("")}>关闭</button>
          </div>
        )}

        {/* Chunking config */}
        <div className="mb-6 p-4 bg-white border rounded-lg">
          <h3 className="text-sm font-medium mb-3 text-gray-700">分块配置</h3>
          <div className="flex flex-wrap gap-4 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">切分策略</label>
              <select
                value={chunkStrategy}
                onChange={(e) => setChunkStrategy(e.target.value)}
                className="border rounded px-2 py-1.5 text-sm bg-white min-w-[140px]"
              >
                <option value="FIXED_WINDOW">固定窗口</option>
                <option value="SEMANTIC">语义切分</option>
                <option value="BLOCK_AWARE">块感知切分</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">块大小 (字符)</label>
              <input
                type="number"
                min={100}
                max={5000}
                step={50}
                value={chunkSize}
                onChange={(e) => setChunkSize(Number(e.target.value))}
                className="border rounded px-2 py-1.5 text-sm w-28"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">重叠 (字符)</label>
              <input
                type="number"
                min={0}
                max={1000}
                step={10}
                value={chunkOverlap}
                onChange={(e) => setChunkOverlap(Number(e.target.value))}
                className="border rounded px-2 py-1.5 text-sm w-28"
              />
            </div>
          </div>
        </div>

        {/* Upload mode toggle */}
        <div className="mb-4 flex gap-2">
          <button
            onClick={() => setUploadMode("batch")}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              uploadMode === "batch" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            <Files size={14} className="inline mr-1" />
            批量上传
          </button>
          <button
            onClick={() => setUploadMode("file")}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              uploadMode === "file" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            <Upload size={14} className="inline mr-1" />
            单文件
          </button>
          <button
            onClick={() => setUploadMode("url")}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              uploadMode === "url" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            <Globe size={14} className="inline mr-1" />
            URL导入
          </button>
          <button
            onClick={() => setUploadMode("paste")}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              uploadMode === "paste" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            <ClipboardPaste size={14} className="inline mr-1" />
            粘贴内容
          </button>
        </div>

        {/* Upload area */}
        {uploadMode === "batch" ? (
          <div className="mb-6 p-6 bg-white border rounded-lg">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".txt,.md,.pdf,.docx,.xlsx,.csv,.pptx,.html"
              onChange={handleBatchFileSelect}
              className="hidden"
            />

            {/* Selected files list */}
            {batchFiles.length > 0 && (
              <div className="mb-4">
                <div className="text-sm font-medium text-gray-700 mb-2">
                  已选择 {batchFiles.length} 个文件
                </div>
                <div className="max-h-48 overflow-y-auto space-y-1">
                  {batchFiles.map((f, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between text-sm text-gray-600 bg-gray-50 rounded px-3 py-1.5"
                    >
                      <span className="flex-1 min-w-0 truncate">{f.name}</span>
                      <span className="text-xs text-gray-400 ml-3 w-[60px] text-right shrink-0 tabular-nums font-mono">
                        {(f.size / 1024).toFixed(1)} KB
                      </span>
                      <button
                        onClick={() => removeBatchFile(i)}
                        className="ml-2 text-gray-400 hover:text-red-500"
                      >
                        <XCircle size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {batchFiles.length === 0 && !uploading ? (
              <button
                onClick={() => fileInputRef.current?.click()}
                className="w-full border-2 border-dashed border-gray-300 rounded-lg p-8 text-gray-500 hover:border-blue-400 hover:text-blue-600 transition-colors flex flex-col items-center gap-2"
              >
                <Files size={32} />
                <span className="text-sm font-medium">点击选择多个文件</span>
                <span className="text-xs text-gray-400">
                  支持 TXT / MD / PDF / DOCX / XLSX / CSV / PPTX / HTML
                </span>
              </button>
            ) : uploading ? (
              <div className="flex items-center justify-center gap-2 text-blue-600 py-8">
                <Loader2 size={20} className="animate-spin" />
                <span className="text-sm">批量处理中，请稍候...</span>
              </div>
            ) : (
              <button
                onClick={handleBatchUpload}
                className="w-full py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
              >
                开始上传 {batchFiles.length} 个文件
              </button>
            )}

            {/* Batch result summary */}
            {batchResults && (
              <div className="mt-4 border-t pt-4">
                <div className="text-sm font-medium mb-2">
                  批量导入完成：{batchSummary}
                </div>
                <div className="max-h-64 overflow-y-auto space-y-1">
                  {batchResults.map((r, i) => (
                    <div
                      key={i}
                      className={`flex items-center gap-2 text-sm rounded px-3 py-1.5 ${
                        r.status === "success"
                          ? "bg-green-50 text-green-700"
                          : r.status === "duplicate"
                          ? "bg-yellow-50 text-yellow-700"
                          : "bg-red-50 text-red-700"
                      }`}
                    >
                      {r.status === "success" ? (
                        <CheckCircle size={14} />
                      ) : r.status === "duplicate" ? (
                        <AlertCircle size={14} />
                      ) : (
                        <XCircle size={14} />
                      )}
                      <span className="truncate flex-1">{r.fileName}</span>
                      <span className="text-xs shrink-0">
                        {r.status === "success"
                          ? `${r.chunkCount ?? 0} 分块`
                          : r.status === "duplicate"
                          ? "已存在"
                          : r.error || "失败"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : uploadMode === "file" ? (
          <div className="mb-6 p-6 bg-white border-2 border-dashed border-gray-300 rounded-lg text-center hover:border-blue-400 transition-colors">
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.pdf,.docx,.xlsx,.csv,.pptx,.html"
              onChange={handleUpload}
              className="hidden"
            />
            {uploading ? (
              <div className="flex items-center justify-center gap-2 text-blue-600">
                <Loader2 size={20} className="animate-spin" />
                <span className="text-sm">上传解析中...</span>
              </div>
            ) : (
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex flex-col items-center gap-2 w-full text-gray-500 hover:text-blue-600"
              >
                <Upload size={32} />
                <span className="text-sm font-medium">点击上传文档</span>
                <span className="text-xs text-gray-400">支持 TXT / MD / PDF / DOCX / XLSX / CSV / PPTX / HTML</span>
              </button>
            )}
          </div>
        ) : uploadMode === "paste" ? (
          <div className="mb-6 p-6 bg-white border rounded-lg">
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500 block mb-1">文档名称（可选）</label>
                <input
                  type="text"
                  value={markdownDocName}
                  onChange={(e) => setMarkdownDocName(e.target.value)}
                  placeholder="例如：产品说明"
                  maxLength={256}
                  className="w-full border rounded px-3 py-2 text-sm"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-xs text-gray-500">文本 / Markdown / 图片</label>
                  <span className="text-xs text-gray-400">{markdownContent.length} 字符</span>
                </div>
                <textarea
                  value={markdownContent}
                  onChange={handleClipboardContentChange}
                  onPaste={handleClipboardContentPaste}
                  placeholder={"在这里粘贴文本、Markdown 或截图。\n\n# 标题\n\n正文和图片可以一起粘贴……"}
                  rows={14}
                  spellCheck={false}
                  className="w-full border rounded px-3 py-2 text-sm font-mono resize-y min-h-[240px] focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-400"
                />
              </div>
              {clipboardImages.length > 0 && (
                <div>
                  <div className="text-xs text-gray-500 mb-2">
                    已捕获 {clipboardImages.length} 张剪贴板图片
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {clipboardImages.map((image) => (
                      <div key={image.id} className="relative border rounded-md overflow-hidden bg-gray-50">
                        {image.previewFailed ? (
                          <div className="w-full h-24 px-2 flex items-center justify-center text-center text-xs text-amber-600">
                            预览不可用，导入时将继续尝试读取
                          </div>
                        ) : (
                          <img
                            src={image.previewUrl}
                            alt={image.alt}
                            referrerPolicy="no-referrer"
                            onError={() => handleClipboardPreviewError(image.id)}
                            className="w-full h-24 object-contain"
                          />
                        )}
                        <button
                          type="button"
                          onClick={() => removeClipboardImage(image.id)}
                          className="absolute top-1 right-1 p-0.5 rounded-full bg-white/90 text-gray-500 hover:text-red-500"
                          title="移除图片"
                        >
                          <XCircle size={16} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-400">
                  图片会保存为文档资产并生成可检索描述，随后统一分块、Embedding 和入库
                </span>
                <button
                  onClick={handleClipboardImport}
                  disabled={uploading || (!markdownContent.trim() && clipboardImages.length === 0)}
                  className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1"
                >
                  {uploading ? (
                    <>
                      <Loader2 size={14} className="animate-spin" /> 导入中
                    </>
                  ) : (
                    <>
                      <ClipboardPaste size={14} /> 导入剪贴板内容
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="mb-6 p-6 bg-white border rounded-lg">
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500 block mb-1">文档URL</label>
                <div className="flex gap-2">
                  <input
                    type="url"
                    value={urlInput}
                    onChange={(e) => setUrlInput(e.target.value)}
                    placeholder="https://example.com/document.md"
                    className="flex-1 border rounded px-3 py-2 text-sm"
                  />
                  {uploading ? (
                    <button disabled className="px-4 py-2 bg-blue-400 text-white rounded text-sm flex items-center gap-1">
                      <Loader2 size={14} className="animate-spin" /> 导入中
                    </button>
                  ) : (
                    <button
                      onClick={handleUrlUpload}
                      disabled={!urlInput.trim()}
                      className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1"
                    >
                      <LinkIcon size={14} /> 导入
                    </button>
                  )}
                </div>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">文档名称（可选）</label>
                <input
                  type="text"
                  value={urlDocName}
                  onChange={(e) => setUrlDocName(e.target.value)}
                  placeholder="留空则自动从URL提取"
                  className="w-full border rounded px-3 py-2 text-sm"
                />
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="scheduleCheck"
                  checked={scheduleEnabled}
                  onChange={(e) => setScheduleEnabled(e.target.checked)}
                  className="rounded"
                />
                <label htmlFor="scheduleCheck" className="text-xs text-gray-600">
                  启用定时自动刷新（每小时检查一次，URL内容变化后自动更新）
                </label>
              </div>
            </div>
          </div>
        )}

        {/* Doc list */}
        {loading ? (
          <div className="text-center text-gray-400 py-8">加载中...</div>
        ) : docs.length === 0 ? (
          <div className="text-center text-gray-400 py-8">
            <FileText size={48} className="mx-auto mb-3 text-gray-300" />
            <p>暂无文档，上传文件开始构建知识库</p>
          </div>
        ) : (
          <div className="space-y-2">
            {docs.map((doc) => (
              <div key={doc.id} className="bg-white border rounded-lg overflow-hidden">
                <div className="flex items-center justify-between p-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <span className="font-medium text-sm truncate">{doc.docName}</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${statusColor(doc.status)}`}>
                        {statusLabel(doc.status)}
                      </span>
                    </div>
                    <div className="flex gap-4 mt-1 text-xs text-gray-500">
                      <span>.{doc.fileType}</span>
                      <span>{formatSize(doc.fileSize)}</span>
                      <span>{doc.chunkCount} 个分块</span>
                      <span>{doc.createTime}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 ml-3">
                    <button
                      onClick={() => handleViewChunks(doc)}
                      className="p-2 text-gray-400 hover:text-blue-500 hover:bg-blue-50 rounded-md"
                      title="查看分块"
                    >
                      <Eye size={16} />
                    </button>
                    <button
                      onClick={() => handleDelete(doc.id, doc.docName)}
                      className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-md"
                      title="删除文档"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>


              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
