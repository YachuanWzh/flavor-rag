import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Trash2, Database, ArrowLeft } from "lucide-react";
import {
  fetchKnowledgeBases,
  createKnowledgeBase,
  deleteKnowledgeBase,
} from "@/services/knowledgeService";
import type { KnowledgeBase } from "@/types";

export default function KnowledgeBasePage() {
  const navigate = useNavigate();
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      setLoading(true);
      const data = await fetchKnowledgeBases();
      setKbs(data);
    } catch {
      setError("加载知识库列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      setCreating(true);
      setError("");
      await createKnowledgeBase(newName.trim());
      setNewName("");
      setShowCreate(false);
      await load();
    } catch (err: any) {
      setError(err?.message || "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`确定要删除知识库「${name}」吗？`)) return;
    try {
      await deleteKnowledgeBase(id);
      await load();
    } catch (err: any) {
      setError(err?.message || "删除失败");
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate("/chat")}
              className="p-1 hover:bg-gray-200 rounded"
              title="返回对话"
            >
              <ArrowLeft size={20} />
            </button>
            <h1 className="text-xl font-bold">知识库管理</h1>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1 px-3 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm"
          >
            <Plus size={16} />
            新建知识库
          </button>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
            {error}
            <button className="ml-2 underline" onClick={() => setError("")}>关闭</button>
          </div>
        )}

        {/* Create modal */}
        {showCreate && (
          <div className="mb-6 p-4 bg-white border rounded-lg shadow-sm">
            <h2 className="font-semibold mb-3 text-sm">新建知识库</h2>
            <div className="flex gap-2">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="输入知识库名称"
                className="flex-1 px-3 py-2 border rounded-md text-sm"
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                autoFocus
              />
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
              >
                {creating ? "创建中..." : "创建"}
              </button>
              <button
                onClick={() => { setShowCreate(false); setNewName(""); }}
                className="px-4 py-2 border rounded-md text-sm hover:bg-gray-50"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* KB List */}
        {loading ? (
          <div className="text-center text-gray-400 py-12">加载中...</div>
        ) : kbs.length === 0 ? (
          <div className="text-center text-gray-400 py-12">
            <Database size={48} className="mx-auto mb-3 text-gray-300" />
            <p>暂无知识库，点击上方按钮创建</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {kbs.map((kb) => (
              <div
                key={kb.id}
                className="flex items-center justify-between p-4 bg-white border rounded-lg hover:border-blue-300 transition-colors cursor-pointer"
                onClick={() => navigate(`/knowledge/${kb.id}`)}
              >
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-sm truncate">{kb.name}</h3>
                  <div className="flex gap-4 mt-1 text-xs text-gray-500">
                    <span>模型: {kb.embeddingModel}</span>
                    <span className="truncate">集合: {kb.collectionName}</span>
                    <span>创建: {kb.createTime}</span>
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(kb.id, kb.name);
                  }}
                  className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-md ml-3"
                  title="删除知识库"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
