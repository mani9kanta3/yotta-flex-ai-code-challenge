import api from "../api.js";
import { BarList, MonthColumns, StackedBar } from "../components/charts.jsx";
import { ErrorNote, Loading, PageHeader, Panel, Tile, Verdict, useLoad } from "../components/ui.jsx";
import { compact, dateTime, monthName, number, percent, words } from "../format.js";

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

export default function Overview() {
  const overview = useLoad(() => api.get("/overview"), []);
  const metrics = useLoad(() => api.get("/metrics"), []);
  const data = overview.data;

  if (overview.error) return <ErrorNote text={overview.error} />;
  if (!data) return <Loading />;

  const counts = data.row_counts;
  const types = Object.fromEntries(data.locations_by_type.map((row) => [row.location_type, row.rows]));
  const statusRows = data.order_statuses
    .map((row) => ({ label: words(row.status), value: row.rows, series: ORDER_SERIES[row.status] || 6 }))
    .sort((a, b) => ORDER_SERIES[a.label.toUpperCase()] - ORDER_SERIES[b.label.toUpperCase()]);
  const m = metrics.data;

  return (
    <>
      <PageHeader eyebrow={`As of ${dateTime(data.as_of)}`} title="One view of stock across the network">
        {number(counts.sku)} SKUs at {number(counts.location)} locations: {types.NDC || 0} national DC, {types.WAREHOUSE || 0} warehouses
        and {types.STORE || 0} stores, selling through web, app, marketplace and walk-in.
      </PageHeader>

      <div className="tiles">
        <Tile label="On hand" value={compact(data.on_hand_units)} note="Units on shelves, including damaged" />
        <Tile label="Reserved" value={compact(data.reserved_units)} note={`${number(data.open_holds)} open holds`} />
        <Tile label="Blocked" value={compact(data.blocked_units)} note="Damaged, can’t be sold" />
        <Tile label="In transit" value={compact(data.in_transit_units)} note={`${number(data.in_transit_trucks)} trucks on the road`} />
        <Tile label="Movements" value={compact(counts.inventory_movement)} note="Immutable ledger rows" />
        <Tile label="Orders" value={compact(counts.orders)} note={`${number(counts.order_line)} lines`} />
      </div>

      <div className="grid-two">
        <Panel title="What happened to orders" note="Current status of every order">
          <StackedBar rows={statusRows} />
        </Panel>

        <Panel title="Can these numbers be trusted?" note="The last measured run on this dataset"
               actions={<a className="btn btn-sm btn-outline-secondary" href="#/evidence">See the evidence</a>}>
          {metrics.error && <p className="quiet-note">{metrics.error}</p>}
          {m && (
            <ul className="check-list">
              {allPassed(m).map((row) => (
                <li key={row.label}>
                  <Verdict good={row.good}>{row.value}</Verdict>
                  <span>{row.label}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {m && (
        <div className="grid-two">
          <Panel title="Ledger rows by type" note={`All ${number(counts.inventory_movement)} movements, measured on the loaded data`}>
            <BarList rows={m.mix.movements.map((row) => ({ label: row.movement.replace("SALE, ", "Sale, ").replace(/^[A-Z_]+$/, words), value: row.movements, note: percent(row.share) }))} />
          </Panel>
          <div className="stack-panels">
            <Panel title="Ledger rows by month" note="The end-of-season sale runs in early July">
              <MonthColumns rows={m.mix.months.map((row) => ({ label: monthName(row.month), value: row.movements }))} />
            </Panel>
            <Panel title="How order lines were decided" note={`${number(m.decisions.total)} decisions, each kept with its reason`}>
              <BarList rows={m.decisions.by_code.map((row) => ({ label: words(row.code), value: row.decisions, note: percent(row.share) }))} />
            </Panel>
          </div>
        </div>
      )}

      <p className="page-foot">
        All data is synthetic, generated with seed {m ? m.dataset.seed : "20261022"}. No customer data or external data is used.
      </p>
    </>
  );
}
