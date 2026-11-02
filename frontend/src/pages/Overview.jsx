import api from "../api.js";
import { BarList, MonthColumns, StackedBar } from "../components/charts.jsx";
import { Empty, ErrorNote, Icon, Loading, PageHeader, Panel, Stat, Stats, Verdict, useLoad } from "../components/ui.jsx";
import { compact, dateTime, monthName, number, percent, words } from "../format.js";
import { ISSUES } from "./Exceptions.jsx";

// Each outcome keeps its own colour wherever it appears, in this fixed order.
const ORDER_SERIES = { FULFILLED: 1, CANCELLED: 2, CONFIRMED: 3, REJECTED: 4, ALLOCATED: 5 };

function allPassed(metrics) {
  const { detection, validation, checkpoints, reproducibility, unrelated_problems: unrelated } = metrics;
  return [
    { label: "Clean baseline passes every check",
      value: `${metrics.clean_baseline?.checks - metrics.clean_baseline?.failed} of ${metrics.clean_baseline?.checks}`,
      good: metrics.clean_baseline?.failed === 0 },
    { label: "Unexpected failures on the final data",
      value: number(validation?.unexpected_failures.length), good: validation?.unexpected_failures.length === 0 },
    { label: "Detector recall, all four types",
      value: Object.values(detection || {}).every((row) => row.recall === 1) ? "100%" : "Below 100%",
      good: Object.values(detection || {}).every((row) => row.recall === 1) },
    { label: "ATP checkpoints agreeing with SQL",
      value: checkpoints ? `${Object.values(checkpoints.by_kind).reduce((sum, row) => sum + row.matched, 0)} of ${Object.values(checkpoints.by_kind).reduce((sum, row) => sum + row.checked, 0)}` : "–",
      good: checkpoints?.differing === 0 },
    { label: "Same seed, same files", value: reproducibility ? `${reproducibility.files_compared} files` : "Not run",
      good: reproducibility?.different.length === 0 },
    { label: "Unrelated records changed by injection", value: unrelated ? number(unrelated.length) : "–", good: unrelated?.length === 0 },
  ];
}

// The detectors' latest answer, one line per type, each leading to its rows on the Exceptions page.
function NeedsAttention({ found }) {
  if (found.error) return <ErrorNote text={found.error} onRetry={found.reload} />;
  if (!found.data) return <Loading rows={4} />;
  const counts = Object.fromEntries(found.data.counts.map((row) => [row.issue_type, row.rows]));
  if (found.data.counts.length === 0) {
    return <Empty title="Nothing flagged">The detectors found no problems in the loaded data, or haven’t run yet.</Empty>;
  }
  return (
    <ul className="link-list">
      {Object.entries(ISSUES).map(([code, info]) => (
        <li key={code}>
          <a href={`#/exceptions?issue=${code}`}>
            <span className={`link-count ${counts[code] ? "count-bad" : ""}`}>{number(counts[code] || 0)}</span>
            <span>{info.title}</span>
            <span className="link-arrow" aria-hidden="true">→</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

export default function Overview() {
  const overview = useLoad(() => api.get("/overview"), []);
  const metrics = useLoad(() => api.get("/metrics"), []);
  const found = useLoad(() => api.get("/exceptions", { params: { limit: 1 } }), []);
  const data = overview.data;

  if (overview.error) return <ErrorNote text={overview.error} onRetry={overview.reload} />;
  if (!data) return <Loading page />;

  const counts = data.row_counts;
  const types = Object.fromEntries(data.locations_by_type.map((row) => [row.location_type, row.rows]));
  const statusRows = data.order_statuses
    .map((row) => ({ label: words(row.status), value: row.rows, series: ORDER_SERIES[row.status] || 6 }))
    .sort((a, b) => a.series - b.series);
  const m = metrics.data;

  return (
    <>
      <PageHeader eyebrow={`As of ${dateTime(data.as_of)}`} title="Network overview">
        {number(counts.sku)} SKUs at {number(counts.location)} locations: {types.NDC || 0} national DC, {types.WAREHOUSE || 0} warehouses
        and {types.STORE || 0} stores, selling through web, app, marketplace and walk-in.
      </PageHeader>

      <Stats columns={6}>
        <Stat lead label="On hand" value={number(data.on_hand_units)} note="Units, including damaged" />
        <Stat label="Reserved" value={number(data.reserved_units)} note={`${number(data.open_holds)} open holds`} />
        <Stat label="Blocked" value={number(data.blocked_units)} note="Damaged, can’t be sold" />
        <Stat label="In transit" value={number(data.in_transit_units)} note={`${number(data.in_transit_trucks)} trucks on the road`} />
        <Stat label="Orders" value={number(counts.orders)} note={`${number(counts.order_line)} lines`} />
        <Stat label="Movements" value={compact(counts.inventory_movement)} note="Ledger rows, never edited" />
      </Stats>

      <div className="grid-two">
        <Panel title="What needs attention" note="The latest detector run"
               actions={<a className="btn btn-sm btn-outline-secondary" href="#/exceptions">All exceptions</a>}>
          <NeedsAttention found={found} />
        </Panel>

        <Panel title="What happened to orders" note={`Current status of all ${number(counts.orders)} orders`}>
          <StackedBar rows={statusRows} />
        </Panel>
      </div>

      {metrics.error && <ErrorNote text={metrics.error} onRetry={metrics.reload} />}
      {metrics.loading && !m && <Panel><Loading /></Panel>}
      {m && (
        <>
          <div className="grid-two">
            <Panel title="Ledger rows by month" note="The end-of-season sale runs in early July">
              <MonthColumns rows={m.mix.months.map((row) => ({ label: monthName(row.month), value: row.movements }))} />
            </Panel>
            <Panel title="Can these numbers be trusted?" note="The last measured run on this dataset"
                   actions={<a className="btn btn-sm btn-outline-secondary" href="#/evidence">See the evidence</a>}>
              <ul className="check-list">
                {allPassed(m).map((row) => (
                  <li key={row.label}>
                    <span>{row.label}</span>
                    <Verdict good={row.good}>{row.value}</Verdict>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>

          <div className="grid-two">
            <Panel title="Ledger rows by type" note={`Measured on the ${number(counts.inventory_movement)} movements loaded`}>
              <BarList rows={m.mix.movements.map((row) => ({ label: row.movement.replace("SALE, ", "Sale, ").replace(/^[A-Z_]+$/, words), value: row.movements, note: percent(row.share) }))} />
            </Panel>
            <Panel title="How order lines were decided" note={`${number(m.decisions.total)} decisions, each kept with its reason`}>
              <BarList rows={m.decisions.by_code.map((row) => ({ label: words(row.code), value: row.decisions, note: percent(row.share) }))} />
            </Panel>
          </div>
        </>
      )}

      <p className="page-foot">
        <Icon name="alert" size={14} /> All data is synthetic, generated with seed {m ? m.dataset.seed : "20261022"}. No customer data or external data is used.
      </p>
    </>
  );
}
