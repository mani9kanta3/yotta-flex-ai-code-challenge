import { useState } from "react";
import api, { errorText } from "../api.js";
import { Badge, Empty, ErrorNote, Loading, PageHeader, Panel, Stat, Stats, Verdict, useLoad } from "../components/ui.jsx";
import { dateTime, money, number, words } from "../format.js";

// Puts the order, hold and truck events into one story, oldest first. The kinds stay labelled.
function timelineOf(trace) {
  const rows = [
    ...trace.order_events.map((event) => ({
      at: event.occurred_at, id: `o${event.event_id}`, kind: event.order_line_id ? "Line" : "Order",
      what: event.order_line_id || trace.order.order_id, to: event.to_status, reason: event.reason_code,
    })),
    ...trace.reservation_events.map((event) => ({
      at: event.occurred_at, id: `r${event.event_id}`, kind: "Hold", what: event.reservation_id, to: event.to_status, reason: event.reason_code,
      promise: event.promised_ship_by,
    })),
    ...trace.shipment_events.map((event) => ({
      at: event.occurred_at, id: `s${event.event_id}`, kind: "Shipment", what: event.shipment_id, to: event.to_status, reason: event.reason_code,
      eta: event.eta_at,
    })),
    ...trace.movements.map((row) => ({
      at: row.occurred_at, id: row.movement_id, kind: "Ledger", what: row.movement_id, to: row.movement_type,
      reason: `${row.quantity} from ${words(row.stock_bucket).toLowerCase()} at ${row.location_id}`,
    })),
    ...trace.returns.map((row) => ({
      at: row.occurred_at, id: row.movement_id, kind: "Ledger", what: row.movement_id, to: row.movement_type,
      reason: `+${row.quantity} ${words(row.reason_code).toLowerCase()}`,
    })),
  ];
  return rows.sort((a, b) => a.at.localeCompare(b.at));
}

// How far the order has got, read from its own events and parcels, not guessed from its status.
function stepsOf(trace) {
  const reached = new Set(trace.order_events.filter((event) => !event.order_line_id).map((event) => event.to_status));
  const parcels = trace.shipments.filter((row) => row.shipment_type === "CUSTOMER");
  const steps = [
    { label: "Placed", done: reached.has("PLACED") },
    { label: "Allocated", done: reached.has("ALLOCATED") },
    { label: "Paid", done: reached.has("CONFIRMED") },
    { label: "Shipped", done: reached.has("FULFILLED") },
    { label: "Delivered", done: parcels.length > 0 && parcels.every((row) => row.status === "DELIVERED") },
  ];
  const status = trace.order.status;
  if (status !== "CANCELLED" && status !== "REJECTED") return steps;
  return [...steps.filter((step) => step.done), { label: words(status), done: true, ended: true }];
}

function OrderSteps({ trace }) {
  return (
    <ol className="steps">
      {stepsOf(trace).map((step) => (
        <li key={step.label} className={`${step.done ? "step-done" : ""} ${step.ended ? "step-ended" : ""}`}>
          <span className="step-mark" aria-hidden="true">{step.ended ? "×" : step.done ? "✓" : ""}</span>
          <span>{step.label}<span className="visually-hidden">{step.done ? ", done" : ", not yet"}</span></span>
        </li>
      ))}
    </ol>
  );
}

