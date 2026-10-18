import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/shell";
import { ToastProvider, useToast } from "./components/toasts";
import { ApiError } from "./lib/api";
import { AuthProvider, useAuth } from "./lib/auth";
import { useLiveUpdates } from "./lib/live";
import { Activity } from "./pages/Activity";
import { Alerts } from "./pages/Alerts";
import { Inbox, NoLeadSelected } from "./pages/Inbox";
import { LeadPane } from "./pages/lead/LeadPane";
import { Login } from "./pages/Login";
import { Products } from "./pages/Products";
import { Settings } from "./pages/Settings";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      retry: (count, err) => !(err instanceof ApiError && [401, 403, 404].includes(err.status)) && count < 2,
    },
  },
});

function SignedIn() {
  const { token } = useAuth();
  const toast = useToast();
  const onNotification = useCallback(
    (n: { title: string; lead_id: string | null; kind: string }) =>
      toast.show(n.title.replace(/^\S+\s/, ""), { tone: n.kind === "hot_lead" ? "hot" : "info", to: n.lead_id ? `/leads/${n.lead_id}` : undefined }),
    [toast],
  );
  const live = useLiveUpdates({ token, onNotification });
  if (!token) return <Navigate to="/login" replace />;
  return <Shell live={live} />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route element={<SignedIn />}>
                <Route path="/leads" element={<Inbox />}>
                  <Route index element={<NoLeadSelected />} />
                  <Route path=":leadId" element={<LeadPane />} />
                </Route>
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/products" element={<Products />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/activity" element={<Activity />} />
                <Route path="*" element={<Navigate to="/leads" replace />} />
              </Route>
            </Routes>
          </AuthProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
