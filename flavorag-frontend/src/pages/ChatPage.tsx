import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import { getCurrentUser } from "@/services/authService";
import { fetchSessions, createSession, deleteSession } from "@/services/sessionService";
import SessionList from "@/components/session/SessionList";
import ChatInput from "@/components/chat/ChatInput";
import MessageList from "@/components/chat/MessageList";

export default function ChatPage() {
  const navigate = useNavigate();
  const { logout, setUser } = useAuthStore();
  const {
    sessions, setSessions, currentSessionId, setCurrentSession,
    messages, isLoading, isStreaming, sendMessage, cancelGeneration,
    addSession, removeSession,
  } = useChatStore();
  const [initLoading, setInitLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { navigate("/login"); return; }
    Promise.all([getCurrentUser(), fetchSessions()])
      .then(([user, sess]) => {
        setUser(user);
        setSessions(sess);
        if (sess.length > 0) setCurrentSession(sess[0].id);
        setInitLoading(false);
      })
      .catch(() => { logout(); navigate("/login"); });
  }, []);

  const handleNewSession = async () => {
    try {
      const s = await createSession();
      addSession(s);
      setCurrentSession(s.id);
    } catch {}
  };

  const handleDeleteSession = async (id: string) => {
    try {
      await deleteSession(id);
      removeSession(id);
    } catch {}
  };

  const handleSend = (text: string) => {
    sendMessage(text);
  };

  if (initLoading) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">加载中...</div>;
  }

  return (
    <div className="min-h-screen flex bg-white">
      {/* Sidebar */}
      <aside className="w-64 border-r flex flex-col bg-gray-50">
        <div className="p-3 border-b flex items-center justify-between">
          <h1 className="font-bold text-sm">RAG 智能问答</h1>
          <button
            onClick={handleNewSession}
            className="text-xs px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            + 新对话
          </button>
        </div>
        <SessionList
          sessions={sessions}
          currentId={currentSessionId}
          onSelect={setCurrentSession}
          onDelete={handleDeleteSession}
        />
        <div className="p-3 border-t mt-auto">
          <button
            onClick={() => { logout(); navigate("/login"); }}
            className="text-xs text-gray-500 hover:text-red-500"
          >
            退出登录
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col min-w-0">
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm">
              输入问题开始对话
            </div>
          ) : (
            <MessageList messages={messages} />
          )}
        </div>
        <ChatInput
          onSend={handleSend}
          onCancel={cancelGeneration}
          isStreaming={isStreaming}
        />
      </main>
    </div>
  );
}
