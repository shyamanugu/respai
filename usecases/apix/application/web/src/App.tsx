import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, Coach, Employee, Manager, Program } from "./api";
import { useAuth } from "./auth";
import { ChatWidget } from "./components/ChatWidget";
import Login from "./pages/Login";
import IndividualReport from "./pages/IndividualReport";
import Metrics from "./pages/Metrics";
import ManagerOverview from "./pages/ManagerOverview";
import Analytics from "./pages/Analytics";

// ── Shared filter state (program / week / coach / employee / view-as) ────────
interface Filters {
  programs: Program[]; program: string; setProgram: (p: string) => void;
  weeks: string[]; week: string; setWeek: (w: string) => void;
  coaches: Coach[]; coachId: string; setCoachId: (c: string) => void;
  employees: Employee[]; employeeId: string; setEmployeeId: (e: string) => void;
  managers: Manager[]; viewAs: string; setViewAs: (m: string) => void;
}
const FiltersCtx = createContext<Filters>({} as Filters);
export const useFilters = () => useContext(FiltersCtx);

function FiltersProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [programs, setPrograms] = useState<Program[]>([]);
  const [program, setProgram] = useState(user?.program || "");
  const [weeks, setWeeks] = useState<string[]>([]);
  const [week, setWeek] = useState("");
  const [coaches, setCoaches] = useState<Coach[]>([]);
  const [coachId, setCoachId] = useState("");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeeId, setEmployeeId] = useState("");
  const [managers, setManagers] = useState<Manager[]>([]);
  const [viewAs, setViewAs] = useState("");

  useEffect(() => {
    api.programs().then((p) => { setPrograms(p); if (!program && p[0]) setProgram(user?.program || p[0].id); }).catch(() => {});
    if (user && user.role !== "coach") api.managers().then(setManagers).catch(() => {});
  }, []);

  useEffect(() => {
    if (!program) return;
    api.weeks(program).then((r) => { setWeeks(r.weeks); setWeek((w) => w || r.weeks[0] || ""); }).catch(() => setWeeks([]));
  }, [program]);

  useEffect(() => {
    if (!program || !week) return;
    api.coaches(program, week, viewAs || undefined).then(setCoaches).catch(() => setCoaches([]));
  }, [program, week, viewAs]);

  useEffect(() => {
    if (!program || !week) return;
    api.employees(program, week, coachId || undefined, viewAs || undefined)
      .then((e) => { setEmployees(e); setEmployeeId((cur) => (e.some((x) => x.employee_id === cur) ? cur : e[0]?.employee_id || "")); })
      .catch(() => setEmployees([]));
  }, [program, week, coachId, viewAs]);

  return (
    <FiltersCtx.Provider value={{
      programs, program, setProgram, weeks, week, setWeek, coaches, coachId, setCoachId,
      employees, employeeId, setEmployeeId, managers, viewAs, setViewAs,
    }}>{children}</FiltersCtx.Provider>
  );
}

function Sidebar() {
  const { user } = useAuth();
  const f = useFilters();
  const isCoach = user?.role === "coach";
  const isSuper = user?.role !== "coach" && (!user?.coach_ids || user.coach_ids.length === 0);
  return (
    <aside className="sidebar">
      <div className="brand">APIX<small>AFNI Performance Index</small></div>
      <NavLink to="/individual" className="navlink">Individual Report</NavLink>
      <NavLink to="/metrics" className="navlink">Performance Metrics</NavLink>
      {!isCoach && <NavLink to="/manager" className="navlink">Manager Overview</NavLink>}
      <NavLink to="/analytics" className="navlink">Analytics</NavLink>
      <div className="filters">
        <label>Program</label>
        <select value={f.program} onChange={(e) => f.setProgram(e.target.value)}>
          {f.programs.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <label>Week</label>
        <select value={f.week} onChange={(e) => f.setWeek(e.target.value)}>
          {f.weeks.map((w) => <option key={w} value={w}>{w}</option>)}
        </select>
        {isSuper && (
          <>
            <label>View as manager</label>
            <select value={f.viewAs} onChange={(e) => f.setViewAs(e.target.value)}>
              <option value="">All</option>
              {f.managers.map((m) => <option key={m.employee_id} value={m.employee_id}>{m.name}</option>)}
            </select>
          </>
        )}
        {!isCoach && (
          <>
            <label>Coach</label>
            <select value={f.coachId} onChange={(e) => f.setCoachId(e.target.value)}>
              <option value="">All coaches</option>
              {f.coaches.map((c) => <option key={c.coach_id} value={c.coach_id}>{c.coach_name}</option>)}
            </select>
          </>
        )}
        <label>Employee</label>
        <select value={f.employeeId} onChange={(e) => f.setEmployeeId(e.target.value)}>
          {f.employees.map((e) => <option key={e.employee_id} value={e.employee_id}>{e.employee_name}</option>)}
        </select>
      </div>
    </aside>
  );
}

function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main">
        <div className="header">
          <h1>Performance Intelligence</h1>
          <div className="who">
            {user?.name} · <span className="pill good">{user?.role}</span>{" "}
            <button className="btn ghost sm" onClick={async () => { await logout(); nav("/login"); }}>Logout</button>
          </div>
        </div>
        <div className="content">{children}</div>
      </div>
      {user && <ChatWidget userId={user.sub} />}
    </div>
  );
}

function AuthGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="spinner">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <FiltersProvider><Layout>{children}</Layout></FiltersProvider>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<AuthGuard><IndividualReport /></AuthGuard>} />
      <Route path="/individual" element={<AuthGuard><IndividualReport /></AuthGuard>} />
      <Route path="/metrics" element={<AuthGuard><Metrics /></AuthGuard>} />
      <Route path="/manager" element={<AuthGuard><ManagerOverview /></AuthGuard>} />
      <Route path="/analytics" element={<AuthGuard><Analytics /></AuthGuard>} />
      <Route path="*" element={<Navigate to="/individual" replace />} />
    </Routes>
  );
}
