import { createBrowserRouter, Navigate } from "react-router-dom";
import App from "./App";
import LoginPage from "./pages/LoginPage";
import ChatPage from "./pages/ChatPage";
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
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
