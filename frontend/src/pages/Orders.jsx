import { useState } from "react";
import api, { errorText } from "../api.js";
import { Badge, Empty, ErrorNote, Loading, PageHeader, Panel, SkuPicker, useDefaultSku, useLoad } from "../components/ui.jsx";
import { dateTime, money, number, words } from "../format.js";

const REGIONS = ["SOUTH", "NORTH", "EAST", "WEST", "CENTRAL"];
const DECISIONS = ["", "SINGLE", "SPLIT", "INBOUND", "REJECTED", "REALLOCATED"];
const PAGE_SIZE = 15;

function NewOrder({ onPlaced }) {
  const [sku, setSku] = useDefaultSku();
  const [qty, setQty] = useState(1);
  const [channel, setChannel] = useState("WEB");
  const [region, setRegion] = useState("SOUTH");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function place(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await api.post("/orders", {
        channel_code: channel, ship_to_region: region, lines: [{ sku_id: sku, qty: Number(qty) }],
      });
      setResult(response.data);
      onPlaced();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Place an order" note="Goes through the same locked allocation as everything else, and really writes to the database">
      <form onSubmit={place} className="order-form">
        <div className="form-wide"><SkuPicker value={sku} onChange={setSku} /></div>
        <div>
          <label className="form-label" htmlFor="qty">Quantity</label>
          <input id="qty" type="number" min="1" max="10000" className="form-control" value={qty} onChange={(event) => setQty(event.target.value)} required />
        </div>
        <div>
          <label className="form-label" htmlFor="channel">Channel</label>
          <select id="channel" className="form-select" value={channel} onChange={(event) => setChannel(event.target.value)}>
            <option value="WEB">Web</option>
            <option value="APP">App</option>
            <option value="MARKETPLACE">Marketplace</option>
          </select>
        </div>
        <div>
          <label className="form-label" htmlFor="region">Ship to</label>
          <select id="region" className="form-select" value={region} onChange={(event) => setRegion(event.target.value)}>
            {REGIONS.map((code) => <option key={code} value={code}>{words(code)}</option>)}
          </select>
        </div>
        <div className="form-action">
          <button type="submit" className="btn btn-primary" disabled={busy || !sku}>{busy ? "Placing…" : "Place order"}</button>
        </div>
      </form>
      <p className="table-foot">Ask for more than is available to see the oversell refusal. A refused order is kept, with its reason.</p>

      <ErrorNote text={error} />
      {result && (
        <div className={`note ${result.status === "REJECTED" ? "note-bad" : "note-good"}`} role="status">
          <div className="note-title">
            <strong>{result.order_id}</strong> <Badge code={result.status} /> <span>{money(result.order_total)}</span>
            <a className="ms-auto" href={`#/orders/${result.order_id}`}>Open its trace</a>
          </div>
          {result.lines.map((line) => (
            <p key={line.order_line_id} className="mb-0">
              {line.sku_id} × {line.qty}: <strong>{words(line.decision_code)}</strong>, {words(line.reason_code).toLowerCase()}
              {line.allocations.map((part) => (
                <span key={part.reservation_id} className="d-block cell-note">
                  {part.qty} held at {part.location_id} from {part.supply_type === "INBOUND" ? `truck ${part.inbound_shipment_id}` : "the shelf"},
                  {" "}until {dateTime(part.expires_at)}. Ship by {dateTime(part.promised_ship_by)}.
                </span>
              ))}
            </p>
          ))}
        </div>
      )}
    </Panel>
  );
}

export default function Orders() {
  const [decision, setDecision] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const orders = useLoad(
    () => api.get("/orders", { params: { decision: decision || undefined, search: search || undefined, limit: PAGE_SIZE, offset } }),
    [decision, search, offset]
  );

  function chooseDecision(code) {
    setDecision(code);
    setOffset(0);
  }

  return (
    <>
      <PageHeader eyebrow="Customer and analyst" title="Orders and the decision behind each one">
        Every line records which location was chosen and why, including the ones that were refused.
      </PageHeader>

      <NewOrder onPlaced={orders.reload} />

      <Panel title="All orders" note={orders.data ? `${number(orders.data.total)} match. Newest first.` : " "}
             actions={
               <input className="form-control form-control-sm search-box" type="search" aria-label="Search orders by order or SKU"
                      placeholder="Search order or SKU" value={search}
                      onChange={(event) => { setSearch(event.target.value); setOffset(0); }} />
             }>
        <div className="chip-row" role="group" aria-label="Filter by decision">
          {DECISIONS.map((code) => (
            <button key={code} type="button" className={`chip ${code === decision ? "active" : ""}`} aria-pressed={code === decision}
                    onClick={() => chooseDecision(code)}>{code ? words(code) : "All decisions"}</button>
          ))}
        </div>

        <ErrorNote text={orders.error} />
        {orders.loading && !orders.data && <Loading />}
        {orders.data && orders.data.orders.length === 0 && <Empty>No orders match that.</Empty>}
        {orders.data && orders.data.orders.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="table data-table table-hover">
                <thead>
                  <tr><th>Order</th><th>Placed</th><th>Channel</th><th>Ship to</th><th className="num">Lines</th><th>Decision</th><th>Status</th><th className="num">Total</th></tr>
                </thead>
                <tbody>
                  {orders.data.orders.map((order) => (
                    <tr key={order.order_id}>
                      <td><a href={`#/orders/${order.order_id}`} className="id-link">{order.order_id}</a></td>
                      <td className="nowrap">{dateTime(order.placed_at)}</td>
                      <td>{words(order.channel_code)}</td>
                      <td>{words(order.ship_to_region)}</td>
                      <td className="num">{order.lines}</td>
                      <td>{order.decisions ? order.decisions.split(", ").map(words).join(", ") : "–"}</td>
                      <td><Badge code={order.status} /></td>
                      <td className="num">{money(order.order_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <button type="button" className="btn btn-sm btn-outline-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Newer</button>
              <span>{number(offset + 1)} to {number(Math.min(offset + PAGE_SIZE, orders.data.total))} of {number(orders.data.total)}</span>
              <button type="button" className="btn btn-sm btn-outline-secondary" disabled={offset + PAGE_SIZE >= orders.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Older</button>
            </div>
          </>
        )}
      </Panel>
    </>
  );
}
