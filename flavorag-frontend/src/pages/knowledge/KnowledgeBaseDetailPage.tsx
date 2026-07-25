import { useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft, Upload, Trash2, FileText, Eye, Loader2, Link as LinkIcon, Globe,
} from "lucide-react";
import {
  fetchDocuments,
  uploadDocument,
  uploadDocumentFromUrl,
  deleteDocument,
  type ChunkOptions,
} from "@/services/knowledgeService";
import type { KnowledgeDocument } from "@/types";

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

  // Upload mode: file or URL
  const [uploadMode, setUploadMode] = useState<"file" | "url">("file");
  const [urlInput, setUrlInput] = useState("");
  const [urlDocName, setUrlDocName] = useState("");
  const [scheduleEnabled, setScheduleEnabled] = useState(false);

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

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !kbId) return;

    const supported = ["txt", "md", "pdf", "docx"];
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
      running: "处理中",
      success: "已完成",
      failed: "失败",
    };
    return map[s] || s;
  };

  const statusColor = (s: string) => {
    const map: Record<string, string> = {
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
            onClick={() => setUploadMode("file")}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              uploadMode === "file" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            <Upload size={14} className="inline mr-1" />
            上传文件
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
        </div>

        {/* Upload area */}
        {uploadMode === "file" ? (
          <div className="mb-6 p-6 bg-white border-2 border-dashed border-gray-300 rounded-lg text-center hover:border-blue-400 transition-colors">
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.pdf,.docx"
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
                <span className="text-xs text-gray-400">支持 TXT / MD / PDF / DOCX</span>
              </button>
            )}
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
