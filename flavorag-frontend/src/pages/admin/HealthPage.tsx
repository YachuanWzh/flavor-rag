import { useEffect, useState } from "react";
import { api } from "@/services/api";

interface HealthData {
  status: string;
  components: Record<string, string>;
}

export default function HealthPage() {
  const [health, setHealth] = useState<HealthData | null>(null);

  useEffect(() => {
    api.get("/api/admin/health").then((data: any) => setHealth(data));
  }, []);

  if (!health) return <div className="p-8 text-gray-400">加载中...</div>;

  return (
    <div className="p-6">
      <h2 className="text-lg font-bold mb-4">系统状态</h2>
      <div className={`px-4 py-2 mb-4 rounded-lg text-sm font-medium inline-block ${
        health.status === "healthy" ? "bg-green-50 text-green-700" : "bg-yellow-50 text-yellow-700"
      }`}>
        {health.status === "healthy" ? "系统正常" : "部分降级"}
      </div>

      <div className="bg-white rounded-xl border overflow-hidden max-w-lg">
        {Object.entries(health.components).map(([key, value]) => (
          <div key={key} className="flex justify-between items-center px-4 py-3 border-b last:border-0">
            <span className="text-sm font-medium text-gray-700 capitalize">{key}</span>
            <span className={`text-xs px-2 py-1 rounded font-mono ${
              value === "ok" ? "bg-green-50 text-green-700" :
              value === "error" ? "bg-red-50 text-red-600" :
              "bg-gray-100 text-gray-400"
            }`}>
              {value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
