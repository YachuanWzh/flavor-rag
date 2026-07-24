import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login, register } from "../services/authService";
import { useAuthStore } from "../stores/authStore";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isRegister, setIsRegister] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const authLogin = useAuthStore((s) => s.login);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      const fn = isRegister ? register : login;
      const result = await fn({ username, password });
      authLogin(result.token, { id: "", username: result.username, role: result.role });
      navigate("/chat");
    } catch (err: any) {
      setError(err?.message || "操作失败");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-md bg-white rounded-lg shadow-md p-8">
        <h1 className="text-2xl font-bold text-center mb-6">
          {isRegister ? "注册" : "登录"}
        </h1>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">用户名</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 border rounded-md"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border rounded-md"
              required
            />
          </div>
          {error && <p className="text-red-500 text-sm">{error}</p>}
          <button
            type="submit"
            className="w-full py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            {isRegister ? "注册" : "登录"}
          </button>
        </form>
        <p className="text-center text-sm mt-4 text-gray-500">
          {isRegister ? "已有账号？" : "没有账号？"}
          <button
            onClick={() => setIsRegister(!isRegister)}
            className="text-blue-600 ml-1 hover:underline"
          >
            {isRegister ? "去登录" : "去注册"}
          </button>
        </p>
      </div>
    </div>
  );
}
