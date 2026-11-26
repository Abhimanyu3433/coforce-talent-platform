// Minimal Gantt: rows of {key, label, sub, start, end, tone, title}; optional vertical markers [{date, label, tone}]
const DAY = 86400000;
const toMs = (d) => new Date(d + "T00:00:00").getTime();

export default function Gantt({ rows, markers = [], from, to }) {
  if (!rows.length) return <div className="muted small">Nothing scheduled.</div>;
  const min = from ? toMs(from) : Math.min(...rows.map((r) => toMs(r.start)));
  const max = (to ? toMs(to) : Math.max(...rows.map((r) => toMs(r.end)), ...markers.map((m) => toMs(m.date)))) + DAY;
  const span = Math.max(DAY, max - min);
  const pct = (ms) => `${Math.max(0, Math.min(100, (100 * (ms - min)) / span))}%`;
  const weeks = [];
  for (let t = min - ((new Date(min).getDay() + 6) % 7) * DAY; t < max; t += 7 * DAY) if (t >= min) weeks.push(t);
  return (
    <div className="gantt">
      <div className="gantt-head">
        <div className="gantt-label" />
        <div className="gantt-track">
          {weeks.map((w) => (
            <span key={w} className="gantt-week" style={{ left: pct(w) }}>
              {new Date(w).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}</span>))}
        </div>
      </div>
      {rows.map((r) => (
        <div key={r.key} className="gantt-row">
          <div className="gantt-label" title={r.label}><div>{r.label}</div>{r.sub && <div className="muted small">{r.sub}</div>}</div>
          <div className="gantt-track">
            {weeks.map((w) => <span key={w} className="gantt-grid" style={{ left: pct(w) }} />)}
            {markers.map((m) => <span key={m.label} className={`gantt-marker ${m.tone || ""}`} style={{ left: pct(toMs(m.date)) }} title={`${m.label} ${m.date}`} />)}
            <span className={`gantt-bar ${r.tone || ""}`} title={r.title || `${r.start} → ${r.end}`}
                  style={{ left: pct(toMs(r.start)), width: `max(6px, calc(${pct(toMs(r.end) + DAY)} - ${pct(toMs(r.start))}))` }}>
              {r.barLabel}</span>
          </div>
        </div>
      ))}
      {markers.length > 0 && (
        <div className="gantt-legend small muted">{markers.map((m) => <span key={m.label}><i className={`gantt-dot ${m.tone || ""}`} /> {m.label} {m.date}</span>)}</div>
      )}
    </div>
  );
}
