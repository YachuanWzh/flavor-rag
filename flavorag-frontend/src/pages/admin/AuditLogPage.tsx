import { useEffect, useState } from "react";
import { ClipboardList, ChevronLeft, ChevronRight, Filter } from "lucide-react";
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
  knowledge_chunk: "切片",
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

const OP_TYPE_STYLE: Record<string, string> = {
  CREATE: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  DELETE: "bg-red-50 text-red-700 ring-red-600/20",
  UPDATE: "bg-blue-50 text-blue-700 ring-blue-600/20",
  ENABLE: "bg-teal-50 text-teal-700 ring-teal-600/20",
  DISABLE: "bg-amber-50 text-amber-700 ring-amber-600/20",
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

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
            <ClipboardList size={18} />
          </span>
          <div>
            <h2 className="text-base font-semibold text-gray-900">审计日志</h2>
            <p className="text-xs text-gray-500 mt-0.5">记录知识库与文档的关键变更操作</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Filter size={14} className="text-gray-400" />
          <select
            value={filterBizType}
            onChange={(e) => setFilterBizType(e.target.value)}
            className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400 transition"
          >
            <option value="">全部类型</option>
            {Object.entries(BIZ_TYPE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <table className="w-full text-sm table-fixed">
          <thead>
            <tr className="bg-gray-50/80 border-b border-gray-100">
              <th className="text-left py-3 px-3 text-xs font-semibold text-gray-500 uppercase tracking-wide w-[150px]">时间</th>
              <th className="text-center py-3 px-3 text-xs font-semibold text-gray-500 uppercase tracking-wide w-[72px]">类型</th>
              <th className="text-center py-3 px-3 text-xs font-semibold text-gray-500 uppercase tracking-wide w-[64px]">操作</th>
              <th className="text-left py-3 px-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">操作描述</th>
              <th className="text-left py-3 px-3 text-xs font-semibold text-gray-500 uppercase tracking-wide w-[90px]">操作人</th>
              <th className="text-center py-3 px-3 text-xs font-semibold text-gray-500 uppercase tracking-wide w-[72px]">结果</th>
              <th className="text-left py-3 px-3 text-xs font-semibold text-gray-500 uppercase tracking-wide w-[100px]">IP</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {loading ? (
              <tr>
                <td colSpan={7} className="py-16 text-center">
                  <div className="flex flex-col items-center gap-2 text-gray-400">
                    <span className="h-5 w-5 animate-spin rounded-full border-2 border-gray-300 border-t-indigo-500" />
                    <span className="text-xs">加载中...</span>
                  </div>
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-16 text-center">
                  <div className="flex flex-col items-center gap-2 text-gray-400">
                    <ClipboardList size={32} className="text-gray-200" />
                    <span className="text-sm">暂无审计记录</span>
                    <span className="text-xs text-gray-300">创建、删除知识库或修改切片状态后会在此显示</span>
                  </div>
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id} className="hover:bg-indigo-50/30 transition-colors">
                  <td className="py-3 px-3 text-xs text-gray-500 whitespace-nowrap font-mono tabular-nums">
                    {r.createTime?.slice(0, 19) || "—"}
                  </td>
                  <td className="py-3 px-3 text-center whitespace-nowrap">
                    <span className="inline-flex items-center justify-center rounded-md bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                      {BIZ_TYPE_LABEL[r.bizType] || r.bizType}
                    </span>
                  </td>
                  <td className="py-3 px-3 text-center whitespace-nowrap">
                    <span className={`inline-flex items-center justify-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                      OP_TYPE_STYLE[r.operationType] || "bg-gray-50 text-gray-600 ring-gray-500/20"
                    }`}>
                      {OP_TYPE_LABEL[r.operationType] || r.operationType}
                    </span>
                  </td>
                  <td className="py-3 px-3 text-xs text-gray-700 truncate" title={r.actionDesc}>
                    {r.actionDesc || "—"}
                  </td>
                  <td className="py-3 px-3 text-xs whitespace-nowrap truncate">
                    <span className="font-medium text-gray-700">{r.operatorName || "—"}</span>
                    {r.operatorRole && (
                      <span className="ml-0.5 text-gray-400">({r.operatorRole})</span>
                    )}
                  </td>
                  <td className="py-3 px-3 text-center whitespace-nowrap">
                    <span className={`inline-flex items-center justify-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                      r.success
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-red-50 text-red-600"
                    }`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${r.success ? "bg-emerald-500" : "bg-red-500"}`} />
                      {r.success ? "成功" : "失败"}
                    </span>
                  </td>
                  <td className="py-3 px-3 text-xs text-gray-400 font-mono whitespace-nowrap">{r.ip || "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > pageSize && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">
            共 <span className="font-medium text-gray-700">{total}</span> 条记录
          </span>
          <div className="flex items-center gap-1.5">
            <button
              disabled={page <= 1}
              onClick={() => fetchLogs(page - 1)}
              className="inline-flex items-center gap-1 px-3 py-1.5 border border-gray-200 rounded-lg text-gray-600 shadow-sm disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 transition"
            >
              <ChevronLeft size={14} /> 上一页
            </button>
            <span className="px-3 py-1.5 text-gray-600 tabular-nums">
              {page} / {totalPages}
            </span>
            <button
              disabled={page >= totalPages}
              onClick={() => fetchLogs(page + 1)}
              className="inline-flex items-center gap-1 px-3 py-1.5 border border-gray-200 rounded-lg text-gray-600 shadow-sm disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 transition"
            >
              下一页 <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
