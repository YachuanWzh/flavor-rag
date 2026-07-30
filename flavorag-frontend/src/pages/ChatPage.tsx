import { lazy, Suspense, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import { getCurrentUser } from "@/services/authService";
import { fetchSessions, createSession, deleteSession } from "@/services/sessionService";
import { fetchKnowledgeBases } from "@/services/knowledgeService";
import { fetchRagCapabilities } from "@/services/graphService";
import type { KnowledgeBase, SourceRef } from "@/types";
import SessionList from "@/components/session/SessionList";
import ChatInput from "@/components/chat/ChatInput";
import MessageList from "@/components/chat/MessageList";
import SourcesDrawer from "@/components/chat/SourcesDrawer";
import KnowledgeGraphPanel from "@/components/chat/KnowledgeGraphPanel";
import InterviewExperience from "@/features/interview/InterviewExperience";
import { Mic2, Sparkles } from "lucide-react";

const DocumentPreviewModal = lazy(
  () => import("@/components/chat/DocumentPreviewModal"),
);

export default function ChatPage() {
  const navigate = useNavigate();
  const { logout, setUser } = useAuthStore();
  const {
    sessions, setSessions, currentSessionId, setCurrentSession,
    selectedKbId, setSelectedKbId,
    messages, isLoading, isStreaming, streamingMessageId,
    sendMessage, cancelGeneration,
    addSession, removeSession,
    deepThinkingEnabled, setDeepThinking,
    agenticRagEnabled, setAgenticRag,
    graphRagEnabled, setGraphRag, neighborExpansionEnabled, setNeighborExpansion, graphRevision, graphFocusQuery,
    hydeEnabled, setHyde,
    progressMessage,
  } = useChatStore();
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [initLoading, setInitLoading] = useState(true);
  const [interviewMode, setInterviewMode] = useState(false);

  // Sources drawer state
  const [drawerSources, setDrawerSources] = useState<SourceRef[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerHighlight, setDrawerHighlight] = useState<number | null>(null);
  // Direct PDF preview state (citation click)
  const [previewSource, setPreviewSource] = useState<SourceRef | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { navigate("/login"); return; }
    Promise.all([getCurrentUser(), fetchSessions(), fetchKnowledgeBases()])
      .then(([user, sess, kbList]) => {
        setUser(user);
        setSessions(sess);
        setKbs(kbList);
        if (sess.length > 0) setCurrentSession(sess[0].id);
        if (kbList.length > 0) setSelectedKbId("*");
        setInitLoading(false);
        fetchRagCapabilities()
          .then((capabilities) => {
            setAgenticRag(capabilities.agenticRag.defaultEnabled);
            setGraphRag(
              capabilities.graphRag.available &&
              capabilities.graphRag.defaultEnabled
            );
            if (capabilities.hyde) {
              setHyde(
                capabilities.hyde.available &&
                capabilities.hyde.defaultEnabled
              );
            }
          })
          .catch(() => {
            // Capability discovery is advisory; per-request toggles still work.
          });
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

  const handleViewSources = (sources: SourceRef[], highlightIndex?: number) => {
    // 点击具体引用编号时，直接弹出 PDF 预览
    if (highlightIndex != null && sources[highlightIndex]?.documentId) {
      setPreviewSource(sources[highlightIndex]);
      return;
    }
    // 否则打开来源抽屉
    setDrawerSources(sources);
    setDrawerHighlight(highlightIndex ?? null);
    setDrawerOpen(true);
  };

  if (initLoading) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">加载中...</div>;
  }

  return (
    <div className="h-screen flex bg-white overflow-hidden">
      {/* Sidebar */}
      <aside className="hidden w-64 flex-col border-r bg-gray-50 md:flex">
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
            <option value="*">全部知识库</option>
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
            onClick={() => setInterviewMode(true)}
            className="flex w-full items-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-left text-xs font-medium text-white hover:bg-slate-800"
          >
            <Mic2 className="h-3.5 w-3.5 text-cyan-300" />
            模拟面试
          </button>
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

      <div className="flex min-w-0 flex-1 flex-col lg:flex-row">
        {/* Main */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="hidden h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5 md:flex">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-cyan-700">
                Evidence workspace
              </p>
              <p className="text-sm font-semibold text-slate-900">
                {interviewMode ? "面试模式" : "智能对话"}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setInterviewMode((enabled) => !enabled)}
              className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-2 text-xs font-semibold transition ${
                interviewMode
                  ? "bg-cyan-50 text-cyan-800 ring-1 ring-cyan-200"
                  : "bg-slate-950 text-white hover:bg-slate-800"
              }`}
            >
              {interviewMode ? <Sparkles className="h-4 w-4" /> : <Mic2 className="h-4 w-4 text-cyan-300" />}
              {interviewMode ? "退出面试模式" : "开启面试模式"}
            </button>
          </div>
          <div className="flex h-14 shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 md:hidden">
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-cyan-700">
                RAG workspace
              </p>
              <select
                value={selectedKbId || ""}
                onChange={(event) => setSelectedKbId(event.target.value || null)}
                aria-label="检索知识库"
                className="mt-0.5 w-full truncate border-0 bg-transparent p-0 text-xs font-medium text-slate-800 outline-none"
              >
                <option value="">不检索知识库</option>
                <option value="*">全部知识库</option>
                {kbs.map((kb) => (
                  <option key={kb.id} value={kb.id}>{kb.name}</option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => setInterviewMode((enabled) => !enabled)}
              className={`shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-medium ${
                interviewMode ? "bg-cyan-100 text-cyan-800" : "bg-slate-900 text-white"
              }`}
            >
              {interviewMode ? "退出面试" : "面试"}
            </button>
            <button
              type="button"
              onClick={handleNewSession}
              className="shrink-0 rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-white"
            >
              新对话
            </button>
          </div>
          {interviewMode ? (
            <InterviewExperience
              knowledgeBases={kbs}
              selectedKbId={selectedKbId}
              conversationId={currentSessionId}
              onClose={() => setInterviewMode(false)}
              onViewSources={(sources) => handleViewSources(sources)}
            />
          ) : (
          <><div className="flex-1 overflow-y-auto">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                <span className="mb-3 text-xs font-medium uppercase tracking-[0.22em] text-cyan-700">
                  Evidence workspace
                </span>
                <h2 className="text-xl font-semibold tracking-tight text-slate-900">
                  从知识、工具与关系中找到答案
                </h2>
                <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
                  在输入框下方按需开启 Agentic RAG 或 Graph RAG。开启图谱后，关系网络会在右侧同步展开。
                </p>
              </div>
            ) : (
              <MessageList
                messages={messages}
                isStreaming={isStreaming}
                streamingMessageId={streamingMessageId}
                progressMessage={progressMessage}
                onViewSources={handleViewSources}
                onRecommendedQuestion={handleSend}
              />
            )}
          </div>
          <ChatInput
            onSend={handleSend}
            onCancel={cancelGeneration}
            isStreaming={isStreaming}
            deepThinking={deepThinkingEnabled}
            onDeepThinkingChange={setDeepThinking}
            agenticRag={agenticRagEnabled}
            onAgenticRagChange={setAgenticRag}
            graphRag={graphRagEnabled}
            onGraphRagChange={setGraphRag}
            graphRagLocked={selectedKbId === "*"}
            neighborExpansion={neighborExpansionEnabled}
            onNeighborExpansionChange={setNeighborExpansion}
            hyde={hydeEnabled}
            onHydeChange={setHyde}
          />
          </>
          )}
        </main>

        {graphRagEnabled && !interviewMode && (
          <KnowledgeGraphPanel
            kbId={selectedKbId}
            kbName={
              selectedKbId === "*"
                ? "全部知识库"
                : kbs.find((kb) => kb.id === selectedKbId)?.name
            }
            refreshKey={graphRevision}
            focusQuery={graphFocusQuery}
            locked={selectedKbId === "*"}
            onClose={() => setGraphRag(false)}
          />
        )}
      </div>

      {/* Sources Drawer */}
      <SourcesDrawer
        open={drawerOpen}
        sources={drawerSources}
        onClose={() => setDrawerOpen(false)}
        highlightIndex={drawerHighlight}
      />

      {/* Direct citation PDF preview */}
      {previewSource && (
        <Suspense fallback={null}>
          <DocumentPreviewModal
            open={!!previewSource}
            documentId={previewSource.documentId}
            docName={previewSource.docName}
            fileType={previewSource.fileType}
            pageStart={previewSource.pageStart}
            bboxes={previewSource.bboxes}
            sourceContent={previewSource.content}
            onClose={() => setPreviewSource(null)}
          />
        </Suspense>
      )}
    </div>
  );
}
