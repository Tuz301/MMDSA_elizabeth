/**
 * Authentication state.
 *
 * Two modes, one interface. In the pilot the dashboard signs in against AWS
 * Cognito with SRP, handling the forced first-password change the programme
 * uses for new accounts. In development, a bearer token is pasted directly,
 * so the dashboard can run against a local backend with no identity pool.
 *
 * The access token lives in sessionStorage: it survives a refresh, dies with
 * the tab, and never touches localStorage where it would outlive the working
 * session on a shared facility computer. That trade-off is deliberate — the
 * likelier threat at a facility is the next person at the same keyboard, not
 * a cross-site script on a hardened, no-third-party-script page.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { configureClient } from "../api/client";
import { cognitoSignIn, type SignInOutcome } from "./cognito";

const STORAGE_KEY = "mmdsa.access_token";

export type AuthMode = "cognito" | "token";

export const AUTH_MODE: AuthMode =
  (import.meta.env.VITE_AUTH_MODE as AuthMode | undefined) ?? "cognito";

interface AuthState {
  token: string | null;
  signInWithToken: (token: string) => void;
  signInWithCognito: (
    username: string,
    password: string,
    newPassword?: string,
  ) => Promise<SignInOutcome>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  });

  const store = useCallback((value: string | null) => {
    setToken(value);
    try {
      if (value === null) sessionStorage.removeItem(STORAGE_KEY);
      else sessionStorage.setItem(STORAGE_KEY, value);
    } catch {
      // Session storage can be unavailable; the in-memory token still works.
    }
  }, []);

  const signOut = useCallback(() => store(null), [store]);

  useEffect(() => {
    configureClient({
      tokenProvider: () => token,
      onUnauthorized: signOut,
    });
  }, [token, signOut]);

  const value = useMemo<AuthState>(
    () => ({
      token,
      signInWithToken: (raw: string) => store(raw.trim()),
      signInWithCognito: async (username, password, newPassword) => {
        const outcome = await cognitoSignIn(username, password, newPassword);
        if (outcome.kind === "success") store(outcome.accessToken);
        return outcome;
      },
      signOut,
    }),
    [token, store, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth outside AuthProvider");
  return value;
}
