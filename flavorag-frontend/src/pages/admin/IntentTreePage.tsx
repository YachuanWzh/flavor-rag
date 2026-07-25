import { useEffect, useState } from "react";
import { Plus, Trash2, Edit3, X, Check } from "lucide-react";
import { api } from "@/services/api";

interface IntentNode {
  id: string;
  intentCode: string;
  name: string;
  level: number;
  parentIntentCode: string | null;
  kbId: string | null;
  description: string | null;
  collectionName: string | null;
  searchChannels: string[] | null;
  promptTemplate: string | null;
  sortOrder: number;
  enabled: number;
}

const emptyForm = {
  intent_code: "",
  name: "",
  level: 1,
  parent_intent_code: "",
  description: "",
  collection_name: "",
  prompt_template: "",
  sort_order: 0,
};

export default function IntentTreePage() {
  const [nodes, setNodes] = useState<IntentNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const loadNodes = async () => {
    try {
      const data = await api.get("/api/admin/intent-tree");
      setNodes(data as IntentNode[]);
    } catch { setError("加载失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadNodes(); }, []);

  const handleSave = async () => {
    if (!form.intent_code.trim() || !form.name.trim()) return;
    try {
      if (editingId) {
        await api.put(`/api/admin/intent-tree/${editingId}`, form);
      } else {
        await api.post("/api/admin/intent-tree", form);
      }
      setShowForm(false);
      setEditingId(null);
      setForm(emptyForm);
      await loadNodes();
    } catch (err: any) { setError(err?.message || "保存失败"); }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`删除意图「${name}」？`)) return;
    await api.delete(`/api/admin/intent-tree/${id}`);
    await loadNodes();
  };

  const startEdit = (n: IntentNode) => {
    setForm({
      intent_code: n.intentCode,
      name: n.name,
      level: n.level,
      parent_intent_code: n.parentIntentCode || "",
      description: n.description || "",
      collection_name: n.collectionName || "",
      prompt_template: n.promptTemplate || "",
      sort_order: n.sortOrder,
    });
    setEditingId(n.id);
    setShowForm(true);
  };

  // Group nodes by level for tree display
  const topNodes = nodes.filter(n => !n.parentIntentCode);
  const childMap: Record<string, IntentNode[]> = {};
  nodes.forEach(n => {
    if (n.parentIntentCode) {
      const key = n.parentIntentCode;
      if (!childMap[key]) childMap[key] = [];
      childMap[key].push(n);
    }
  });

  if (loading) return <div className="p-8 text-gray-400">加载中...</div>;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-bold">意图树管理</h2>
        <button onClick={() => { setForm(emptyForm); setEditingId(null); setShowForm(true); }}
          className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
          <Plus size={14} /> 新增意图
        </button>
      </div>

      {error && <div className="mb-3 p-2 bg-red-50 text-red-700 text-xs rounded">{error}</div>}

      {/* Create/Edit Modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => setShowForm(false)}>
          <div className="bg-white rounded-xl p-6 w-[480px] max-h-[80vh] overflow-y-auto shadow-xl" onClick={e => e.stopPropagation()}>
            <h3 className="font-bold mb-4">{editingId ? "编辑意图" : "新增意图"}</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500">意图编码 *</label>
                <input value={form.intent_code} onChange={e => setForm({...form, intent_code: e.target.value})}
                  placeholder="如 code_search" className="w-full border rounded px-2 py-1.5 text-sm" disabled={!!editingId} />
              </div>
              <div>
                <label className="text-xs text-gray-500">名称 *</label>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})}
                  placeholder="如 代码搜索" className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-gray-500">层级</label>
                  <input type="number" min={1} max={5} value={form.level} onChange={e => setForm({...form, level: +e.target.value})}
                    className="w-full border rounded px-2 py-1.5 text-sm" />
                </div>
                <div>
                  <label className="text-xs text-gray-500">排序</label>
                  <input type="number" value={form.sort_order} onChange={e => setForm({...form, sort_order: +e.target.value})}
                    className="w-full border rounded px-2 py-1.5 text-sm" />
                </div>
              </div>
              <div>
                <label className="text-xs text-gray-500">父意图编码</label>
                <input value={form.parent_intent_code} onChange={e => setForm({...form, parent_intent_code: e.target.value})}
                  placeholder="如 knowledge_qa" className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-500">描述</label>
                <input value={form.description} onChange={e => setForm({...form, description: e.target.value})}
                  className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-500">Collection名称</label>
                <input value={form.collection_name} onChange={e => setForm({...form, collection_name: e.target.value})}
                  className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-500">Prompt模板</label>
                <textarea value={form.prompt_template} onChange={e => setForm({...form, prompt_template: e.target.value})}
                  rows={2} className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
            </div>
            <div className="flex gap-2 mt-4 justify-end">
              <button onClick={() => { setShowForm(false); setEditingId(null); }}
                className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded">取消</button>
              <button onClick={handleSave}
                className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">保存</button>
            </div>
          </div>
        </div>
      )}

      {/* Tree table */}
      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">意图编码</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">名称</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">父节点</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">Collection</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">状态</th>
              <th className="text-right py-2.5 px-4 text-xs font-medium text-gray-500 w-20">操作</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map(n => (
              <tr key={n.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="py-2.5 px-4">
                  <span className={`${n.level > 1 ? "ml-" + (n.level * 3) : ""} font-mono text-xs`}>
                    {n.level > 1 ? "└ " : ""}{n.intentCode}
                  </span>
                </td>
                <td className="py-2.5 px-4">{n.name}</td>
                <td className="py-2.5 px-4 text-gray-500">{n.parentIntentCode || "—"}</td>
                <td className="py-2.5 px-4 text-gray-500 text-xs">{n.collectionName || "—"}</td>
                <td className="py-2.5 px-4">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${n.enabled ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-400"}`}>
                    {n.enabled ? "启用" : "禁用"}
                  </span>
                </td>
                <td className="py-2.5 px-4 text-right">
                  <div className="flex gap-1 justify-end">
                    <button onClick={() => startEdit(n)} className="p-1 text-gray-400 hover:text-blue-500 rounded"><Edit3 size={14} /></button>
                    <button onClick={() => handleDelete(n.id, n.name)} className="p-1 text-gray-400 hover:text-red-500 rounded"><Trash2 size={14} /></button>
                  </div>
                </td>
              </tr>
            ))}
            {nodes.length === 0 && (
              <tr><td colSpan={6} className="py-8 text-center text-gray-400">暂无意图节点</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
