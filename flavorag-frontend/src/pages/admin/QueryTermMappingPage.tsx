import { useEffect, useState } from "react";
import { Plus, Trash2, Edit3 } from "lucide-react";
import { api } from "@/services/api";

interface TermMapping {
  id: string;
  sourceTerm: string;
  targetTerm: string;
  kbId: string | null;
  mappingType: string;
  enabled: number;
}

const MAPPING_TYPES = ["EXACT", "SYNONYM", "ABBREVIATION"];
const TYPE_LABELS: Record<string, string> = {
  EXACT: "精确匹配",
  SYNONYM: "同义词",
  ABBREVIATION: "缩写",
};
const TYPE_COLORS: Record<string, string> = {
  EXACT: "bg-blue-50 text-blue-700",
  SYNONYM: "bg-green-50 text-green-700",
  ABBREVIATION: "bg-purple-50 text-purple-700",
};

export default function QueryTermMappingPage() {
  const [items, setItems] = useState<TermMapping[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({
    source_term: "", target_term: "", kb_id: "", mapping_type: "EXACT",
  });
  const [error, setError] = useState("");

  const loadItems = async () => {
    try {
      const data = await api.get<TermMapping[], TermMapping[]>("/api/admin/query-term-mapping");
      setItems(data);
    } catch { setError("加载失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadItems(); }, []);

  const handleSave = async () => {
    if (!form.source_term.trim() || !form.target_term.trim()) return;
    try {
      if (editingId) {
        await api.put(`/api/admin/query-term-mapping/${editingId}`, form);
      } else {
        await api.post("/api/admin/query-term-mapping", form);
      }
      setShowForm(false);
      setEditingId(null);
      setForm({ source_term: "", target_term: "", kb_id: "", mapping_type: "EXACT" });
      await loadItems();
    } catch (err: any) { setError(err?.message || "保存失败"); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("删除此映射？")) return;
    await api.delete(`/api/admin/query-term-mapping/${id}`);
    await loadItems();
  };

  const startEdit = (m: TermMapping) => {
    setForm({
      source_term: m.sourceTerm, target_term: m.targetTerm,
      kb_id: m.kbId || "", mapping_type: m.mappingType,
    });
    setEditingId(m.id);
    setShowForm(true);
  };

  if (loading) return <div className="p-8 text-gray-400">加载中...</div>;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-bold">查询词映射</h2>
          <p className="text-xs text-gray-500 mt-1">配置同义词映射，提升检索召回率。如 搜索"HR系统"时自动映射为"人事管理系统"</p>
        </div>
        <button onClick={() => { setForm({ source_term: "", target_term: "", kb_id: "", mapping_type: "EXACT" }); setEditingId(null); setShowForm(true); }}
          className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
          <Plus size={14} /> 新增映射
        </button>
      </div>

      {error && <div className="mb-3 p-2 bg-red-50 text-red-700 text-xs rounded">{error}</div>}

      {showForm && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => setShowForm(false)}>
          <div className="bg-white rounded-xl p-6 w-[480px] shadow-xl" onClick={e => e.stopPropagation()}>
            <h3 className="font-bold mb-4">{editingId ? "编辑映射" : "新增映射"}</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500">源词/同义词 *</label>
                <input value={form.source_term} onChange={e => setForm({...form, source_term: e.target.value})}
                  placeholder="如 HR系统" className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-500">目标词/标准词 *</label>
                <input value={form.target_term} onChange={e => setForm({...form, target_term: e.target.value})}
                  placeholder="如 人事管理系统" className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-500">映射类型</label>
                <select value={form.mapping_type} onChange={e => setForm({...form, mapping_type: e.target.value})}
                  className="w-full border rounded px-2 py-1.5 text-sm">
                  {MAPPING_TYPES.map(t => (
                    <option key={t} value={t}>{TYPE_LABELS[t]}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500">关联知识库ID (可选)</label>
                <input value={form.kb_id} onChange={e => setForm({...form, kb_id: e.target.value})}
                  placeholder="留空则全局生效" className="w-full border rounded px-2 py-1.5 text-sm" />
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

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">源词</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">→ 目标词</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500 w-20">类型</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500 w-20">状态</th>
              <th className="text-right py-2.5 px-4 text-xs font-medium text-gray-500 w-20">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map(m => (
              <tr key={m.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="py-2.5 px-4 font-mono text-xs">{m.sourceTerm}</td>
                <td className="py-2.5 px-4 font-mono text-xs text-blue-700">
                  → {m.targetTerm}
                  {m.kbId && <span className="ml-2 text-gray-400 font-sans">({m.kbId})</span>}
                </td>
                <td className="py-2.5 px-4">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${TYPE_COLORS[m.mappingType] || "bg-gray-100"}`}>
                    {TYPE_LABELS[m.mappingType] || m.mappingType}
                  </span>
                </td>
                <td className="py-2.5 px-4">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${m.enabled ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-400"}`}>
                    {m.enabled ? "启用" : "禁用"}
                  </span>
                </td>
                <td className="py-2.5 px-4 text-right">
                  <button onClick={() => startEdit(m)} className="p-1 text-gray-400 hover:text-blue-500 rounded"><Edit3 size={14} /></button>
                  <button onClick={() => handleDelete(m.id)} className="p-1 text-gray-400 hover:text-red-500 rounded"><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={5} className="py-8 text-center text-gray-400">暂无映射</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
