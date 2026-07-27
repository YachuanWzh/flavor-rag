import { useEffect, useState } from "react";
import { Plus, Trash2, Edit3, X, Check } from "lucide-react";
import { api } from "@/services/api";

interface SampleQuestionItem {
  id: string;
  question: string;
  kbId: string | null;
  sortOrder: number;
  enabled: number;
  createTime: string;
}

export default function SampleQuestionsPage() {
  const [items, setItems] = useState<SampleQuestionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState({ question: "", kb_id: "", sort_order: 0 });
  const [error, setError] = useState("");

  const loadItems = async () => {
    try {
      const data = await api.get<SampleQuestionItem[], SampleQuestionItem[]>("/api/admin/sample-questions");
      setItems(data);
    } catch { setError("加载失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadItems(); }, []);

  const handleSave = async () => {
    if (!form.question.trim()) return;
    try {
      if (editingId) {
        await api.put(`/api/admin/sample-questions/${editingId}`, form);
      } else {
        await api.post("/api/admin/sample-questions", form);
      }
      setShowForm(false);
      setEditingId(null);
      setForm({ question: "", kb_id: "", sort_order: 0 });
      await loadItems();
    } catch (err: any) { setError(err?.message || "保存失败"); }
  };

  const handleDelete = async (id: string, question: string) => {
    if (!confirm(`删除「${question.slice(0, 40)}...」？`)) return;
    await api.delete(`/api/admin/sample-questions/${id}`);
    await loadItems();
  };

  const startEdit = (q: SampleQuestionItem) => {
    setForm({ question: q.question, kb_id: q.kbId || "", sort_order: q.sortOrder });
    setEditingId(q.id);
    setShowForm(true);
  };

  if (loading) return <div className="p-8 text-gray-400">加载中...</div>;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-bold">示例问题管理</h2>
        <button onClick={() => { setForm({ question: "", kb_id: "", sort_order: 0 }); setEditingId(null); setShowForm(true); }}
          className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
          <Plus size={14} /> 新增问题
        </button>
      </div>

      {error && <div className="mb-3 p-2 bg-red-50 text-red-700 text-xs rounded">{error}</div>}

      {showForm && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => setShowForm(false)}>
          <div className="bg-white rounded-xl p-6 w-[480px] shadow-xl" onClick={e => e.stopPropagation()}>
            <h3 className="font-bold mb-4">{editingId ? "编辑问题" : "新增问题"}</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500">问题内容 *</label>
                <textarea value={form.question} onChange={e => setForm({...form, question: e.target.value})}
                  rows={2} placeholder="如：如何创建知识库？" className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-500">关联知识库ID (可选)</label>
                <input value={form.kb_id} onChange={e => setForm({...form, kb_id: e.target.value})}
                  placeholder="留空则全局展示" className="w-full border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-500">排序</label>
                <input type="number" value={form.sort_order} onChange={e => setForm({...form, sort_order: +e.target.value})}
                  className="w-24 border rounded px-2 py-1.5 text-sm" />
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
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500">问题</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500 w-24">知识库</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500 w-16">排序</th>
              <th className="text-left py-2.5 px-4 text-xs font-medium text-gray-500 w-20">状态</th>
              <th className="text-right py-2.5 px-4 text-xs font-medium text-gray-500 w-20">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map(q => (
              <tr key={q.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="py-2.5 px-4">{q.question}</td>
                <td className="py-2.5 px-4 text-xs text-gray-500">{q.kbId || "全局"}</td>
                <td className="py-2.5 px-4 text-xs">{q.sortOrder}</td>
                <td className="py-2.5 px-4 whitespace-nowrap">
                  <span className={`inline-block text-xs px-1.5 py-0.5 rounded whitespace-nowrap ${q.enabled ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-400"}`}>
                    {q.enabled ? "启用" : "禁用"}
                  </span>
                </td>
                <td className="py-2.5 px-4 text-right">
                  <button onClick={() => startEdit(q)} className="p-1 text-gray-400 hover:text-blue-500 rounded"><Edit3 size={14} /></button>
                  <button onClick={() => handleDelete(q.id, q.question)} className="p-1 text-gray-400 hover:text-red-500 rounded"><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={5} className="py-8 text-center text-gray-400">暂无示例问题</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
