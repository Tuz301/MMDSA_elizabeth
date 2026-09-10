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
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { configureClient } from "../api/client";
import { cognitoSignIn, type SignInOutcome } from "./cognito";

const STORAGE_KEY = "mmdsa.access_token";

export type AuthMode = "cognito" | "token" | "session";

export const AUTH_MODE: AuthMode =
  (import.meta.env.VITE_AUTH_MODE as AuthMode | undefined) ?? "cognito";

/**
 * Sentinel for session mode: the user is signed in through the Django
 * session cookie, so requests carry no bearer header at all.
 */
export const SESSION_SENTINEL = "@session";

interface AuthState {
  token: string | null;
  authNotice: string | null;
  signInWithToken: (token: string) => void;
  signInWithSession: () => void;
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

  const [authNotice, setAuthNotice] = useState<string | null>(null);

  // The token lives in a ref as well as in state, and the client is
  // configured synchronously during render, never in an effect. React runs
  // child effects before parent effects, and the query layer fires its
  // first fetch from a child effect — an effect-configured provider would
  // hand that first fetch a null token, the 401 would call signOut, and
  // sign-in could never complete outside session mode.
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const store = useCallback((value: string | null) => {
    tokenRef.current = value;
    setToken(value);
    if (value !== null) setAuthNotice(null);
    try {
      if (value === null) sessionStorage.removeItem(STORAGE_KEY);
      else sessionStorage.setItem(STORAGE_KEY, value);
    } catch {
      // Session storage can be unavailable; the in-memory token still works.
    }
  }, []);

  const signOut = useCallback(() => store(null), [store]);

  configureClient({
    tokenProvider: () =>
      tokenRef.current === SESSION_SENTINEL ? null : tokenRef.current,
    onUnauthorized: () => {
      if (tokenRef.current !== null) {
        setAuthNotice(
          "The server did not accept the session or token. Sign in again.",
        );
      }
      store(null);
    },
  });

  const value = useMemo<AuthState>(
    () => ({
      token,
      authNotice,
      signInWithToken: (raw: string) => store(raw.trim()),
      signInWithSession: () => store(SESSION_SENTINEL),
      signInWithCognito: async (username, password, newPassword) => {
        const outcome = await cognitoSignIn(username, password, newPassword);
        if (outcome.kind === "success") store(outcome.accessToken);
        return outcome;
      },
      signOut,
    }),
    [token, authNotice, store, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth outside AuthProvider");
  return value;
}
