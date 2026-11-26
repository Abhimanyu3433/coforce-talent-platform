import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Card, ErrorBox, fmtDate, useApi } from "../components/ui";

const ADD_TO_INPUT_FIELDS = new Set(["mandatory", "nice"]);   // skill suggestions build up a list instead of sending
const VERDICT_TONE = { healthy: "green", trainable: "amber", scarce: "red" };

// tiny renderer: **bold**, line breaks, "•" bullets
function Rich({ text }) {
  return text.split("\n").map((line, i) => (
    <Fragment key={i}>
      {line.split(/(\*\*[^*]+\*\*)/g).map((part, j) =>
        part.startsWith("**") && part.endsWith("**") ? <b key={j}>{part.slice(2, -2)}</b> : <Fragment key={j}>{part}</Fragment>)}
      <br />
    </Fragment>
  ));
}

export default function DemandAssistant() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const status = useApi("/api/chat/demand/status");
  const recent = useApi("/api/chat/demand/sessions", null, [view?.phase]);
  const bottom = useRef(null);
  const input = useRef(null);
  const sid = params.get("session");

  const start = async () => {
    setError(null);
    try {
      const v = await api("/api/chat/demand/sessions", { method: "POST" });
      setView(v);
      setParams({ session: v.session_id }, { replace: true });
    } catch (e) { setError(e); }
  };

  useEffect(() => {
    if (sid) api(`/api/chat/demand/sessions/${sid}`).then(setView).catch((e) => { setError(e); start(); });
    else start();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [view?.messages.length, sending]);

  const send = async (msg) => {
    const t = (msg ?? text).trim();
    if (!t || !view || sending) return;
    setSending(true); setError(null);
    setView((v) => ({ ...v, messages: [...v.messages, { role: "user", content: t, pending: true }] }));
    setText("");
    try {
      setView(await api(`/api/chat/demand/sessions/${view.session_id}/messages`, { method: "POST", body: { text: t } }));
    } catch (e) {
      setError(e);
      setView((v) => ({ ...v, messages: v.messages.filter((m) => !m.pending) }));
      setText(t);
    } finally {
      setSending(false);
      input.current?.focus();
    }
  };

  const quick = (q, m) => {
    if (q === "Open in form") return nav(`/demands/new?chat=${view.session_id}`);
    if (q === "View matches") return nav(`/demands/${m.demand_id || view.demand_id}`);
    if (q === "Raise another demand") { setParams({}); return start(); }
    if (ADD_TO_INPUT_FIELDS.has(m.field) && q !== "None") {
      setText((t) => (t.trim() ? `${t.trim().replace(/,$/, "")}, ${q}` : q));
      return input.current?.focus();
    }
    return send(q.replace(/ — .*$/, ""));
  };

  const msgs = view?.messages || [];
  const lastBot = [...msgs].reverse().find((m) => m.role === "assistant");
  const d = view?.draft;
  const s = view?.supply;
  const st = status.data;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Demand assistant</h1>
          <p className="muted">Describe what you need in plain words — or paste a JD. I'll ask for anything missing and check internal supply as we go.</p>
        </div>
        <div className="filters-inline">
          {st && <Badge tone={st.mode === "gemini" ? "green" : "grey"} title={st.reason || ""}>
            {st.mode === "gemini" ? `Gemini · ${st.model}` : "Built-in parser"} · {st.engine}</Badge>}
          <button className="btn btn-ghost" onClick={() => { setParams({}); start(); }}>New chat</button>
        </div>
      </div>
      {st?.mode === "rules" && (
        <div className="callout small">Running on the built-in rule-based parser ({st.reason}). Add <code>GEMINI_API_KEY</code> to the backend
          environment and restart (or ask a manager/HR to reload) to switch to Gemini — same conversation, better understanding of free text.</div>
      )}
      <ErrorBox error={error} />

      <div className="assistant">
        <Card className="chat">
          <div className="chat-log">
            {msgs.map((m, i) => (
              <div key={i} className={`msg msg-${m.role} ${m.kind ? "msg-" + m.kind : ""}`}>
                <div className="bubble"><Rich text={m.content} />
                  {m.kind === "created" && m.demand_id && <Link className="btn btn-primary btn-sm" to={`/demands/${m.demand_id}`}>Open {m.demand_id}</Link>}
                  {m.role === "assistant" && m.source && (
                    <span className={`via ${m.source === "gemini" ? "via-gemini" : m.source.includes("Gemini") ? "via-error" : ""}`}>
                      {m.source === "gemini" ? "understood via Gemini"
                        : m.source.includes("paused") ? "Gemini busy — built-in parser used (retrying in a couple of minutes)"
                        : m.source.includes("Gemini") ? "Gemini call failed — built-in parser used"
                        : "understood via built-in parser"}
                    </span>)}
                </div>
              </div>
            ))}
            {sending && <div className="msg msg-assistant"><div className="bubble typing"><span /><span /><span /></div></div>}
            <div ref={bottom} />
          </div>
          {lastBot?.quick_replies?.length > 0 && !sending && (
            <div className="quick">
              {lastBot.quick_replies.map((q) => (
                <button key={q} className={`chip-btn ${q === "Create demand" ? "primary" : ""}`} onClick={() => quick(q, lastBot)}>
                  {ADD_TO_INPUT_FIELDS.has(lastBot.field) && q !== "None" ? `+ ${q}` : q}</button>
              ))}
            </div>
          )}
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
            <textarea ref={input} rows={text.length > 120 ? 5 : 2} value={text} disabled={!view || view.phase === "created"}
                      placeholder={view?.phase === "created" ? "This demand is created — start a new chat for another one." : "Type your answer… (Enter to send, Shift+Enter for a new line)"}
                      onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
            <button className="btn btn-primary" disabled={sending || !text.trim()}>Send</button>
          </form>
        </Card>

        <aside className="assistant-side">
          {d && (
            <Card title="Draft demand" actions={<span className="muted small">{view.progress}% complete</span>}>
              <div className="meter"><span className="meter-fill" style={{ width: `${view.progress}%` }} /></div>
              <dl className="draft">
                <Row label="Role" ok={d.role_title}>{d.positions ? `${d.positions} × ` : ""}{d.role_title}</Row>
                <Row label="Mandatory" ok={d.mandatory.length}>
                  {d.mandatory.map((m) => <span key={m.skill_id} className="chip chip-hl">{m.name}{m.min_proficiency && <b>{m.min_proficiency}+</b>}</span>)}</Row>
                <Row label="Good to have" ok={d.nice.length || !view.missing.includes("nice")}>
                  {d.nice.map((n) => <span key={n.skill_id} className="chip">{n.name}</span>)}</Row>
                <Row label="Experience" ok={d.exp_min != null}>{d.exp_min != null && `${d.exp_min}–${d.exp_max} yrs · ${d.grade_min}–${d.grade_max}`}</Row>
                <Row label="Engagement" ok={d.account_id || d.ibu_id}>{d.account_name || d.ibu_id}{d.project_name && <div className="muted small">{d.project_name}</div>}</Row>
                <Row label="Location" ok={d.locations.length || d.remote_allowed}>{d.locations.join(", ")}{d.remote_allowed ? " · remote OK" : ""}{d.work_mode ? ` · ${d.work_mode}` : ""}</Row>
                <Row label="Needed by" ok={d.needed_by}>{d.needed_by && fmtDate(d.needed_by)}{d.duration_months ? ` · ${d.duration_months} months` : ""}</Row>
                <Row label="Shift / interview" ok={d.shift}>{d.shift}{d.client_interview != null ? ` · interview ${d.client_interview ? "yes" : "no"}` : ""}</Row>
                <Row label="Priority / factor" ok={d.priority}>{d.priority}{d.overprovision_factor ? ` · ×${d.overprovision_factor}` : ""}</Row>
              </dl>
              {view.required_missing.length > 0 && <p className="small muted">Still required: {view.required_missing.join(", ").replace(/_/g, " ")}</p>}
              <div className="form-row">
                <button className="btn btn-ghost" onClick={() => nav(`/demands/new?chat=${view.session_id}`)}>Open in form</button>
                {view.can_create && view.phase !== "created" && <button className="btn btn-primary" onClick={() => send("create demand")}>Create now</button>}
                {view.demand_id && <Link className="btn btn-primary" to={`/demands/${view.demand_id}`}>View {view.demand_id}</Link>}
              </div>
            </Card>
          )}
          {s && (
            <Card title={<>Internal supply <Badge tone={VERDICT_TONE[s.verdict]}>{s.verdict}</Badge></>}>
              <div className="supply-grid">
                <div><b>{s.fully_matching_in_time}</b><span>fully match in time</span></div>
                <div><b>{s.ready_in_time_with_training}</b><span>ready with training</span></div>
                <div><b>{s.eligible}</b><span>eligible</span></div>
                <div><b>{s.needed}</b><span>needed</span></div>
              </div>
              {s.assumed_date && <p className="muted small">Assuming a start in 30 days until you give a date.</p>}
              {s.relaxations.filter((r) => r.gain > 0).slice(0, 3).map((r) => (
                <div key={r.skill_id} className="relax">
                  <span>Make <b>{r.name}</b> good-to-have → +{r.gain}</span>
                  {view.phase !== "created" && <button className="btn btn-link" onClick={() => send(`make ${r.name} good-to-have`)}>Apply</button>}
                </div>
              ))}
              {s.top?.length > 0 && <><h4>Top matches</h4>
                {s.top.map((t) => <div key={t.emp_id} className="small">{t.name} · <b>{t.score}</b> <span className="muted">{t.availability_label}</span></div>)}</>}
            </Card>
          )}
          {recent.data?.length > 1 && (
            <Card title="Recent chats">
              {recent.data.filter((r) => r.session_id !== view?.session_id).slice(0, 6).map((r) => (
                <div key={r.session_id} className="recent">
                  <button className="btn btn-link" onClick={() => { setParams({ session: r.session_id }); api(`/api/chat/demand/sessions/${r.session_id}`).then(setView); }}>{r.title}</button>
                  <span className="muted small">{r.status === "created" ? r.demand_id : "in progress"}</span>
                </div>
              ))}
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
}

function Row({ label, ok, children }) {
  return (
    <div className="draft-row">
      <dt><span className={ok ? "tick" : "pending"}>{ok ? "✓" : "•"}</span> {label}</dt>
      <dd>{ok ? children : <span className="muted small">—</span>}</dd>
    </div>
  );
}
