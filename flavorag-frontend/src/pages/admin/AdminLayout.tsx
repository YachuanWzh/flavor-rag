import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { ReactElement } from "react";
import {
  LayoutDashboard, HelpCircle, ArrowLeftRight,
  Activity, ArrowLeft, GitBranch, ClipboardList,
  Clock, Layers,
} from "lucide-react";

function HeartPulseIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" />
      <path d="M12 5 9.04 7.96a2.17 2.17 0 0 0 0 3.08c.82.82 2.13.85 2.96 0V5Z" />
      <path d="M12 5l2.96 2.96a2.17 2.17 0 0 1 0 3.08c-.82.82-2.13.85-2.96 0V5Z" />
    </svg>
  );
}

const navItems = [
  { to: "/admin", end: true, Icon: LayoutDashboard, label: "仪表板" },
  { to: "/admin/intent-tree", Icon: GitBranch, label: "意图树" },
  { to: "/admin/sample-questions", Icon: HelpCircle, label: "示例问题" },
  { to: "/admin/query-mapping", Icon: ArrowLeftRight, label: "查询词映射" },
  { to: "/admin/traces", Icon: Activity, label: "链路追踪" },
  { to: "/admin/health", Icon: HeartPulseIcon, label: "系统状态" },
  { to: "/admin/audit", Icon: ClipboardList, label: "审计日志" },
  { to: "/admin/schedule", Icon: Clock, label: "定时调度" },
  { to: "/admin/ingestion", Icon: Layers, label: "入库流水线" },
];

export default function AdminLayout() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen flex bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r flex flex-col shrink-0">
        <div className="p-4 border-b">
          <div className="flex items-center gap-2">
            <button onClick={() => navigate("/chat")} className="p-1 hover:bg-gray-100 rounded" title="返回对话">
              <ArrowLeft size={16} />
            </button>
            <h1 className="font-bold text-sm">管理后台</h1>
          </div>
        </div>
        <nav className="flex-1 py-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors ${
                  isActive
                    ? "bg-blue-50 text-blue-700 font-medium border-r-2 border-blue-600"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`
              }
            >
              <item.Icon size={16} />
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
