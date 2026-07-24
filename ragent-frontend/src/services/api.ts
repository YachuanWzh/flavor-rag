import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = token;
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    const payload = response.data;
    if (payload && typeof payload === "object" && "code" in payload) {
      if (payload.code !== "0") {
        if (payload.message?.includes("未登录")) {
          localStorage.clear();
          window.location.href = "/login";
        }
        return Promise.reject(new Error(payload.message));
      }
      return payload.data;
    }
    return payload;
  },
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.clear();
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);
