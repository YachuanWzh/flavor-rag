import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

const EVENT_NAME = "app:forbidden";

/** Fire a global 403 toast from anywhere (including axios interceptors). */
export function notifyForbidden(message = "权限不足，无法执行此操作") {
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: message }));
}

export default function ForbiddenToast() {
  const [visible, setVisible] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const handler = (e: Event) => {
      setMsg((e as CustomEvent).detail || "权限不足");
      setVisible(true);
      clearTimeout(timer);
      timer = setTimeout(() => setVisible(false), 4000);
    };
    window.addEventListener(EVENT_NAME, handler);
    return () => {
      window.removeEventListener(EVENT_NAME, handler);
      clearTimeout(timer);
    };
  }, []);

  if (!visible) return null;

  return (
    <div className="fixed top-5 left-1/2 z-[9999] -translate-x-1/2 animate-[fadeIn_0.2s_ease]">
      <div className="flex items-center gap-2.5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 shadow-lg">
        <ShieldAlert size={18} className="shrink-0 text-red-500" />
        <span className="text-sm font-medium text-red-700">{msg}</span>
        <button
          onClick={() => setVisible(false)}
          className="ml-2 text-red-400 hover:text-red-600"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