function Actions({ trace, orderId, onChanged }) {
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const status = trace.order.status;
  const activeHolds = trace.reservations.filter((row) => row.status === "ACTIVE");
  const parcels = trace.shipments.filter((row) => row.shipment_type === "CUSTOMER" && row.status === "IN_TRANSIT");
  const trucks = trace.shipments.filter((row) => row.shipment_type === "TRANSFER" && row.status === "IN_TRANSIT");

  async function run(request) {
    setBusy(true);
    setError(null);
    try {
      await request();
      onChanged();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  const post = (path, body) => () => run(() => api.post(path, body));
  const buttons = [];
  if (status === "ALLOCATED") buttons.push(["Confirm payment", post(`/orders/${orderId}/confirm`), "btn-primary"]);
  if (status === "CONFIRMED") buttons.push(["Ship it", post(`/orders/${orderId}/ship`), "btn-primary"]);
  if (trucks.length > 0 && status === "CONFIRMED") {
    trucks.forEach((truck) => buttons.push([`Receive truck ${truck.shipment_id}`, post(`/transfers/${truck.shipment_id}/receive`), "btn-outline-primary"]));
  }
  if (activeHolds.length > 0) {
    buttons.push(["Let the hold expire", post("/reservations/expire", { as_of: activeHolds[0].expires_at }), "btn-outline-secondary"]);
  }
  if (status === "ALLOCATED" || status === "CONFIRMED") buttons.push(["Cancel order", post(`/orders/${orderId}/cancel`), "btn-outline-danger"]);
  parcels.forEach((parcel) => buttons.push([`Deliver ${parcel.shipment_id}`, post(`/shipments/${parcel.shipment_id}/deliver`), "btn-outline-primary"]));

  if (buttons.length === 0) return null;
  return (
    <div className="action-area">
      <p className="action-title">Next step</p>
      <div className="action-row">
        {buttons.map(([label, onClick, style]) => (
          <button key={label} type="button" className={`btn ${style}`} disabled={busy} onClick={onClick}>{label}</button>
        ))}
      </div>
      <ErrorNote text={error} />
    </div>
  );
}

function ReturnForm({ sale, onChanged }) {
  const [qty, setQty] = useState(1);
  const [condition, setCondition] = useState("GOOD");
  const [error, setError] = useState(null);

  async function send(event) {
    event.preventDefault();
    setError(null);
    try {
      await api.post("/returns", { sale_movement_id: sale.movement_id, qty: Number(qty), condition });
      onChanged();
    } catch (failure) {
      setError(errorText(failure));
    }
  }

  return (
    <form className="return-form" onSubmit={send}>
      <label className="visually-hidden" htmlFor={`qty-${sale.movement_id}`}>Units to return</label>
      <input id={`qty-${sale.movement_id}`} type="number" min="1" className="form-control form-control-sm" value={qty} onChange={(event) => setQty(event.target.value)} />
      <label className="visually-hidden" htmlFor={`cond-${sale.movement_id}`}>Condition</label>
      <select id={`cond-${sale.movement_id}`} className="form-select form-select-sm" value={condition} onChange={(event) => setCondition(event.target.value)}>
        <option value="GOOD">Good</option>
        <option value="DAMAGED">Damaged</option>
      </select>
      <button type="submit" className="btn btn-sm btn-outline-secondary">Record return</button>
      <ErrorNote text={error} />
    </form>
  );
}

function Candidates({ decision }) {
  const rows = decision.candidates || [];
  if (rows.length === 0) return <Empty title="No candidates">No location could sell this SKU on this channel.</Empty>;
  return (
    <div className="table-wrap">
      <table className="table data-table compact-table stack-on-phone">
        <thead>
          <tr><th>Considered, best first</th><th className="num">ATP</th><th className="num">Safety</th><th className="num">Buffer</th><th className="num">From the shelf</th><th className="num">Promisable</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.location_id} className={row.location_id === decision.chosen_location_id ? "row-chosen" : ""}>
              <td className="cell-title">{row.location_id}{row.location_id === decision.chosen_location_id && <small className="chosen-tag"> chosen</small>}</td>
              <td className="num" data-label="ATP">{number(row.atp)}</td>
              <td className="num" data-label="Safety">{number(row.safety_stock)}</td>
              <td className="num" data-label="Buffer">{number(row.buffer_units)}</td>
              <td className="num" data-label="From the shelf">{number(row.from_on_hand)}</td>
              <td className="num strong" data-label="Promisable">{number(row.promisable)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function OrderTrace({ orderId }) {
  const trace = useLoad(() => api.get(`/orders/${orderId}/trace`), [orderId]);
  const backLink = <a href="#/orders">← All orders</a>;

  if (trace.error) {
    return (<><PageHeader eyebrow={backLink} title={orderId} /><ErrorNote text={trace.error} onRetry={trace.reload} /></>);
  }
  if (!trace.data) return <Loading page />;

  const data = trace.data;
  const order = data.order;
  const timeline = timelineOf(data);
  const expiresAt = data.reservations.find((row) => row.status === "ACTIVE")?.expires_at;

  return (
    <>
      <PageHeader eyebrow={backLink} title={order.order_id} actions={<Badge code={order.status} />}>
        Placed {dateTime(order.placed_at)} on {words(order.channel_code).toLowerCase()}, shipping to {words(order.ship_to_region).toLowerCase()}.
        {expiresAt && ` The hold runs out at ${dateTime(expiresAt)}.`}
      </PageHeader>

      <Panel className="progress-panel">
        <OrderSteps trace={data} />
        <Actions trace={data} orderId={orderId} onChanged={trace.reload} />
      </Panel>

      <Stats>
        <Stat lead label="Order total" value={money(order.order_total)} note={`${data.lines.length} line${data.lines.length === 1 ? "" : "s"}`} />
        <Stat label="Holds" value={number(data.reservations.length)} note={[...new Set(data.reservations.map((row) => words(row.status)))].join(", ") || "None"} />
        <Stat label="Ledger rows" value={number(data.movements.length + data.returns.length)} note="Stock that really moved" />
        <Stat label="Traceable end to end" value={<Verdict good={data.fully_traceable}>{data.fully_traceable ? "Yes" : "No"}</Verdict>} note="Each used hold leads to a sale and a parcel" />
      </Stats>

      <div className="grid-trace">
        <div>
          <Panel title="Lines and decisions" note="What was asked for, and why this location">
            {data.lines.map((line) => (
              <div key={line.order_line_id} className="line-block">
                <div className="line-title">
                  <div>
                    <strong>{line.product_name}</strong>
                    <small className="cell-note">
                      <a href={`#/availability?sku=${line.sku_id}`}>{line.sku_id}</a> · {line.colour} {line.size} · {number(line.qty)} × {money(line.unit_price)}
                    </small>
                  </div>
                  <Badge code={line.status} />
                </div>
                {data.decisions.filter((decision) => decision.order_line_id === line.order_line_id).map((decision) => (
                  <div key={decision.decision_id} className="decision">
                    <p><Badge code={decision.decision_code} /> <span>{words(decision.reason_code)}</span> <small className="text-muted-soft">{decision.decision_id} · {dateTime(decision.decided_at)}</small></p>
                    <Candidates decision={decision} />
                  </div>
                ))}
              </div>
            ))}
          </Panel>

          {data.reservations.length > 0 && (
            <Panel title="Holds and allocations">
              <div className="table-wrap">
                <table className="table data-table compact-table stack-on-phone">
                  <thead><tr><th>Hold and allocation</th><th>At</th><th className="num">Units</th><th>From</th><th>First promise</th><th>Status</th></tr></thead>
                  <tbody>
                    {data.reservations.map((row) => (
                      <tr key={row.reservation_id}>
                        <td className="nowrap cell-title">{row.reservation_id}<small className="cell-note">{row.allocation_id || "No allocation"}</small></td>
                        <td className="nowrap" data-label="At">{row.location_id || "–"}</td>
                        <td className="num" data-label="Units">{number(row.qty)}</td>
                        <td className="nowrap" data-label="From">{row.supply_type === "INBOUND" ? `Truck ${row.inbound_shipment_id}` : "Shelf"}</td>
                        <td className="nowrap" data-label="First promise">{dateTime(row.promised_ship_by)}</td>
                        <td data-label="Status"><Badge code={row.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {data.movements.length > 0 && (
            <Panel title="Stock leaving the shelf" note="Immutable ledger rows. A return has to name its sale, and can’t pass what was sold.">
              {data.movements.map((sale) => (
                <div key={sale.movement_id} className="sale-row">
                  <div>
                    <strong>{sale.movement_id}</strong>
                    <small className="cell-note">
                      {number(-sale.quantity)} of <a href={`#/ledger?sku=${sale.sku_id}&location=${sale.location_id}`}>{sale.sku_id} at {sale.location_id}</a>, {words(sale.stock_bucket).toLowerCase()} bucket, parcel {sale.shipment_id}
                    </small>
                    {sale.stock_bucket === "BLOCKED" && <p className="note note-bad mt-2 mb-0">This sale came from damaged stock. It is one of the problems the detectors look for.</p>}
                  </div>
                  <ReturnForm sale={sale} onChanged={trace.reload} />
                </div>
              ))}
            </Panel>
          )}
        </div>

        <Panel title="Everything that happened" note={`${timeline.length} events, oldest first. None of them can be changed.`}>
          <ol className="timeline">
            {timeline.map((row) => (
              <li key={row.id}>
                <span className={`timeline-dot kind-${row.kind.toLowerCase()}`} aria-hidden="true" />
                <div>
                  <p className="timeline-main"><strong>{words(row.to)}</strong> <small>{row.kind}</small></p>
                  <p className="timeline-sub">{words(row.reason)}{row.eta && ` · new ETA ${dateTime(row.eta)}`}</p>
                  <p className="timeline-sub">{dateTime(row.at)} · {row.what}</p>
                </div>
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </>
  );
}
