import { useEffect, useState } from "react";
import { api } from "@/services/api";

interface TraceItem {
  id: string;
  query: string;
  intent: string;
  totalMs: number;
  recallCount: number;
  finalCount: number;
  status: string;
  createTime: string;
}

export default function TracesPage() {
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/api/admin/traces?limit=50")
      .then((data: any) => setTraces(data.items))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-400">加载中...</div>;

  return (
    <div className="p-6">
      <h2 className="text-lg font-bold mb-4">链路追踪</h2>
      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500">查询</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-20">意图</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-16">耗时</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-20">召回/最终</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-16">状态</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-36">时间</th>
            </tr>
          </thead>
          <tbody>
            {traces.map((t) => (
              <tr key={t.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="py-2.5 px-3 truncate max-w-[300px]">{t.query}</td>
                <td className="py-2.5 px-3 text-xs text-gray-500">{t.intent || "—"}</td>
                <td className="py-2.5 px-3 text-xs">{t.totalMs}ms</td>
                <td className="py-2.5 px-3 text-xs">{t.recallCount}/{t.finalCount}</td>
                <td className="py-2.5 px-3">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${t.status === "success" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600"}`}>
                    {t.status}
                  </span>
                </td>
                <td className="py-2.5 px-3 text-xs text-gray-400">{t.createTime?.slice(0, 19) || "—"}</td>
              </tr>
            ))}
            {traces.length === 0 && (
              <tr><td colSpan={6} className="py-8 text-center text-gray-400">暂无追踪记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
