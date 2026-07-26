import { useEffect, useState } from "react";
import { Building2, KeyRound, Plus, ShieldCheck } from "lucide-react";
import { api } from "@/services/api";

interface Department {
  id: string;
  name: string;
  parentId?: string;
}

export default function AccessControlPage() {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentName, setDepartmentName] = useState("");
  const [grant, setGrant] = useState({
    subject_type: "DEPARTMENT",
    subject_id: "",
    resource_type: "KNOWLEDGE_BASE",
    resource_id: "",
    permission: "READ",
  });
  const [message, setMessage] = useState("");

  const load = () =>
    api.get("/api/security/departments").then((data: any) => setDepartments(data));

  useEffect(() => {
    load();
  }, []);

  const createDepartment = async () => {
    if (!departmentName.trim()) return;
    await api.post("/api/security/departments", { name: departmentName.trim() });
    setDepartmentName("");
    load();
  };

  const grantAccess = async () => {
    await api.post("/api/security/acl", grant);
    setMessage("访问权限已保存");
    window.setTimeout(() => setMessage(""), 2400);
  };

  return (
    <div className="p-6">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-700">
        Governance
      </p>
      <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">组织与访问控制</h2>
      <p className="mt-1 text-sm text-slate-500">管理租户内部门，并为知识库或文档授予最小权限。</p>

      <div className="mt-6 grid gap-5 xl:grid-cols-2">
        <section className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-cyan-700" />
            <h3 className="text-sm font-semibold text-slate-900">部门</h3>
          </div>
          <div className="mt-4 flex gap-2">
            <input
              value={departmentName}
              onChange={(event) => setDepartmentName(event.target.value)}
              placeholder="例如：产品研发部"
              className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-cyan-500"
            />
            <button
              type="button"
              onClick={createDepartment}
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-950 px-3 py-2 text-xs font-medium text-white"
            >
              <Plus className="h-3.5 w-3.5" />
              新建
            </button>
          </div>
          <div className="mt-4 divide-y divide-slate-100 rounded-xl border border-slate-200">
            {departments.map((department) => (
              <div key={department.id} className="flex items-center justify-between px-3 py-2.5">
                <span className="text-sm text-slate-700">{department.name}</span>
                <span className="font-mono text-[10px] text-slate-400">{department.id}</span>
              </div>
            ))}
            {!departments.length && (
              <p className="px-3 py-8 text-center text-xs text-slate-400">尚未创建部门</p>
            )}
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-cyan-700" />
            <h3 className="text-sm font-semibold text-slate-900">授权</h3>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <Field label="授权对象">
              <select
                value={grant.subject_type}
                onChange={(event) => setGrant({ ...grant, subject_type: event.target.value })}
                className="input"
              >
                <option value="DEPARTMENT">部门</option>
                <option value="USER">用户</option>
                <option value="ROLE">角色</option>
              </select>
            </Field>
            <Field label="对象 ID">
              <input
                value={grant.subject_id}
                onChange={(event) => setGrant({ ...grant, subject_id: event.target.value })}
                className="input"
              />
            </Field>
            <Field label="资源类型">
              <select
                value={grant.resource_type}
                onChange={(event) => setGrant({ ...grant, resource_type: event.target.value })}
                className="input"
              >
                <option value="KNOWLEDGE_BASE">知识库</option>
                <option value="DOCUMENT">文档</option>
              </select>
            </Field>
            <Field label="资源 ID">
              <input
                value={grant.resource_id}
                onChange={(event) => setGrant({ ...grant, resource_id: event.target.value })}
                className="input"
              />
            </Field>
            <Field label="权限">
              <select
                value={grant.permission}
                onChange={(event) => setGrant({ ...grant, permission: event.target.value })}
                className="input"
              >
                <option value="READ">读取</option>
                <option value="WRITE">编辑</option>
                <option value="ADMIN">管理</option>
              </select>
            </Field>
          </div>
          <button
            type="button"
            onClick={grantAccess}
            disabled={!grant.subject_id || !grant.resource_id}
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-cyan-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            <KeyRound className="h-4 w-4" />
            保存授权
          </button>
          {message && <p className="mt-3 text-xs text-emerald-700">{message}</p>}
        </section>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label>
      <span className="text-xs font-medium text-slate-500">{label}</span>
      <div className="mt-1.5 [&_.input]:w-full [&_.input]:rounded-lg [&_.input]:border [&_.input]:border-slate-200 [&_.input]:bg-white [&_.input]:px-3 [&_.input]:py-2 [&_.input]:text-sm [&_.input]:outline-none focus-within:[&_.input]:border-cyan-500">
        {children}
      </div>
    </label>
  );
}
