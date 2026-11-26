import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import ResidencyDashboard from "./pages/ResidencyDashboard";
import TalentExplorer from "./pages/TalentExplorer";
import EmployeePage from "./pages/EmployeePage";
import MyProfile from "./pages/MyProfile";
import CertQueue from "./pages/CertQueue";
import DemandsList from "./pages/DemandsList";
import DemandForm from "./pages/DemandForm";
import DemandDetail from "./pages/DemandDetail";
import Approvals from "./pages/Approvals";
import DemandAssistant from "./pages/DemandAssistant";
import TrainingHub from "./pages/TrainingHub";
import PlanBuilder from "./pages/PlanBuilder";
import PlanDetail from "./pages/PlanDetail";
import UpskillAdvisor from "./pages/UpskillAdvisor";
import SystemPage from "./pages/SystemPage";

const PRIV = ["lead", "manager", "hr"];

function Guard({ children, roles }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="center muted">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.app_role)) return <Navigate to="/" replace />;
  return children;
}

function Home() {
  const { isPrivileged } = useAuth();
  return <Navigate to={isPrivileged ? "/dashboard" : "/me"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Guard><Layout /></Guard>}>
        <Route index element={<Home />} />
        <Route path="dashboard" element={<Guard roles={["lead", "manager", "hr"]}><ResidencyDashboard /></Guard>} />
        <Route path="talent" element={<Guard roles={["lead", "manager", "hr"]}><TalentExplorer /></Guard>} />
        <Route path="employees/:empId" element={<EmployeePage />} />
        <Route path="me" element={<MyProfile />} />
        <Route path="certifications" element={<Guard roles={["hr"]}><CertQueue /></Guard>} />
        <Route path="demands" element={<Guard roles={PRIV}><DemandsList /></Guard>} />
        <Route path="demands/new" element={<Guard roles={PRIV}><DemandForm /></Guard>} />
        <Route path="demands/assistant" element={<Guard roles={PRIV}><DemandAssistant /></Guard>} />
        <Route path="demands/:demandId" element={<Guard roles={PRIV}><DemandDetail /></Guard>} />
        <Route path="approvals" element={<Approvals />} />
        <Route path="training" element={<Guard roles={PRIV}><TrainingHub /></Guard>} />
        <Route path="training/new" element={<Guard roles={PRIV}><PlanBuilder /></Guard>} />
        <Route path="training/plans/:planId" element={<Guard roles={PRIV}><PlanDetail /></Guard>} />
        <Route path="upskilling" element={<Guard roles={PRIV}><UpskillAdvisor /></Guard>} />
        <Route path="system" element={<Guard roles={["manager", "hr"]}><SystemPage /></Guard>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
