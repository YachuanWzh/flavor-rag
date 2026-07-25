import { useEffect, useState } from "react";
import { ClipboardList } from "lucide-react";
import { api } from "@/services/api";

interface AuditItem {
  id: string;
  bizType: string;
  bizId: string;
  operationType: string;
  actionDesc: string;
  operatorName: string;
  operatorRole: string;
  success: boolean;
  ip: string;
  createTime: string;
}

const BIZ_TYPE_LABEL: Record<string, string> = {
  knowledge_base: "知识库",
  knowledge_document: "文档",
  intent_node: "意图节点",
  sample_question: "示例问题",
  query_term_mapping: "查询映射",
};

const OP_TYPE_LABEL: Record<string, string> = {
  CREATE: "创建",
  UPDATE: "修改",
  DELETE: "删除",
  ENABLE: "启用",
  DISABLE: "禁用",
};

export default function AuditLogPage() {
  const [rows, setRows] = useState<AuditItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [filterBizType, setFilterBizType] = useState("");
  const pageSize = 20;

  const fetchLogs = (p: number) => {
    setLoading(true);
    const params = new URLSearchParams({ page: String(p), pageSize: String(pageSize) });
    if (filterBizType) params.set("biz_type", filterBizType);
    api.get(`/api/admin/audit/logs?${params}`)
      .then((data: any) => {
        setRows(data.rows);
        setTotal(data.total);
        setPage(data.page);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchLogs(1); }, [filterBizType]);

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold">审计日志</h2>
        <select
          value={filterBizType}
          onChange={(e) => setFilterBizType(e.target.value)}
          className="text-sm border rounded-lg px-3 py-1.5 bg-white"
        >
          <option value="">全部类型</option>
          {Object.entries(BIZ_TYPE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </div>

      <div className="bg-white rounded-xl border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-32">时间</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-16">类型</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-16">操作</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500">操作描述</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-20">操作人</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-12">结果</th>
              <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 w-28">IP</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="py-2.5 px-3 text-xs text-gray-400 whitespace-nowrap">
                  {r.createTime?.slice(0, 19) || "—"}
                </td>
                <td className="py-2.5 px-3 text-xs">
                  {BIZ_TYPE_LABEL[r.bizType] || r.bizType}
                </td>
                <td className="py-2.5 px-3 text-xs">
                  <span className={`px-1.5 py-0.5 rounded text-xs ${
                    r.operationType === "DELETE" ? "bg-red-50 text-red-700" :
                    r.operationType === "CREATE" ? "bg-green-50 text-green-700" :
                    "bg-blue-50 text-blue-700"
                  }`}>
                    {OP_TYPE_LABEL[r.operationType] || r.operationType}
                  </span>
                </td>
                <td className="py-2.5 px-3 text-xs truncate max-w-[300px]">
                  {r.actionDesc || "—"}
                </td>
                <td className="py-2.5 px-3 text-xs text-gray-600">
                  {r.operatorName || "—"}
                  {r.operatorRole && (
                    <span className="ml-1 text-gray-400">({r.operatorRole})</span>
                  )}
                </td>
                <td className="py-2.5 px-3">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    r.success ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600"
                  }`}>
                    {r.success ? "成功" : "失败"}
                  </span>
                </td>
                <td className="py-2.5 px-3 text-xs text-gray-400 font-mono">{r.ip || "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && !loading && (
              <tr><td colSpan={7} className="py-8 text-center text-gray-400">暂无审计记录</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > pageSize && (
        <div className="flex items-center justify-between mt-4 text-sm">
          <span className="text-gray-500">共 {total} 条</span>
          <div className="flex gap-2">
            <button
              disabled={page <= 1}
              onClick={() => fetchLogs(page - 1)}
              className="px-3 py-1 border rounded disabled:opacity-30 hover:bg-gray-50"
            >上一页</button>
            <span className="px-2 py-1 text-gray-600">第 {page} 页</span>
            <button
              disabled={page * pageSize >= total}
              onClick={() => fetchLogs(page + 1)}
              className="px-3 py-1 border rounded disabled:opacity-30 hover:bg-gray-50"
            >下一页</button>
          </div>
        </div>
      )}
    </div>
  );
}
