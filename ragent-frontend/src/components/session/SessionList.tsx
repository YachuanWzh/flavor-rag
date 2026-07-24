import type { Session } from "@/types";

interface Props {
  sessions: Session[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export default function SessionList({ sessions, currentId, onSelect, onDelete }: Props) {
  if (sessions.length === 0) {
    return <div className="p-4 text-xs text-gray-400">暂无会话</div>;
  }

  return (
    <div className="flex-1 overflow-y-auto">
      {sessions.map((s) => (
        <div
          key={s.id}
          className={`group flex items-center px-3 py-2 cursor-pointer text-sm border-b border-gray-100 ${
            s.id === currentId ? "bg-blue-50 text-blue-700" : "hover:bg-gray-100"
          }`}
          onClick={() => onSelect(s.id)}
        >
          <span className="truncate flex-1">{s.title}</span>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(s.id); }}
            className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 ml-2 text-xs"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
