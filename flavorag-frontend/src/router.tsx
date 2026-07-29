import { createBrowserRouter, Navigate } from "react-router-dom";
import { lazy, Suspense, type ReactNode } from "react";
import App from "./App";

const LoginPage = lazy(() => import("./pages/LoginPage"));
const ChatPage = lazy(() => import("./pages/ChatPage"));
const AdminLayout = lazy(() => import("./pages/admin/AdminLayout"));
const DashboardPage = lazy(() => import("./pages/admin/DashboardPage"));
const IntentTreePage = lazy(() => import("./pages/admin/IntentTreePage"));
const SampleQuestionsPage = lazy(() => import("./pages/admin/SampleQuestionsPage"));
const QueryTermMappingPage = lazy(() => import("./pages/admin/QueryTermMappingPage"));
const TracesPage = lazy(() => import("./pages/admin/TracesPage"));
const HealthPage = lazy(() => import("./pages/admin/HealthPage"));
const MonitoringPage = lazy(() => import("./pages/admin/MonitoringPage"));
const AuditLogPage = lazy(() => import("./pages/admin/AuditLogPage"));
const SchedulePage = lazy(() => import("./pages/admin/SchedulePage"));
const IngestionPipelinePage = lazy(() => import("./pages/admin/IngestionPipelinePage"));
const EvaluationPage = lazy(() => import("./pages/admin/EvaluationPage"));
const AccessControlPage = lazy(() => import("./pages/admin/AccessControlPage"));
const UserProfilePage = lazy(() => import("./pages/admin/UserProfilePage"));
const HyperParamsPage = lazy(() => import("./pages/admin/HyperParamsPage"));
const KnowledgeBasePage = lazy(() => import("./pages/knowledge/KnowledgeBasePage"));
const KnowledgeBaseDetailPage = lazy(() => import("./pages/knowledge/KnowledgeBaseDetailPage"));
const KnowledgeChunksPage = lazy(() => import("./pages/knowledge/KnowledgeChunksPage"));
const NotFoundPage = lazy(() => import("./pages/NotFoundPage"));

const page = (content: ReactNode) => (
  <Suspense fallback={<div className="p-8 text-sm text-slate-500">加载中…</div>}>
    {content}
  </Suspense>
);

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "login", element: page(<LoginPage />) },
      { path: "chat", element: page(<ChatPage />) },
      { path: "chat/:sessionId", element: page(<ChatPage />) },
      {
        path: "admin",
        element: page(<AdminLayout />),
        children: [
          { index: true, element: page(<DashboardPage />) },
          { path: "intent-tree", element: page(<IntentTreePage />) },
          { path: "sample-questions", element: page(<SampleQuestionsPage />) },
          { path: "query-mapping", element: page(<QueryTermMappingPage />) },
          { path: "traces", element: page(<TracesPage />) },
          { path: "health", element: page(<HealthPage />) },
          { path: "monitoring", element: page(<MonitoringPage />) },
          { path: "audit", element: page(<AuditLogPage />) },
          { path: "schedule", element: page(<SchedulePage />) },
          { path: "ingestion", element: page(<IngestionPipelinePage />) },
          { path: "evaluation", element: page(<EvaluationPage />) },
          { path: "access", element: page(<AccessControlPage />) },
          { path: "profiles", element: page(<UserProfilePage />) },
          { path: "hyperparams", element: page(<HyperParamsPage />) },
        ],
      },
      { path: "knowledge", element: page(<KnowledgeBasePage />) },
      { path: "knowledge/:kbId", element: page(<KnowledgeBaseDetailPage />) },
      { path: "knowledge/:kbId/docs/:docId", element: page(<KnowledgeChunksPage />) },
      { path: "*", element: page(<NotFoundPage />) },
    ],
  },
]);
