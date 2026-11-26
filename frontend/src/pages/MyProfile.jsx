import ProfileView from "../components/ProfileView";
import UpskillRecs from "../components/UpskillRecs";
import { Card, ErrorBox, Loading, fmtDate, useApi } from "../components/ui";

export default function MyProfile() {
  const { data, loading, error, reload } = useApi("/api/me/profile");
  const upcoming = useApi("/api/training/my");
  return (
    <div className="page">
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <>
          <div className="callout callout-blue">
            Keep your profile current: add skills and tools you use and upload certifications — HR verifies them, and verified
            skills rank higher when demands are matched. Recommendations update as your skills and open demand change.
          </div>
          <UpskillRecs editable onStarted={() => { upcoming.reload(); reload(); }} />
          {upcoming.data?.length > 0 && (
            <Card title="Your upcoming training">
              {upcoming.data.map((t) => (
                <div key={t.enrollment_id} className="res-line"><span>{fmtDate(t.start_date)} – {fmtDate(t.end_date)}</span>
                  <b>{t.title}</b> <span className="muted small">{t.mode} · {t.status.replace("_", " ")}{t.plan_id ? ` · plan ${t.plan_id}` : ""}</span></div>))}
            </Card>
          )}
          <ProfileView p={data} editable onChanged={reload} />
        </>
      )}
    </div>
  );
}
