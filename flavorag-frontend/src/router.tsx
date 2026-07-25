import { createBrowserRouter, Navigate } from "react-router-dom";
import App from "./App";
import LoginPage from "./pages/LoginPage";
import ChatPage from "./pages/ChatPage";
import AdminPage from "./pages/admin/AdminPage";
import KnowledgeBasePage from "./pages/knowledge/KnowledgeBasePage";
import KnowledgeBaseDetailPage from "./pages/knowledge/KnowledgeBaseDetailPage";
import KnowledgeChunksPage from "./pages/knowledge/KnowledgeChunksPage";
import NotFoundPage from "./pages/NotFoundPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "login", element: <LoginPage /> },
      { path: "chat", element: <ChatPage /> },
      { path: "chat/:sessionId", element: <ChatPage /> },
      { path: "admin", element: <AdminPage /> },
      { path: "knowledge", element: <KnowledgeBasePage /> },
      { path: "knowledge/:kbId", element: <KnowledgeBaseDetailPage /> },
      { path: "knowledge/:kbId/docs/:docId", element: <KnowledgeChunksPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
