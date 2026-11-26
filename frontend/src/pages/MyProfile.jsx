import { useState } from "react";
import ProfileView from "../components/ProfileView";
import ResumeUpload from "../components/ResumeUpload";
import UpskillRecs from "../components/UpskillRecs";
import { Card, ErrorBox, Loading, fmtDate, useApi } from "../components/ui";

export default function MyProfile() {
  const { data, loading, error, reload } = useApi("/api/me/profile");
  const upcoming = useApi("/api/training/my");
  const [certPrefill, setCertPrefill] = useState(null);
  return (
    <div className="page">
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <>
          <div className="callout callout-blue">
            Keep your profile current: upload your resume to pick up skills quickly, add tools you use, and add certifications
            with proof — HR verifies them, and verified skills rank higher when demands are matched. Recommendations update
            as your skills and open demand change.
          </div>
          <ResumeUpload onApplied={reload} onAddCert={(c) => setCertPrefill({ ...c, at: Date.now() })} />
          <UpskillRecs editable onStarted={() => { upcoming.reload(); reload(); }} />
          {upcoming.data?.length > 0 && (
            <Card title="Your upcoming training">
              {upcoming.data.map((t) => (
                <div key={t.enrollment_id} className="res-line"><span>{fmtDate(t.start_date)} – {fmtDate(t.end_date)}</span>
                  <b>{t.title}</b> <span className="muted small">{t.mode} · {t.status.replace("_", " ")}{t.plan_id ? ` · plan ${t.plan_id}` : ""}</span></div>))}
            </Card>
          )}
          <ProfileView p={data} editable onChanged={reload} certPrefill={certPrefill} />
        </>
      )}
    </div>
  );
}
