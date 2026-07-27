import { useEffect, useState } from "react";
import { Clock, Play, Pause, Trash2, RefreshCw } from "lucide-react";
import { api } from "@/services/api";

interface ScheduleItem {
  id: string;
  docId: string;
  kbId: string;
  cronExpr: string;
  enabled: boolean;
  nextRunTime: string;
  lastRunTime: string;
  lastSuccessTime: string;
  lastStatus: string;
  lastError: string;
}

interface ExecutionItem {
  id: string;
  scheduleId: string;
  docId: string;
  status: string;
  message: string;
  startTime: string;
  endTime: string;
}

export default function SchedulePage() {
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [executions, setExecutions] = useState<ExecutionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"schedules" | "executions">("schedules");

  const fetchSchedules = () => {
    setLoading(true);
    api.get("/api/admin/schedule/list?pageSize=50")
      .then((data: any) => setSchedules(data.rows))
      .finally(() => setLoading(false));
  };

  const fetchExecutions = () => {
    setLoading(true);
    api.get("/api/admin/schedule/executions?pageSize=50")
      .then((data: any) => setExecutions(data.rows))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    tab === "schedules" ? fetchSchedules() : fetchExecutions();
  }, [tab]);

  const toggleSchedule = (id: string, enabled: boolean) => {
    api.put(`/api/admin/schedule/${id}`, { enabled: !enabled })
      .then(() => fetchSchedules());
  };

  const deleteSchedule = (id: string) => {
    if (!confirm("确定删除此调度配置？")) return;
    api.delete(`/api/admin/schedule/${id}`)
      .then(() => fetchSchedules());
  };

  return (
    <div className="p-6">
      <h2 className="text-lg font-bold mb-4">文档定时刷新调度</h2>

      {/* Tabs */}
      <div className="flex gap-4 mb-4 border-b">
        <button
          onClick={() => setTab("schedules")}
          className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "schedules" ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >调度列表</button>
        <button
          onClick={() => setTab("executions")}
          className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "executions" ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >执行历史</button>
      </div>

      {loading ? (
        <div className="text-gray-400 py-8">加载中...</div>
      ) : tab === "schedules" ? (
        <div className="bg-white rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500">文档ID</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-20">间隔(秒)</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-20">状态</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-36">下次执行</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-36">上次执行</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-20">结果</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-24">操作</th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="py-2.5 px-3 text-xs font-mono">{s.docId}</td>
                  <td className="py-2.5 px-3 text-xs">{s.cronExpr || "—"}</td>
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    <span className={`inline-block text-xs px-1.5 py-0.5 rounded whitespace-nowrap ${
                      s.enabled ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"
                    }`}>{s.enabled ? "启用" : "停用"}</span>
                  </td>
                  <td className="py-2.5 px-3 text-xs text-gray-400">{s.nextRunTime?.slice(0, 19) || "—"}</td>
                  <td className="py-2.5 px-3 text-xs text-gray-400">{s.lastRunTime?.slice(0, 19) || "—"}</td>
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    <span className={`inline-block text-xs px-1.5 py-0.5 rounded whitespace-nowrap ${
                      s.lastStatus === "success" ? "bg-green-50 text-green-700" :
                      s.lastStatus === "error" ? "bg-red-50 text-red-600" :
                      "bg-gray-100 text-gray-500"
                    }`}>{s.lastStatus || "—"}</span>
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex gap-1">
                      <button
                        onClick={() => toggleSchedule(s.id, s.enabled)}
                        title={s.enabled ? "停用" : "启用"}
                        className="p-1 hover:bg-gray-100 rounded"
                      >
                        {s.enabled ? <Pause size={14} /> : <Play size={14} />}
                      </button>
                      <button
                        onClick={() => deleteSchedule(s.id)}
                        title="删除"
                        className="p-1 hover:bg-red-50 text-red-500 rounded"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {schedules.length === 0 && (
                <tr><td colSpan={7} className="py-8 text-center text-gray-400">暂无调度配置</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="bg-white rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-36">开始时间</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-36">结束时间</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500">文档ID</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-20">状态</th>
                <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500">详情</th>
              </tr>
            </thead>
            <tbody>
              {executions.map((e) => (
                <tr key={e.id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="py-2.5 px-3 text-xs text-gray-400">{e.startTime?.slice(0, 19) || "—"}</td>
                  <td className="py-2.5 px-3 text-xs text-gray-400">{e.endTime?.slice(0, 19) || "—"}</td>
                  <td className="py-2.5 px-3 text-xs font-mono">{e.docId}</td>
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    <span className={`inline-block text-xs px-1.5 py-0.5 rounded whitespace-nowrap ${
                      e.status === "success" ? "bg-green-50 text-green-700" :
                      e.status === "error" ? "bg-red-50 text-red-600" :
                      "bg-gray-100 text-gray-500"
                    }`}>{e.status}</span>
                  </td>
                  <td className="py-2.5 px-3 text-xs text-gray-500 truncate max-w-[200px]">
                    {e.message || "—"}
                  </td>
                </tr>
              ))}
              {executions.length === 0 && (
                <tr><td colSpan={5} className="py-8 text-center text-gray-400">暂无执行记录</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
