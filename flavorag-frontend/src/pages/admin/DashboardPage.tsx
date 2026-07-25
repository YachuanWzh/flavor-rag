import { useEffect, useState } from "react";
import { Database, FileText, MessageSquare, BarChart3, Clock, Search, Activity, Sparkles } from "lucide-react";
import { api } from "@/services/api";

interface DashboardStats {
  knowledgeBases: number;
  documents: number;
  chunks: number;
  conversations: number;
  messages: number;
  todayQuestions: number;
  todayTraces: number;
  avgSearchMs: number;
  avgLlmMs: number;
  avgTotalMs: number;
  positiveFeedback: number;
  negativeFeedback: number;
}

function ThumbsUpIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M7 10v12" />
      <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3.13 3.13 0 0 1 3 3.88Z" />
    </svg>
  );
}

function ThumbsDownIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 14V2" />
      <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3.13 3.13 0 0 1-3-3.88Z" />
    </svg>
  );
}

const Card = ({ icon: Icon, label, value, color }: {
  icon: any; label: string; value: number | string; color: string;
}) => (
  <div className="bg-white rounded-xl border p-5 hover:shadow-sm transition-shadow">
    <div className="flex items-center justify-between mb-2">
      <span className="text-xs text-gray-500">{label}</span>
      <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${color}`}>
        <Icon size={18} className="text-white" />
      </div>
    </div>
    <div className="text-2xl font-bold text-gray-900">{value}</div>
  </div>
);

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/api/admin/dashboard")
      .then((data: any) => setStats(data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="p-8 text-gray-400">加载中...</div>;
  }

  if (!stats) {
    return <div className="p-8 text-red-500">加载失败</div>;
  }

  return (
    <div className="p-6">
      <h2 className="text-lg font-bold mb-6">仪表板概览</h2>

      {/* Resource stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <Card icon={Database} label="知识库" value={stats.knowledgeBases} color="bg-blue-500" />
        <Card icon={FileText} label="文档" value={stats.documents} color="bg-green-500" />
        <Card icon={FileText} label="分块" value={stats.chunks} color="bg-teal-500" />
        <Card icon={MessageSquare} label="会话" value={stats.conversations} color="bg-purple-500" />
      </div>

      {/* Today's stats */}
      <h3 className="text-sm font-semibold text-gray-600 mb-3">今日数据</h3>
      <div className="grid grid-cols-4 gap-4 mb-6">
        <Card icon={MessageSquare} label="今日提问" value={stats.todayQuestions} color="bg-indigo-500" />
        <Card icon={Activity} label="今日Trace" value={stats.todayTraces} color="bg-orange-500" />
        <Card icon={ThumbsUpIcon} label="正向反馈" value={stats.positiveFeedback} color="bg-green-500" />
        <Card icon={ThumbsDownIcon} label="负向反馈" value={stats.negativeFeedback} color="bg-red-500" />
      </div>

      {/* Performance stats */}
      <h3 className="text-sm font-semibold text-gray-600 mb-3">检索性能 (近100次平均)</h3>
      <div className="grid grid-cols-3 gap-4">
        <Card icon={Search} label="平均检索耗时" value={`${stats.avgSearchMs} ms`} color="bg-cyan-500" />
        <Card icon={Sparkles} label="平均LLM耗时" value={`${stats.avgLlmMs} ms`} color="bg-violet-500" />
        <Card icon={BarChart3} label="平均总耗时" value={`${stats.avgTotalMs} ms`} color="bg-rose-500" />
      </div>
    </div>
  );
}
