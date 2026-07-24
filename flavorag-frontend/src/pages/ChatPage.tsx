import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import { getCurrentUser } from "@/services/authService";
import { fetchSessions, createSession, deleteSession } from "@/services/sessionService";
import { fetchKnowledgeBases } from "@/services/knowledgeService";
import type { KnowledgeBase, SourceRef } from "@/types";
import SessionList from "@/components/session/SessionList";
import ChatInput from "@/components/chat/ChatInput";
import MessageList from "@/components/chat/MessageList";
import SourcesDrawer from "@/components/chat/SourcesDrawer";

export default function ChatPage() {
  const navigate = useNavigate();
  const { logout, setUser } = useAuthStore();
  const {
    sessions, setSessions, currentSessionId, setCurrentSession,
    selectedKbId, setSelectedKbId,
    messages, isLoading, isStreaming, streamingMessageId,
    sendMessage, cancelGeneration,
    addSession, removeSession,
  } = useChatStore();
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [initLoading, setInitLoading] = useState(true);

  // Sources drawer state
  const [drawerSources, setDrawerSources] = useState<SourceRef[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { navigate("/login"); return; }
    Promise.all([getCurrentUser(), fetchSessions(), fetchKnowledgeBases()])
      .then(([user, sess, kbList]) => {
        setUser(user);
        setSessions(sess);
        setKbs(kbList);
        if (sess.length > 0) setCurrentSession(sess[0].id);
        if (kbList.length > 0) setSelectedKbId(kbList[0].id);
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

  const handleViewSources = (sources: SourceRef[]) => {
    setDrawerSources(sources);
    setDrawerOpen(true);
  };

  if (initLoading) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">加载中...</div>;
  }

  return (
    <div className="h-screen flex bg-white overflow-hidden">
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

        {/* KB Selector */}
        <div className="px-3 py-2 border-t">
          <label className="text-xs text-gray-500 block mb-1">检索知识库</label>
          <select
            value={selectedKbId || ""}
            onChange={(e) => setSelectedKbId(e.target.value || null)}
            className="w-full text-xs border rounded px-2 py-1.5 bg-white"
          >
            <option value="">— 不检索 —</option>
            {kbs.map((kb) => (
              <option key={kb.id} value={kb.id}>{kb.name}</option>
            ))}
          </select>
          {kbs.length === 0 && (
            <p className="text-xs text-gray-400 mt-1">
              暂无知识库，<a href="/knowledge" className="text-blue-500 underline">去创建</a>
            </p>
          )}
        </div>

        <div className="p-3 border-t mt-auto space-y-2">
          <button
            onClick={() => navigate("/knowledge")}
            className="block w-full text-left text-xs text-gray-500 hover:text-blue-600"
          >
            知识库管理
          </button>
          <button
            onClick={() => navigate("/admin")}
            className="block w-full text-left text-xs text-gray-500 hover:text-blue-600"
          >
            管理后台
          </button>
          <button
            onClick={() => { logout(); navigate("/login"); }}
            className="block w-full text-left text-xs text-gray-500 hover:text-red-500"
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
            <MessageList
              messages={messages}
              isStreaming={isStreaming}
              streamingMessageId={streamingMessageId}
              onViewSources={handleViewSources}
            />
          )}
        </div>
        <ChatInput
          onSend={handleSend}
          onCancel={cancelGeneration}
          isStreaming={isStreaming}
        />
      </main>

      {/* Sources Drawer */}
      <SourcesDrawer
        open={drawerOpen}
        sources={drawerSources}
        onClose={() => setDrawerOpen(false)}
      />
    </div>
  );
}
