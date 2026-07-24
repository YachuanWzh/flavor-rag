import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/authStore";
import { getCurrentUser } from "../services/authService";

export default function ChatPage() {
  const navigate = useNavigate();
  const { isAuthenticated, login, setUser: setStoreUser, logout } = useAuthStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      navigate("/login");
      return;
    }
    getCurrentUser()
      .then((user) => {
        setStoreUser(user);
        setLoading(false);
      })
      .catch(() => {
        logout();
        navigate("/login");
      });
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500">加载中...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="h-14 border-b flex items-center px-4 bg-white">
        <h1 className="font-semibold text-lg">RAG 智能问答</h1>
        <button
          onClick={() => {
            logout();
            navigate("/login");
          }}
          className="ml-auto text-sm text-gray-500 hover:text-gray-700"
        >
          退出
        </button>
      </header>
      <div className="flex-1 flex items-center justify-center text-gray-400">
        问答界面开发中...
      </div>
    </div>
  );
}
