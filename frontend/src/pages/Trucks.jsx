import { useState } from "react";
import api, { errorText } from "../api.js";
import { Badge, Empty, ErrorNote, Loading, PageHeader, Panel, useLoad } from "../components/ui.jsx";
import { dateTime, number } from "../format.js";

export default function Trucks() {
  const trucks = useLoad(() => api.get("/trucks", { params: { limit: 30 } }), []);
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

  const now = trucks.data?.as_of;

  return (
    <>
      <PageHeader eyebrow="Store and warehouse operator" title="Stock on the road"
                  actions={<button type="button" className="btn btn-sm btn-outline-secondary" disabled={busy} onClick={overdue}>Check overdue trucks</button>}>
        Stock leaves the source when a truck is dispatched and reaches no shelf until it is received, so it is
        counted once. An order held on a truck can’t ship until it arrives.
      </PageHeader>

      <ErrorNote text={error} />
      {message && <div className="note note-good" role="status">{message}</div>}

      <Panel title="Trucks in transit" note={trucks.data ? `${number(trucks.data.total)} on the road, soonest due first. Showing ${trucks.data.trucks.length}.` : " "}>
        {trucks.error && <ErrorNote text={trucks.error} />}
        {trucks.loading && !trucks.data && <Loading />}
        {trucks.data && trucks.data.trucks.length === 0 && <Empty>No trucks are on the road.</Empty>}
        {trucks.data && trucks.data.trucks.length > 0 && (
          <div className="table-wrap">
            <table className="table data-table">
              <thead>
                <tr><th>Truck</th><th>SKU</th><th>Route</th><th className="num">Units</th><th className="num">Held</th><th>Left</th><th>Current ETA</th><th>State</th><th /></tr>
              </thead>
              <tbody>
                {trucks.data.trucks.map((truck) => {
                  const late = truck.current_eta_at !== truck.original_eta_at;
                  const overdueNow = truck.current_eta_at < now;
                  return (
                    <tr key={truck.shipment_id}>
                      <td className="nowrap">{truck.shipment_id}</td>
                      <td className="nowrap"><a href={`#/ledger?sku=${truck.sku_id}&location=${truck.to_location_id}`}>{truck.sku_id}</a></td>
                      <td className="nowrap small-text">{truck.from_location_id} → {truck.to_location_id}</td>
                      <td className="num">{number(truck.qty)}</td>
                      <td className="num">{number(truck.units_held)}</td>
                      <td className="nowrap">{dateTime(truck.created_at)}</td>
                      <td className="nowrap">{dateTime(truck.current_eta_at)}</td>
                      <td>
                        {overdueNow ? <Badge tone="bad">Overdue</Badge> : late ? <Badge tone="warn">Delayed</Badge> : <Badge tone="good">On time</Badge>}
                      </td>
                      <td className="nowrap">
                        <button type="button" className="btn btn-sm btn-outline-primary me-1" disabled={busy} onClick={() => receive(truck.shipment_id)}>Receive</button>
                        <button type="button" className="btn btn-sm btn-outline-secondary" disabled={busy} onClick={() => delay(truck.shipment_id)} title="Give this truck a new ETA, 2 days later">Delay</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
