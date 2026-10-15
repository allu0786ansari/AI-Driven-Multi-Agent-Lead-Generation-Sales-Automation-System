import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken, login as apiLogin, setToken, setUnauthorizedHandler } from "./api";

interface Me {
  user_id: string;
  email: string;
  business_name: string;
}

interface AuthState {
  token: string | null;
  me: Me | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => getToken());
  const [me, setMe] = useState<Me | null>(null);
  const queryClient = useQueryClient();

  const signOut = useCallback(() => {
    setToken(null);
    setTokenState(null);
    setMe(null);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => setUnauthorizedHandler(signOut), [signOut]);

  useEffect(() => {
    if (!token) return;
    api<Me>("/api/auth/me").then(setMe, () => undefined);
  }, [token]);

  const signIn = useCallback(async (email: string, password: string) => {
    const t = await apiLogin(email, password);
    setToken(t);
    setTokenState(t);
  }, []);

  const value = useMemo(() => ({ token, me, signIn, signOut }), [token, me, signIn, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
