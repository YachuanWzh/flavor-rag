import { useEffect, useState } from "react";
import { Sliders, RotateCcw, CheckCircle, AlertCircle } from "lucide-react";
import { api } from "@/services/api";

interface HyperParamItem {
  value: string;
  envDefault: string;
  valueType: string;
  customized: boolean;
}

type GroupedParams = Record<string, { label: string; items: [string, HyperParamItem][] }>;

const GROUP_LABELS: Record<string, string> = {
  retrieval_per_channel_top_k: "检索治理",
  retrieval_max_candidates: "检索治理",
  retrieval_final_top_k: "检索治理",
  retrieval_channel_timeout_ms: "检索治理",
  retrieval_total_timeout_ms: "检索治理",
  retrieval_context_max_chars: "检索治理",
  retrieval_context_max_tokens: "检索治理",
  retrieval_min_relevance_score: "检索治理",
  retrieval_rrf_min_score: "检索治理",
  retrieval_vector_min_score: "检索治理",
  retrieval_reranker_min_score: "检索治理",
  retrieval_channel_weights: "检索治理",
  reranker_enabled: "Reranker",
  reranker_timeout_sec: "Reranker",
  query_decomposition_enabled: "查询重写与意图",
  query_decomposition_max_queries: "查询重写与意图",
  rewrite_enabled: "查询重写与意图",
  intent_llm_enabled: "查询重写与意图",
  intent_min_score: "查询重写与意图",
  intent_max_matches: "查询重写与意图",
  hyde_enabled: "HyDE",
  hyde_channel_weight: "HyDE",
  circuit_breaker_failures: "断路器",
  circuit_breaker_recovery_sec: "断路器",
};

const KEY_LABELS: Record<string, string> = {
  retrieval_per_channel_top_k: "每通道召回数",
  retrieval_max_candidates: "最大候选数",
  retrieval_final_top_k: "最终返回条数 (Rerank)",
  retrieval_channel_timeout_ms: "通道超时 (ms)",
  retrieval_total_timeout_ms: "总超时 (ms)",
  retrieval_context_max_chars: "上下文最大字符数",
  retrieval_context_max_tokens: "上下文最大 Token 数",
  retrieval_min_relevance_score: "最低相关性分数",
  retrieval_rrf_min_score: "RRF 最低分",
  retrieval_vector_min_score: "向量最低分",
  retrieval_reranker_min_score: "Reranker 最低分",
  retrieval_channel_weights: "通道权重",
  reranker_enabled: "Reranker 开关",
  reranker_timeout_sec: "Reranker 超时 (秒)",
  query_decomposition_enabled: "查询拆解开关",
  query_decomposition_max_queries: "最大子查询数",
  rewrite_enabled: "查询重写开关",
  intent_llm_enabled: "意图识别开关",
  intent_min_score: "意图最低分",
  intent_max_matches: "意图最多匹配数",
  hyde_enabled: "HyDE 开关",
  hyde_channel_weight: "HyDE 通道权重",
  circuit_breaker_failures: "断路器失败阈值",
  circuit_breaker_recovery_sec: "断路器恢复秒数",
};

function groupParams(params: Record<string, HyperParamItem>): GroupedParams {
  const groups: Record<string, [string, HyperParamItem][]> = {};
  for (const [key, item] of Object.entries(params)) {
    const group = GROUP_LABELS[key] || "其他";
    if (!groups[group]) groups[group] = [];
    groups[group].push([key, item]);
  }
  const labeled: GroupedParams = {};
  for (const [group, items] of Object.entries(groups)) {
    labeled[group] = { label: group, items };
  }
  return labeled;
}

