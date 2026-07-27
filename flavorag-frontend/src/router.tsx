import { createBrowserRouter, Navigate } from "react-router-dom";
import App from "./App";
import LoginPage from "./pages/LoginPage";
import ChatPage from "./pages/ChatPage";
import AdminLayout from "./pages/admin/AdminLayout";
import DashboardPage from "./pages/admin/DashboardPage";
import IntentTreePage from "./pages/admin/IntentTreePage";
import SampleQuestionsPage from "./pages/admin/SampleQuestionsPage";
import QueryTermMappingPage from "./pages/admin/QueryTermMappingPage";
import TracesPage from "./pages/admin/TracesPage";
import HealthPage from "./pages/admin/HealthPage";
import MonitoringPage from "./pages/admin/MonitoringPage";
import AuditLogPage from "./pages/admin/AuditLogPage";
import SchedulePage from "./pages/admin/SchedulePage";
import IngestionPipelinePage from "./pages/admin/IngestionPipelinePage";
import EvaluationPage from "./pages/admin/EvaluationPage";
import AccessControlPage from "./pages/admin/AccessControlPage";
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
      {
        path: "admin",
        element: <AdminLayout />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: "intent-tree", element: <IntentTreePage /> },
          { path: "sample-questions", element: <SampleQuestionsPage /> },
          { path: "query-mapping", element: <QueryTermMappingPage /> },
          { path: "traces", element: <TracesPage /> },
          { path: "health", element: <HealthPage /> },
          { path: "monitoring", element: <MonitoringPage /> },
          { path: "audit", element: <AuditLogPage /> },
          { path: "schedule", element: <SchedulePage /> },
          { path: "ingestion", element: <IngestionPipelinePage /> },
          { path: "evaluation", element: <EvaluationPage /> },
          { path: "access", element: <AccessControlPage /> },
        ],
      },
      { path: "knowledge", element: <KnowledgeBasePage /> },
      { path: "knowledge/:kbId", element: <KnowledgeBaseDetailPage /> },
      { path: "knowledge/:kbId/docs/:docId", element: <KnowledgeChunksPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
