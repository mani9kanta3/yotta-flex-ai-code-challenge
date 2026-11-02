import { useState } from "react";
import api, { errorText } from "../api.js";
import { Badge, Empty, ErrorNote, Loading, PageHeader, Panel, Segmented, Stat, Stats, useLoad } from "../components/ui.jsx";
import { dateTime, number } from "../format.js";

const STATES = [
  { value: "ALL", label: "All" },
  { value: "OVERDUE", label: "Overdue" },
  { value: "DELAYED", label: "Delayed" },
  { value: "ON_TIME", label: "On time" },
];

function stateOf(truck, now) {
  if (truck.current_eta_at < now) return "OVERDUE";
  return truck.current_eta_at !== truck.original_eta_at ? "DELAYED" : "ON_TIME";
}

function StateBadge({ state }) {
  if (state === "OVERDUE") return <Badge tone="bad">Overdue</Badge>;
  if (state === "DELAYED") return <Badge tone="warn">Delayed</Badge>;
  return <Badge tone="good">On time</Badge>;
}

export default function Trucks() {
  const [limit, setLimit] = useState(30);
  const [shown, setShown] = useState("ALL");
  const trucks = useLoad(() => api.get("/trucks", { params: { limit } }), [limit]);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function act(request, describe) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await request();
      setMessage(describe(response.data));
      trucks.reload();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  const receive = (id) => act(() => api.post(`/transfers/${id}/receive`), (body) =>
    `${id} received at ${dateTime(body.received_at)}. The stock is on the shelf and no longer inbound. ${body.shipped.length} waiting order${body.shipped.length === 1 ? "" : "s"} shipped.`);
  const delay = (id) => act(() => api.post(`/transfers/${id}/delay`, { delay_days: 2 }), (body) =>
    `${id} is 2 days later. Holds kept ${body.KEEP}, moved ${body.REALLOCATE}, re-promised ${body.REPROMISE}, cancelled ${body.CANCEL}.`);
  const overdue = () => act(() => api.post("/trucks/handle-overdue"), (body) =>
    `Checked every truck with paid orders on it. ${body.handled.length} needed something done.`);

  const data = trucks.data;
  const loaded = data?.trucks || [];
  const rows = loaded.filter((truck) => shown === "ALL" || stateOf(truck, data.as_of) === shown);
  const countOf = (state) => loaded.filter((truck) => stateOf(truck, data.as_of) === state).length;

  return (
    <>
      <PageHeader eyebrow="Store and warehouse operator" title="Trucks in transit"
                  actions={<button type="button" className="btn btn-outline-secondary" disabled={busy} onClick={overdue}>Check overdue trucks</button>}>
        Stock leaves the source when a truck is dispatched and reaches no shelf until it is received, so it is
        counted once. An order held on a truck can’t ship until it arrives.
      </PageHeader>

      <ErrorNote text={error} />
      {message && <div className="note note-good" role="status">{message}</div>}

      {data && (
        <Stats>
          <Stat lead label="Trucks on the road" value={number(data.total)} note={`As of ${dateTime(data.as_of)}`} />
          <Stat label="Overdue" value={number(countOf("OVERDUE"))} note={`Among the ${loaded.length} due soonest`} />
          <Stat label="Delayed" value={number(countOf("DELAYED"))} note={`Among the ${loaded.length} due soonest`} />
          <Stat label="Units held for orders" value={number(loaded.reduce((sum, truck) => sum + truck.units_held, 0))} note={`On the ${loaded.length} due soonest`} />
        </Stats>
      )}

      <Panel title="Soonest due first" note={data ? `Showing ${number(rows.length)} of ${number(data.total)} on the road` : " "}>
        <div className="table-tools">
          <div>
            <span className="form-label d-block">State</span>
            <Segmented label="Filter by state" options={STATES} value={shown} onChange={setShown} />
          </div>
        </div>

        <ErrorNote text={trucks.error} onRetry={trucks.reload} />
        {trucks.loading && !data && <Loading rows={8} />}
        {data && rows.length === 0 && (
          <Empty title={shown === "ALL" ? "No trucks are on the road" : "No trucks in this state"}>
            {shown === "ALL" ? "Dispatched transfers appear here until they are received." : "Only the trucks due soonest are loaded here."}
          </Empty>
        )}
        {data && rows.length > 0 && (
          <div className="table-wrap">
            <table className="table data-table stack-on-phone">
              <thead>
                <tr><th>Truck</th><th>SKU</th><th>Route</th><th className="num">Units</th><th className="num">Held</th><th>Left</th><th>Current ETA</th><th>State</th><th><span className="visually-hidden">Actions</span></th></tr>
              </thead>
              <tbody>
                {rows.map((truck) => (
                  <tr key={truck.shipment_id}>
                    <td className="nowrap cell-title strong">{truck.shipment_id}</td>
                    <td className="nowrap" data-label="SKU"><a href={`#/ledger?sku=${truck.sku_id}&location=${truck.to_location_id}`}>{truck.sku_id}</a></td>
                    <td className="nowrap small-text" data-label="Route">{truck.from_location_id} → {truck.to_location_id}</td>
                    <td className="num" data-label="Units">{number(truck.qty)}</td>
                    <td className={`num ${truck.units_held > 0 ? "strong" : ""}`} data-label="Held">{number(truck.units_held)}</td>
                    <td className="nowrap" data-label="Left">{dateTime(truck.created_at)}</td>
                    <td className="nowrap" data-label="Current ETA">{dateTime(truck.current_eta_at)}</td>
                    <td data-label="State"><StateBadge state={stateOf(truck, data.as_of)} /></td>
                    <td className="nowrap cell-actions">
                      <button type="button" className="btn btn-sm btn-outline-primary me-1" disabled={busy} onClick={() => receive(truck.shipment_id)}>Receive</button>
                      <button type="button" className="btn btn-sm btn-outline-secondary" disabled={busy} onClick={() => delay(truck.shipment_id)} title="Give this truck a new ETA, 2 days later">Delay</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && loaded.length < data.total && limit < 200 && (
          <div className="pager">
            <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setLimit(200)}>Load up to 200 trucks</button>
          </div>
        )}
      </Panel>
    </>
  );
}
