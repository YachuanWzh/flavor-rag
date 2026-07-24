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
  return await api.post("/api/auth/login", params);
}

export async function register(params: LoginParams): Promise<AuthResult> {
  return await api.post("/api/auth/register", params);
}

export async function getCurrentUser() {
  return await api.get("/api/auth/current");
}
