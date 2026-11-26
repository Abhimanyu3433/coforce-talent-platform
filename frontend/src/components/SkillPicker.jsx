import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useDebounced } from "./ui";

/** Autocomplete over the canonical skill taxonomy.
 *  multi=true  → value is an array of skill names, renders chips
 *  multi=false → value is a single skill name string */
export default function SkillPicker({ value, onChange, multi = true, placeholder = "Type a skill or tool…" }) {
  const [text, setText] = useState(multi ? "" : value || "");
  const [opts, setOpts] = useState([]);
  const [open, setOpen] = useState(false);
  const q = useDebounced(text, 200);
  const box = useRef(null);

  useEffect(() => {
    if (!multi) setText(value || "");
  }, [value, multi]);

  useEffect(() => {
    if (!q || q.length < 1) return setOpts([]);
    let live = true;
    api("/api/skills/search", { params: { q, limit: 8 } }).then((r) => live && setOpts(r)).catch(() => {});
    return () => { live = false; };
  }, [q]);

  useEffect(() => {
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const choose = (name) => {
    if (multi) {
      if (!value.includes(name)) onChange([...value, name]);
      setText("");
    } else {
      onChange(name);
      setText(name);
    }
    setOpen(false);
  };

  return (
    <div className="skillpicker" ref={box}>
      {multi && value.length > 0 && (
        <div className="chips">
          {value.map((s) => (
            <span key={s} className="chip chip-hl">
              {s} <button type="button" onClick={() => onChange(value.filter((x) => x !== s))}>×</button>
            </span>
          ))}
        </div>
      )}
      <input
        value={text}
        placeholder={placeholder}
        onChange={(e) => { setText(e.target.value); setOpen(true); if (!multi) onChange(e.target.value); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (opts[0]) choose(opts[0].name);
            else if (multi && text.trim()) choose(text.trim());
          }
        }}
      />
      {open && opts.length > 0 && (
        <ul className="dropdown">
          {opts.map((o) => (
            <li key={o.skill_id} onMouseDown={() => choose(o.name)}>
              <span>{o.name}</span>
              <small className="muted">{o.type} · {o.category}</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
