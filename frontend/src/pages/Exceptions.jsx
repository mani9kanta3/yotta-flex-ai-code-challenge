import { useState } from "react";
import api, { errorText } from "../api.js";
import { Empty, ErrorNote, Loading, PageHeader, Panel, useLoad } from "../components/ui.jsx";
import { number, words } from "../format.js";

const ISSUES = {
  BALANCE_NOT_RECONCILED: {
    title: "Balance doesn’t reconcile",
    text: "The stored closing balance differs from opening stock plus the ledger.",
    unit: "units on hand",
  },
  RESERVATION_EXCEEDS_AVAILABLE: {
    title: "Hold above what was available",
    text: "Recalculated from the ledger, this hold was more than could be promised when it was placed.",
    unit: "units held",
  },
  FULFILLED_FROM_BLOCKED: {
    title: "Shipped from damaged stock",
    text: "An order’s sale came out of the blocked bucket.",
    unit: "units sold from blocked",
  },
  TRANSFER_DOUBLE_COUNTED: {
    title: "Transfer counted at both ends",
    text: "Stock reached the destination before it left the source.",
    unit: "units at both ends",
  },
};

function RecordLink({ row }) {
  if (row.order_id) return <a href={`#/orders/${row.order_id}`}>{row.order_id}</a>;
  if (row.sku_id && row.location_id) return <a href={`#/ledger?sku=${row.sku_id}&location=${row.location_id}`}>Open ledger</a>;
  return null;
}

export default function Exceptions() {
  const [issue, setIssue] = useState("");
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const found = useLoad(() => api.get("/exceptions", { params: { issue_type: issue || undefined, limit: 200 } }), [issue]);
  const counts = Object.fromEntries((found.data?.counts || []).map((row) => [row.issue_type, row.rows]));

  async function runAgain() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await api.post("/exceptions/run");
      setMessage(`The detectors ran again and found ${number(response.data.found)} problems.`);
      found.reload();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Operations manager" title="What needs someone’s attention"
                  actions={<button type="button" className="btn btn-sm btn-outline-secondary" disabled={busy} onClick={runAgain}>{busy ? "Running…" : "Run detectors again"}</button>}>
        Four SQL detectors work this out from the ledger and events. They never see which records were
        labelled. Their scores against the labels, including valid look-alike decoys, are on the Evidence page.
      </PageHeader>

      <ErrorNote text={error} />
      {message && <div className="note note-good" role="status">{message}</div>}

      <div className="issue-cards" role="group" aria-label="Filter by problem">
        {Object.entries(ISSUES).map(([code, info]) => (
          <button key={code} type="button" className={`issue-card ${issue === code ? "active" : ""}`} aria-pressed={issue === code}
                  onClick={() => setIssue(issue === code ? "" : code)}>
            <span className="issue-count">{number(counts[code] || 0)}</span>
            <strong>{info.title}</strong>
            <small>{info.text}</small>
          </button>
        ))}
      </div>

      <Panel title={issue ? ISSUES[issue].title : "All exceptions"} note="Expected is what the ledger and events say. Actual is what the record says.">
        {found.error && <ErrorNote text={found.error} />}
        {found.loading && !found.data && <Loading />}
        {found.data && found.data.exceptions.length === 0 && (
          <Empty>Nothing to show. On the clean baseline this is the right answer. If the detectors haven’t run yet, use the button above.</Empty>
        )}
        {found.data && found.data.exceptions.length > 0 && (
          <div className="table-wrap">
            <table className="table data-table">
              <thead>
                <tr><th>Problem</th><th>Record</th><th>SKU</th><th>Location</th><th className="num">Expected</th><th className="num">Actual</th><th>Recommended action</th><th>Follow it</th></tr>
              </thead>
              <tbody>
                {found.data.exceptions.map((row) => (
                  <tr key={row.exception_id}>
                    <td>{ISSUES[row.issue_type]?.title || words(row.issue_type)}<small className="cell-note">{row.exception_id} · {ISSUES[row.issue_type]?.unit}</small></td>
                    <td className="nowrap small-text">{row.record_id}</td>
                    <td className="nowrap"><a href={`#/availability?sku=${row.sku_id}`}>{row.sku_id}</a></td>
                    <td className="nowrap">{row.location_id ? <a href={`#/ledger?sku=${row.sku_id}&location=${row.location_id}`}>{row.location_id}</a> : "–"}</td>
                    <td className="num">{number(row.expected_value)}</td>
                    <td className="num strong">{number(row.actual_value)}</td>
                    <td>{words(row.recommended_action)}</td>
                    <td className="nowrap"><RecordLink row={row} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
