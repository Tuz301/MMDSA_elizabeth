/**
 * The shell: navigation shaped by role.
 *
 * The nav is built from the /me payload rather than guessed from failed
 * requests. A mentor mother with a smartphone sees her alerts and visits; a
 * supervisor sees the clinical worklists; only user administrators see the
 * accounts area. Hiding a link is convenience, not security — the server
 * enforces every permission again.
 */

import { NavLink, Outlet } from "react-router-dom";

import { useMe } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import { ErrorBanner } from "./ui";

export function Layout() {
  const { data: me, error, isLoading } = useMe();
  const { signOut } = useAuth();

  if (isLoading) {
    return <div className="main">Loading your workspace…</div>;
  }
  if (error || !me) {
    return (
      <div className="main">
        <ErrorBanner error={error ?? new Error("Could not load your account.")} />
        <button onClick={signOut}>Back to sign in</button>
      </div>
    );
  }

  const scope = me.facility?.name ?? me.lga?.name ?? me.state?.name ?? "All sites";
  const clinical = me.may_enter_clinical_data;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          MMDSA
          <small>PMTCT supervision</small>
        </div>
        <nav>
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/alerts">Alerts</NavLink>
          {clinical && <NavLink to="/eid">EID cascade</NavLink>}
          <NavLink to="/visits">Home visits</NavLink>
          {clinical && <NavLink to="/registry">Registry</NavLink>}
        </nav>
        <div className="whoami">
          <strong>{me.username}</strong>
          {me.role_display}
          <div>{scope}</div>
          <button className="subtle" style={{ marginTop: 10 }} onClick={signOut}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet context={me} />
      </main>
    </div>
  );
}
