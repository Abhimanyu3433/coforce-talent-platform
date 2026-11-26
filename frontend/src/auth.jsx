import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, tokenStore } from "./api";

const AuthContext = createContext(null);
export const PRIVILEGED = ["lead", "manager", "hr"];

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(!!tokenStore.get());

  useEffect(() => {
    if (!tokenStore.get()) return;
    api("/api/auth/me").then(setUser).catch(() => tokenStore.clear()).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onLogout = () => setUser(null);
    window.addEventListener("cf-logout", onLogout);
    return () => window.removeEventListener("cf-logout", onLogout);
  }, []);

  const login = useCallback(async (email, password) => {
    const res = await api("/api/auth/login", { method: "POST", body: { email, password } });
    tokenStore.set(res.access_token);
    setUser(res.user);
    return res.user;
  }, []);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  const isPrivileged = !!user && PRIVILEGED.includes(user.app_role);
  return (
    <AuthContext.Provider value={{ user, loading, login, logout, isPrivileged }}>{children}</AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
