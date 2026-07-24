import { useEffect, useState } from "react";
import { api } from "@/services/api";

interface HealthData {
  status: string;
  components: Record<string, string>;
}

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

export default function AdminPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [tab, setTab] = useState<"health" | "traces">("health");

  useEffect(() => {
    api.get("/api/admin/health").then(({ data }) => setHealth(data.data));
    api.get("/api/admin/traces?limit=10").then(({ data }) => setTraces(data.data.items));
  }, []);

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-xl font-bold mb-4">管理后台</h1>
      <div className="flex gap-2 mb-4">
        <button onClick={() => setTab("health")} className={`px-3 py-1 rounded text-sm ${tab === "health" ? "bg-blue-600 text-white" : "bg-gray-100"}`}>系统状态</button>
        <button onClick={() => setTab("traces")} className={`px-3 py-1 rounded text-sm ${tab === "traces" ? "bg-blue-600 text-white" : "bg-gray-100"}`}>链路追踪</button>
      </div>

      {tab === "health" && health && (
        <div className="space-y-2">
          <div className={`text-lg font-semibold ${health.status === "healthy" ? "text-green-600" : "text-yellow-600"}`}>
            {health.status === "healthy" ? "系统正常" : "部分降级"}
          </div>
          {Object.entries(health.components).map(([k, v]) => (
            <div key={k} className="flex justify-between py-1 px-3 bg-gray-50 rounded">
              <span className="font-medium">{k}</span>
              <span className={v === "ok" ? "text-green-600" : v === "error" ? "text-red-500" : "text-gray-400"}>
                {v}
              </span>
            </div>
          ))}
        </div>
      )}

      {tab === "traces" && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b text-left">
                <th className="py-2 px-2">查询</th>
                <th className="py-2 px-2">意图</th>
                <th className="py-2 px-2">耗时</th>
                <th className="py-2 px-2">召回/最终</th>
                <th className="py-2 px-2">状态</th>
              </tr>
            </thead>
            <tbody>
              {traces.map((t) => (
                <tr key={t.id} className="border-b hover:bg-gray-50">
                  <td className="py-2 px-2 truncate max-w-[200px]">{t.query}</td>
                  <td className="py-2 px-2 text-xs text-gray-500">{t.intent}</td>
                  <td className="py-2 px-2 text-xs">{t.totalMs}ms</td>
                  <td className="py-2 px-2 text-xs">{t.recallCount}/{t.finalCount}</td>
                  <td className={`py-2 px-2 text-xs ${t.status === "success" ? "text-green-600" : "text-red-500"}`}>{t.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
