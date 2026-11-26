import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

// ---------- data hook
export function useApi(path, params, deps = []) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  const key = JSON.stringify(params || {});
  const reqId = useRef(0);
  const load = useCallback(() => {
    if (!path) return;
    const id = ++reqId.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    api(path, { params })
      .then((data) => id === reqId.current && setState({ data, loading: false, error: null }))
      .catch((error) => id === reqId.current && setState({ data: null, loading: false, error }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, ...deps]);
  useEffect(load, [load]);
  return { ...state, reload: load };
}

export function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

// ---------- formatting
export const fmtDate = (d) =>
  d ? new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—";
export const fmtMonths = (m) => (m === null || m === undefined ? "—" : `${Number(m).toFixed(1)} m`);
export const fmtNum = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-IN"));

// ---------- primitives
export function Card({ title, actions, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h3>{title}</h3>
          <div className="card-actions">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, tone }) {
  return (
    <div className={`kpi ${tone ? "kpi-" + tone : ""}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export function BarList({ data, labelKey = "label", valueKey = "value", tone = (row) => "", onClick }) {
  const max = Math.max(1, ...data.map((d) => d[valueKey] || 0));
  return (
    <div className="barlist">
      {data.map((d) => (
        <div key={d[labelKey]} className={`bar-row ${onClick ? "clickable" : ""}`} onClick={() => onClick?.(d)}>
          <span className="bar-label">{d[labelKey]}</span>
          <span className="bar-track">
            <span className={`bar-fill ${tone(d)}`} style={{ width: `${(100 * (d[valueKey] || 0)) / max}%` }} />
          </span>
          <span className="bar-value">{fmtNum(d[valueKey])}</span>
        </div>
      ))}
    </div>
  );
}

const STATUS_TONE = { allocated: "green", bench: "amber", training: "blue", notice: "red", corporate: "grey" };
export const StatusBadge = ({ status }) => <span className={`badge badge-${STATUS_TONE[status] || "grey"}`}>{status}</span>;
export const Badge = ({ tone = "grey", children, title }) => <span className={`badge badge-${tone}`} title={title}>{children}</span>;

export function ProfDots({ level, max = 5 }) {
  return (
    <span className="dots" title={`Proficiency ${level}/5`}>
      {Array.from({ length: max }, (_, i) => <i key={i} className={i < level ? "on" : ""} />)}
    </span>
  );
}

export function SkillChip({ s, highlight }) {
  return (
    <span className={`chip ${highlight ? "chip-hl" : ""}`} title={`${s.name} · proficiency ${s.proficiency}${s.verified ? " · verified" : ""}`}>
      {s.name} <b>{s.proficiency}</b>{s.verified ? <span className="tick">✓</span> : null}
    </span>
  );
}

export function Pagination({ page, pageSize, total, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pager">
      <span className="muted">
        {total === 0 ? "No results" : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${fmtNum(total)}`}
      </span>
      <button className="btn btn-ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
      <span>{page} / {pages}</span>
      <button className="btn btn-ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next ›</button>
    </div>
  );
}

export const Loading = () => <div className="muted pad">Loading…</div>;
export const ErrorBox = ({ error }) => (error ? <div className="error">{error.message || String(error)}</div> : null);

export function Select({ value, onChange, options, placeholder = "All", ...rest }) {
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)} {...rest}>
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}
