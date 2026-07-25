import { useEffect, useRef, useState } from "react";
import { Layers, Play, Plus, Trash2, CheckCircle2, XCircle, Upload, RefreshCw } from "lucide-react";
import { api } from "@/services/api";
import { fetchKnowledgeBases, fetchDocuments, reprocessDocument } from "@/services/knowledgeService";
import type { KnowledgeBase, KnowledgeDocument } from "@/types";

interface PipelineItem {
  id: string;
  name: string;
  description: string;
  createTime: string;
}

interface TaskItem {
  id: string;
  pipelineId: string;
  sourceType: string;
  sourceFileName: string;
  status: string;
  chunkCount: number;
  errorMessage: string;
  startedAt: string;
  completedAt: string;
}

const NODE_TYPE_LABEL: Record<string, string> = {
  fetcher: "下载",
  parser: "解析",
  chunker: "分块",
  enricher: "增强(元数据)",
  enhancer: "增强(改写)",
  indexer: "索引",
};

export default function IngestionPipelinePage() {
  const [tab, setTab] = useState<"pipelines" | "tasks">("pipelines");
  const [pipelines, setPipelines] = useState<PipelineItem[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newNodeCount, setNewNodeCount] = useState(5);

  // Execute dialog
  const [showExecute, setShowExecute] = useState(false);
  const [execPipeline, setExecPipeline] = useState<PipelineItem | null>(null);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [selKbId, setSelKbId] = useState("");
  const [selDocId, setSelDocId] = useState("");
  const [execFile, setExecFile] = useState<File | null>(null);
  const [execMode, setExecMode] = useState<"existing" | "upload">("existing");
  const [executing, setExecuting] = useState(false);
  const [execResult, setExecResult] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchPipelines = () => {
    setLoading(true);
    api.get("/api/admin/ingestion/pipelines?pageSize=50")
      .then((data: any) => setPipelines(data.rows))
      .finally(() => setLoading(false));
  };

  const fetchTasks = () => {
    setLoading(true);
    api.get("/api/admin/ingestion/tasks?pageSize=50")
      .then((data: any) => setTasks(data.rows))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    tab === "pipelines" ? fetchPipelines() : fetchTasks();
  }, [tab]);

  const createPipeline = () => {
    if (!newName.trim()) return;
    const types = ["fetcher", "parser", "chunker", "enricher", "enhancer", "indexer"];
    const selected = types.slice(0, newNodeCount);
    const defaultNodes = selected.map((t, i) => ({
      node_id: `n${i + 1}`,
      node_type: t,
      next_node_id: i < selected.length - 1 ? `n${i + 2}` : undefined,
    }));
    api.post("/api/admin/ingestion/pipelines", {
      name: newName.trim(),
      description: newDesc.trim() || undefined,
      nodes: defaultNodes,
    })
      .then(() => {
        setShowCreate(false);
        setNewName("");
        setNewDesc("");
        fetchPipelines();
      });
  };

  const deletePipeline = (id: string) => {
    if (!confirm("确定删除此流水线？")) return;
    api.delete(`/api/admin/ingestion/pipelines/${id}`)
      .then(() => fetchPipelines());
  };

  const openExecuteDialog = async (p: PipelineItem) => {
    setExecPipeline(p);
    setShowExecute(true);
    setExecResult("");
    setSelKbId("");
    setSelDocId("");
    setExecFile(null);
    try {
      const kbData: any = await fetchKnowledgeBases();
      setKbs(Array.isArray(kbData) ? kbData : (kbData?.rows || []));
    } catch { setKbs([]); }
  };

  const loadDocs = async (kbId: string) => {
    setSelKbId(kbId);
    setSelDocId("");
    if (!kbId) { setDocs([]); return; }
    try {
      const data: any = await fetchDocuments(kbId);
      setDocs(Array.isArray(data) ? data : (data?.rows || []));
    } catch { setDocs([]); }
  };

  const handleExecute = async () => {
    if (!execPipeline) return;
    setExecuting(true);
    setExecResult("");
    try {
      if (execMode === "upload" && execFile) {
        // Upload + pipeline mode: upload file to selected KB then trigger pipeline
        if (!selKbId) { alert("请选择目标知识库"); setExecuting(false); return; }
        const form = new FormData();
        form.append("file", execFile);
        form.append("chunk_strategy", "FIXED_WINDOW");
        form.append("chunk_size", "512");
        form.append("overlap", "128");
        const kbResult: any = await api.post(`/api/knowledge-base/${selKbId}/docs/upload`, form, {
          headers: { "Content-Type": "multipart/form-data" },
          timeout: 600000,
        });
        const docId = kbResult?.data?.id || kbResult?.id;
        if (!docId) throw new Error("无法获取上传后的文档ID");
        // Then reprocess with pipeline
        const result = await reprocessDocument(docId, execPipeline.id);
        setExecResult(`✅ 成功! ${result.chunkCount} 个分块已入库`);
      } else if (selDocId) {
        const result = await reprocessDocument(selDocId, execPipeline.id);
        setExecResult(`✅ 成功! ${result.chunkCount} 个分块已入库`);
        fetchTasks();
      }
    } catch (e: any) {
      setExecResult(`❌ ${e?.message || "执行失败"}`);
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold">入库流水线</h2>
        {tab === "pipelines" && (
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1 text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            <Plus size={14} /> 新建流水线
          </button>
        )}
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-96 shadow-xl">
            <h3 className="font-bold mb-4">新建入库流水线</h3>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="流水线名称"
              className="w-full border rounded-lg px-3 py-2 text-sm mb-2"
              autoFocus
            />
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="描述（可选）"
              className="w-full border rounded-lg px-3 py-2 text-sm mb-2"
            />
            <label className="text-xs text-gray-500 mb-2 block">
              节点数: {newNodeCount}
              <input type="range" min={2} max={6} value={newNodeCount}
                onChange={(e) => setNewNodeCount(Number(e.target.value))} className="ml-2 w-40" />
            </label>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowCreate(false)} className="px-4 py-1.5 text-sm border rounded-lg hover:bg-gray-50">取消</button>
              <button onClick={createPipeline} className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">创建</button>
            </div>
          </div>
        </div>
      )}

      {/* Execute modal */}
      {showExecute && execPipeline && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-[480px] max-h-[80vh] overflow-y-auto shadow-xl">
            <h3 className="font-bold mb-1">执行流水线: {execPipeline.name}</h3>
            <p className="text-xs text-gray-400 mb-4">
              Fetcher → Parser → Chunker → Enricher → Enhancer → Indexer
            </p>

            {/* Mode toggle */}
            <div className="flex gap-2 mb-3">
              <button onClick={() => setExecMode("existing")}
                className={`px-3 py-1 text-xs rounded ${execMode === "existing" ? "bg-blue-600 text-white" : "border"}`}>
                <RefreshCw size={12} className="inline mr-1" />已有文档重跑
              </button>
              <button onClick={() => setExecMode("upload")}
                className={`px-3 py-1 text-xs rounded ${execMode === "upload" ? "bg-blue-600 text-white" : "border"}`}>
                <Upload size={12} className="inline mr-1" />上传新文件
              </button>
            </div>

            {/* KB selector */}
            <label className="text-xs font-medium block mb-1">目标知识库</label>
            <select value={selKbId} onChange={(e) => loadDocs(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm mb-3 bg-white">
              <option value="">选择知识库...</option>
              {kbs.map((kb) => (
                <option key={kb.id} value={kb.id}>{kb.name} ({kb.collectionName})</option>
              ))}
            </select>

            {execMode === "existing" ? (
              <>
                <label className="text-xs font-medium block mb-1">选择文档</label>
                <select value={selDocId} onChange={(e) => setSelDocId(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm mb-3 bg-white"
                  disabled={!selKbId}>
                  <option value="">选择文档...</option>
                  {docs.map((d) => (
                    <option key={d.id} value={d.id}>{d.docName} ({d.chunkCount} chunks)</option>
                  ))}
                </select>
                {selKbId && docs.length === 0 && (
                  <p className="text-xs text-gray-400 mb-2">该知识库暂无文档</p>
                )}
              </>
            ) : (
              <div className="mb-3">
                <label className="text-xs font-medium block mb-1">上传文件</label>
                <input type="file" ref={fileRef}
                  onChange={(e) => setExecFile(e.target.files?.[0] || null)}
                  className="w-full text-sm"
                  accept=".txt,.md,.pdf,.docx" />
              </div>
            )}

            {/* Result */}
            {execResult && (
              <div className={`mb-3 p-3 text-sm rounded-lg ${
                execResult.startsWith("✅") ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
              }`}>{execResult}</div>
            )}

            <div className="flex justify-end gap-2">
              <button onClick={() => setShowExecute(false)}
                className="px-4 py-1.5 text-sm border rounded-lg hover:bg-gray-50">关闭</button>
              <button onClick={handleExecute} disabled={executing || (!selDocId && !execFile) || !selKbId}
                className="px-4 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50">
                {executing ? "执行中..." : "开始执行"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-4 mb-4 border-b">
        {(["pipelines", "tasks"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t === "pipelines" ? "流水线定义" : "执行任务"}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-gray-400 py-8">加载中...</div>
      ) : tab === "pipelines" ? (
        <div className="grid gap-3">
          {pipelines.map((p) => (
            <div key={p.id} className="bg-white rounded-xl border p-4 flex items-center justify-between hover:shadow-sm transition-shadow">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 bg-blue-50 rounded-lg flex items-center justify-center shrink-0">
                  <Layers size={16} className="text-blue-600" />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-medium">{p.name}</div>
                  <div className="text-xs text-gray-400">{p.description || "默认流水线 · Fetcher→Parser→Chunker→Enricher→Indexer"}</div>
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <span className="text-xs text-gray-400">{p.createTime?.slice(0, 10)}</span>
                <button
                  onClick={() => openExecuteDialog(p)}
                  className="flex items-center gap-1 px-2 py-1 text-xs bg-green-600 text-white rounded hover:bg-green-700"
                >
                  <Play size={12} /> 执行
                </button>
                <button
                  onClick={() => deletePipeline(p.id)}
                  className="p-1.5 hover:bg-red-50 text-red-400 hover:text-red-600 rounded"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
          {pipelines.length === 0 && (
            <div className="text-gray-400 text-center py-8">暂无流水线，点击"新建流水线"创建</div>
          )}
        </div>
      ) : (
        <div className="bg-white rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-24">状态</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500">来源</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-16">分块</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-36">开始时间</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-36">完成时间</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500">错误</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="py-2.5 px-3">
                    <span className={`text-xs px-1.5 py-0.5 rounded flex items-center gap-1 w-fit ${
                      t.status === "success" ? "bg-green-50 text-green-700" :
                      t.status === "running" ? "bg-blue-50 text-blue-700" :
                      "bg-red-50 text-red-600"
                    }`}>
                      {t.status === "success" ? <CheckCircle2 size={12} /> :
                       t.status === "error" ? <XCircle size={12} /> : null}
                      {t.status}
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-xs truncate max-w-[200px]">
                    {t.sourceFileName || t.sourceType || "—"}
                  </td>
                  <td className="py-2.5 px-3 text-xs">{t.chunkCount}</td>
                  <td className="py-2.5 px-3 text-xs text-gray-400">{t.startedAt?.slice(0, 19) || "—"}</td>
                  <td className="py-2.5 px-3 text-xs text-gray-400">{t.completedAt?.slice(0, 19) || "—"}</td>
                  <td className="py-2.5 px-3 text-xs text-red-500 truncate max-w-[200px]">
                    {t.errorMessage || "—"}
                  </td>
                </tr>
              ))}
              {tasks.length === 0 && (
                <tr><td colSpan={6} className="py-8 text-center text-gray-400">暂无执行任务</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
