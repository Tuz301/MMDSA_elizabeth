import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ApiError } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { AlertsPage } from "./pages/AlertsPage";
import { EidPage } from "./pages/EidPage";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RegistryPage } from "./pages/RegistryPage";
import { VisitsPage } from "./pages/VisitsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 401 or 403 will not become a 200 by retrying; a network blip might.
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status < 500) return false;
        return failureCount < 2;
      },
      refetchOnWindowFocus: true,
    },
  },
});

function Routed() {
  const { token } = useAuth();
  if (!token) return <LoginPage />;

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<OverviewPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="eid" element={<EidPage />} />
          <Route path="visits" element={<VisitsPage />} />
          <Route path="registry" element={<RegistryPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Routed />
      </AuthProvider>
    </QueryClientProvider>
  );
}
