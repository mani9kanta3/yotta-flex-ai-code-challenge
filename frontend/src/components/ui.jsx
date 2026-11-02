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

// One set of line icons, drawn on a 24 by 24 grid, so no icon library is needed.
const ICON_PATHS = {
  overview: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  availability: "M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8",
  orders: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6",
  ledger: "M5 4h14v16H5zM9 4v16M12 9h4M12 13h4",
  trucks: "M3 7h11v9H3zM14 10h4l3 3v3h-7zM7 19a2 2 0 100-4 2 2 0 000 4zM17 19a2 2 0 100-4 2 2 0 000 4z",
  exceptions: "M12 4l9 16H3zM12 10v4M12 17v.5",
  evidence: "M12 3l8 3v6c0 4.5-3.2 7.5-8 9-4.800-1.500-8-4.500-8-9V6zM8.500 12l2.500 2.500 4.500-5",
  search: "M11 18a7 7 0 100-14 7 7 0 000 14zM16 16l5 5",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "M6 6l12 12M18 6L6 18",
  empty: "M4 13l3-8h10l3 8v6H4zM4 13h5l1 2h4l1-2h5",
  alert: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 8v5M12 16v.5",
};

export function Icon({ name, size = 18 }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path d={ICON_PATHS[name]} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
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

// One strip of key numbers with thin lines between them, rather than a separate card for each.
export function Stats({ children, columns = 4 }) {
  return <div className={`stats stats-${columns}`}>{children}</div>;
}

export function Stat({ label, value, note, lead = false }) {
  return (
    <div className={`stat ${lead ? "stat-lead" : ""}`}>
      <p className="stat-label">{label}</p>
      <div className="stat-value">{value}</div>
      {note && <p className="stat-note">{note}</p>}
    </div>
  );
}

// Grey lines in the shape of what is coming, so the page doesn't jump when the data arrives.
export function Loading({ rows = 5, page = false }) {
  return (
    <div role="status" aria-label="Loading">
      {page && (
        <div className="skeleton-head">
          <span className="skeleton skeleton-title" />
          <span className="skeleton skeleton-text" />
        </div>
      )}
      {page && <div className="skeleton skeleton-band" />}
      <div className={page ? "panel" : "skeleton-rows"}>
        {Array.from({ length: rows }, (_, index) => <span key={index} className="skeleton skeleton-row" />)}
      </div>
    </div>
  );
}

export function ErrorNote({ text, onRetry }) {
  if (!text) return null;
  return (
    <div className="note note-bad note-icon" role="alert">
      <Icon name="alert" />
      <div><strong>That didn’t work.</strong> {text}</div>
      {onRetry && <button type="button" className="btn btn-sm btn-outline-secondary ms-auto" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      <span className="empty-icon"><Icon name="empty" size={22} /></span>
      <p className="empty-title">{title}</p>
      {children && <p className="empty-text">{children}</p>}
      {action}
    </div>
  );
}

// A row of joined buttons where exactly one is chosen. Used for every filter of this kind.
export function Segmented({ label, options, value, onChange }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" className={option.value === value ? "active" : ""}
                aria-pressed={option.value === value} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
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

// Shows the chosen SKU. Click to search, then pick. The first match is what a page opens with.
export function SkuPicker({ value, onChange, label = "SKU" }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const matches = useLoad(() => api.get("/skus", { params: { search: text, limit: 8 } }), [text]);

  return (
    <div className="picker">
      <label className="form-label" htmlFor="sku-search">{label}</label>
      <div className="input-with-icon">
        <Icon name="search" size={16} />
        <input id="sku-search" className="form-control" autoComplete="off" role="combobox" aria-expanded={open}
               placeholder="Search SKU, product or brand" value={open ? text : value || ""}
               onChange={(event) => setText(event.target.value)}
               onFocus={() => setOpen(true)}
               onKeyDown={(event) => event.key === "Escape" && event.target.blur()}
               onBlur={() => setTimeout(() => { setOpen(false); setText(""); }, 150)} />
      </div>
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
