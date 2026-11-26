import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

const ROLE_LABEL = { manager: "Manager", lead: "Lead", hr: "HR", employee: "Employee" };

export default function Layout() {
  const { user, logout, isPrivileged } = useAuth();
  const nav = useNavigate();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Coforce<span>Talent</span></div>
        <nav>
          {isPrivileged && <NavLink to="/dashboard">Residency dashboard</NavLink>}
          {isPrivileged && <NavLink to="/talent">Talent explorer</NavLink>}
          {isPrivileged && <NavLink to="/demands" end>Demands</NavLink>}
          {isPrivileged && <NavLink to="/demands/assistant">Demand assistant</NavLink>}
          {isPrivileged && <NavLink to="/demands/new">Raise demand (form)</NavLink>}
          {["lead", "manager"].includes(user.app_role) && <NavLink to="/approvals">Release approvals</NavLink>}
          <NavLink to="/me">My profile</NavLink>
          {user.app_role === "hr" && <NavLink to="/certifications">Certification reviews</NavLink>}
          {isPrivileged && <NavLink to="/training">Training planner</NavLink>}
          {isPrivileged && <NavLink to="/upskilling">Upskilling advisor</NavLink>}
          {["manager", "hr"].includes(user.app_role) && <NavLink to="/system">System</NavLink>}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div />
          <div className="user">
            <div>
              <div className="user-name">{user.name}</div>
              <div className="muted small">{user.designation} · {user.ibu_id} · {ROLE_LABEL[user.app_role]}</div>
            </div>
            <button className="btn btn-ghost" onClick={() => { logout(); nav("/login"); }}>Sign out</button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
