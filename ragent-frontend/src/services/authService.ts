import { api } from "./api";

export interface LoginParams {
  username: string;
  password: string;
}

export interface AuthResult {
  token: string;
  username: string;
  role: string;
}

export async function login(params: LoginParams): Promise<AuthResult> {
  const { data } = await api.post("/api/auth/login", params);
  return data;
}

export async function register(params: LoginParams): Promise<AuthResult> {
  const { data } = await api.post("/api/auth/register", params);
  return data;
}

export async function getCurrentUser() {
  const { data } = await api.get("/api/auth/current");
  return data;
}
