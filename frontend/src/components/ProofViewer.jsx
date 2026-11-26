import { useEffect, useRef, useState } from "react";
import { apiBlob } from "../api";
import { ErrorBox, Loading } from "./ui";

const MARK = { true: ["✓", "chip-ok"], false: ["✗", "chip-miss"], null: ["?", "chip-warn"] };

// Automatic checks run on the uploaded proof (employee name, credential ID, title, issuer).
export function ProofChecks({ checks }) {
  if (!checks?.length) return null;
  return (
    <div className="chips">
      {checks.map((c) => {
        const [m, cls] = MARK[String(c.ok)] || MARK.null;
        return <span key={c.check} className={`chip ${cls}`} title={c.detail}>{m} {c.check}</span>;
      })}
    </div>
  );
}

// Modal preview of a certificate proof (PDF in a frame, images inline), fetched with the user's token.
export default function ProofViewer({ cert, onClose, children }) {
  const [url, setUrl] = useState(null);
  const [error, setError] = useState(null);
  const close = useRef(onClose);
  close.current = onClose;                       // parent re-renders (e.g. typing a review note) must not refetch
  useEffect(() => {
    let u = null;
    apiBlob(`/api/certifications/${cert.cert_id}/proof`)
      .then((b) => { u = URL.createObjectURL(b); setUrl(u); })
      .catch(setError);
    const esc = (e) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", esc);
    return () => { if (u) URL.revokeObjectURL(u); window.removeEventListener("keydown", esc); };
  }, [cert.cert_id, cert.proof_uploaded_on]);
  const isPdf = cert.proof_kind === "pdf";
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Certificate proof">
        <header className="modal-head">
          <div>
            <b>{cert.name}</b>
            <div className="muted small">{cert.emp_name ? `${cert.emp_name} · ` : ""}{cert.issuer}
              {cert.credential_id ? ` · credential ${cert.credential_id}` : ""} · {cert.proof_name}</div>
          </div>
          <div className="modal-actions">
            {url && <a className="btn btn-ghost btn-sm" href={url} target="_blank" rel="noreferrer">Open in new tab</a>}
            {url && <a className="btn btn-ghost btn-sm" href={url} download={cert.proof_name}>Download</a>}
            <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
          </div>
        </header>
        <ProofChecks checks={cert.proof_checks} />
        <ErrorBox error={error} />
        <div className="modal-body">
          {!url && !error && <Loading />}
          {url && (isPdf ? <iframe title="Certificate proof" src={url} /> : <img src={url} alt="Certificate proof" />)}
        </div>
        {children}
      </div>
    </div>
  );
}
