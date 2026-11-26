import { useNavigate, useParams } from "react-router-dom";
import ProfileView from "../components/ProfileView";
import UpskillRecs from "../components/UpskillRecs";
import { ErrorBox, Loading, useApi } from "../components/ui";

export default function EmployeePage() {
  const { empId } = useParams();
  const nav = useNavigate();
  const { data, loading, error } = useApi(`/api/employees/${empId}`);
  return (
    <div className="page">
      <button className="btn btn-link back" onClick={() => nav(-1)}>‹ Back</button>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && <>
        <ProfileView p={data} />
        {data.function === "Delivery" && <UpskillRecs empId={empId} />}
      </>}
    </div>
  );
}
