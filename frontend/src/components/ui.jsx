import { useCallback, useEffect, useState } from "react";
import api, { errorText } from "../api.js";
import { words } from "../format.js";

// Ask the API for something when the page opens, and again when `deps` change. The `current`
// check stops a slow old answer overwriting a newer one.
export function useLoad(request, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    let current = true;
    setState((old) => ({ ...old, error: null, loading: true }));
    request()
      .then((response) => current && setState({ data: response.data, error: null, loading: false }))
      .catch((error) => current && setState({ data: null, error: errorText(error), loading: false }));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { ...state, reload };
}

export function PageHeader({ eyebrow, title, children, actions }) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {children && <p className="lead-text">{children}</p>}
      </div>
      {actions && <div className="header-actions">{actions}</div>}
    </header>
  );
}

export function Panel({ title, note, children, actions, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <div className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {note && <p>{note}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Tile({ label, value, note }) {
  return (
    <div className="tile">
      <p className="tile-label">{label}</p>
      <p className="tile-value">{value}</p>
      {note && <p className="tile-note">{note}</p>}
    </div>
  );
}

export function Loading({ text = "Loading" }) {
  return <p className="quiet-note" role="status">{text}…</p>;
}

export function ErrorNote({ text }) {
  return text ? <div className="note note-bad" role="alert"><strong>That didn’t work.</strong> {text}</div> : null;
}

export function Empty({ children }) {
  return <p className="quiet-note">{children}</p>;
}

// Colour never says this alone. Each state also has its own icon and its label.
const TONES = {
  good: ["FULFILLED", "CONSUMED", "DELIVERED", "RECEIVED", "SHIPPED", "SINGLE", "ACTIVE", "CONFIRMED", "ALLOCATED"],
  warn: ["IN_TRANSIT", "INBOUND", "SPLIT", "REALLOCATED", "PENDING", "PLACED", "CREATED"],
  bad: ["REJECTED", "CANCELLED", "EXPIRED", "BLOCKED"],
};
const ICONS = { good: "✓", warn: "•", bad: "×", plain: "•" };

export function toneOf(code) {
  return Object.keys(TONES).find((tone) => TONES[tone].includes(code)) || "plain";
}

export function Badge({ code, tone, children }) {
  const chosen = tone || toneOf(code);
  return (
    <span className={`badge-state tone-${chosen}`}>
      <span aria-hidden="true">{ICONS[chosen]}</span> {children || words(code)}
    </span>
  );
}

export function Verdict({ good, children }) {
  return <Badge tone={good ? "good" : "bad"}>{children}</Badge>;
}

// Type to search, then pick. The first match is what the page shows when it opens.
export function SkuPicker({ value, onChange, label = "SKU" }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const matches = useLoad(() => api.get("/skus", { params: { search: text, limit: 8 } }), [text]);

  return (
    <div className="picker">
      <label className="form-label" htmlFor="sku-search">{label}</label>
      <input id="sku-search" className="form-control" autoComplete="off" role="combobox" aria-expanded={open}
             placeholder={value || "Search by SKU, product or brand"} value={text}
             onChange={(event) => { setText(event.target.value); setOpen(true); }}
             onFocus={() => setOpen(true)}
             onBlur={() => setTimeout(() => setOpen(false), 150)} />
      {open && matches.data && (
        <ul className="picker-list" role="listbox">
          {matches.data.length === 0 && <li className="picker-empty">Nothing matches that</li>}
          {matches.data.map((sku) => (
            <li key={sku.sku_id}>
              <button type="button" role="option" aria-selected={sku.sku_id === value}
                      onClick={() => { onChange(sku.sku_id); setText(""); setOpen(false); }}>
                <strong>{sku.sku_id}</strong>
                <span>{sku.product_name} · {sku.colour} {sku.size} · {sku.locations} locations</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The most stocked SKU, for a page opened with none chosen.
export function useDefaultSku(skuId) {
  const [chosen, setChosen] = useState(skuId || null);
  useEffect(() => {
    if (skuId) setChosen(skuId);
    else if (!chosen) api.get("/skus", { params: { limit: 1 } }).then((response) => setChosen(response.data[0]?.sku_id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skuId]);
  return [chosen, setChosen];
}