export default function HyperParamsPage() {
  const [params, setParams] = useState<Record<string, HyperParamItem> | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const fetchParams = () => {
    setLoading(true);
    api
      .get("/api/admin/hyperparams")
      .then((data: any) => setParams(data))
      .catch(() => showToast("error", "加载配置失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchParams();
  }, []);

  const showToast = (type: "success" | "error", msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const handleSave = async (key: string, value: string) => {
    setSaving(key);
    try {
      await api.put("/api/admin/hyperparams", { key, value });
      showToast("success", `"${KEY_LABELS[key] || key}" 已保存`);
      // Refresh list to reflect customized status
      fetchParams();
    } catch {
      showToast("error", `保存 "${KEY_LABELS[key] || key}" 失败`);
    } finally {
      setSaving(null);
    }
  };

  const handleReset = async (key: string, envDefault: string) => {
    // Resetting to env default means saving the env default value
    await handleSave(key, envDefault);
  };

  const inputFor = (key: string, item: HyperParamItem, onSave: (v: string) => void) => {
    if (item.valueType === "bool") {
      return (
        <select
          className="border rounded px-2 py-1 text-sm bg-white w-28"
          value={String(item.value === "true" || item.value === "True")}
          onChange={(e) => onSave(e.target.value)}
        >
          <option value="true">启用</option>
          <option value="false">禁用</option>
        </select>
      );
    }
    if (item.valueType === "str") {
      return (
        <input
          className="border rounded px-2 py-1 text-sm bg-white w-48 font-mono"
          type="text"
          defaultValue={item.value}
          onBlur={(e) => {
            if (e.target.value !== item.value) onSave(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.target as HTMLInputElement).value !== item.value) {
              onSave((e.target as HTMLInputElement).value);
            }
          }}
        />
      );
    }
    return (
      <input
        className="border rounded px-2 py-1 text-sm bg-white w-28 font-mono"
        type="number"
        step={item.valueType === "float" ? "0.01" : "1"}
        defaultValue={item.value}
        onBlur={(e) => {
          if (e.target.value !== item.value) onSave(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.target as HTMLInputElement).value !== item.value) {
            onSave((e.target as HTMLInputElement).value);
          }
        }}
      />
    );
  };

  if (loading) {
    return <div className="p-8 text-gray-400">加载中...</div>;
  }

  if (!params) {
    return (
      <div className="p-8">
        <div className="text-red-500 mb-4">加载配置失败</div>
        <button
          className="px-4 py-2 bg-teal-600 text-white rounded text-sm"
          onClick={fetchParams}
        >
          重试
        </button>
      </div>
    );
  }

  const grouped = groupParams(params);

  return (
    <div className="p-6">
      {/* Toast */}
      {toast && (
        <div
          className={`fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-2.5 rounded-lg shadow-lg text-sm text-white ${
            toast.type === "success" ? "bg-green-600" : "bg-red-600"
          }`}
        >
          {toast.type === "success" ? <CheckCircle size={16} /> : <AlertCircle size={16} />}
          {toast.msg}
        </div>
      )}

      <div className="flex items-center gap-3 mb-6">
        <Sliders size={20} className="text-teal-600" />
        <h2 className="text-lg font-bold">参数配置</h2>
        <span className="text-xs text-gray-400">
          修改后即刻生效，无需重启服务
        </span>
      </div>

      <div className="space-y-6">
        {Object.entries(grouped).map(([groupKey, group]) => (
          <div key={groupKey}>
            <h3 className="text-sm font-semibold text-gray-600 mb-3 border-b pb-2">
              {group.label}
            </h3>
            <div className="bg-white rounded-xl border overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider">
                    <th className="text-left px-4 py-2.5 font-medium">参数</th>
                    <th className="text-left px-4 py-2.5 font-medium">Key</th>
                    <th className="text-left px-4 py-2.5 font-medium">当前值</th>
                    <th className="text-left px-4 py-2.5 font-medium">环境变量默认值</th>
                    <th className="text-left px-4 py-2.5 font-medium">状态</th>
                    <th className="text-right px-4 py-2.5 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {group.items.map(([key, item]) => (
                    <tr
                      key={key}
                      className={`border-t ${
                        item.customized ? "bg-yellow-50" : ""
                      } hover:bg-gray-50 transition-colors`}
                    >
                      <td className="px-4 py-2.5 font-medium text-gray-700">
                        {KEY_LABELS[key] || key}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs text-gray-500">
                        {key}
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          {inputFor(key, item, (v) => handleSave(key, v))}
                          {saving === key && (
                            <span className="text-xs text-gray-400 animate-pulse">
                              保存中...
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-gray-400 font-mono text-xs">
                        {item.envDefault}
                        {item.customized && (
                          <button
                            className="ml-2 text-teal-600 hover:text-teal-800 inline-flex items-center gap-1"
                            title="恢复为环境变量默认值"
                            onClick={() => handleReset(key, item.envDefault)}
                          >
                            <RotateCcw size={12} />
                          </button>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        {item.customized ? (
                          <span className="inline-flex items-center gap-1 text-xs text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full">
                            已自定义
                          </span>
                        ) : (
                          <span className="text-xs text-gray-400">默认</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <button
                          className="px-3 py-1 text-xs bg-teal-600 text-white rounded hover:bg-teal-700 disabled:opacity-50"
                          disabled={saving === key}
                          onClick={() => handleSave(key, item.value)}
                        >
                          保存
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
