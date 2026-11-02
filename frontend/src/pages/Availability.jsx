import { useState } from "react";
import api from "../api.js";
import { Badge, Empty, ErrorNote, Loading, PageHeader, Panel, Segmented, SkuPicker, Stat, Stats, useDefaultSku, useLoad } from "../components/ui.jsx";
import { dateTime, money, number, words } from "../format.js";

const CHANNELS = ["WEB", "APP", "MARKETPLACE", "STORE"].map((code) => ({ value: code, label: words(code) }));

export default function Availability({ skuId }) {
  const [sku, setSku] = useDefaultSku(skuId);
  const [channel, setChannel] = useState("WEB");
  const [asOf, setAsOf] = useState("");

  const info = useLoad(() => (sku ? api.get(`/skus/${sku}`) : Promise.resolve({ data: null })), [sku]);
  const atp = useLoad(
    () => (sku ? api.get("/atp", { params: { sku_id: sku, channel, as_of: asOf || undefined } }) : Promise.resolve({ data: null })),
    [sku, channel, asOf]
  );
  const names = Object.fromEntries((info.data?.stocked_at || []).map((row) => [row.location_id, row.location_name]));
  const rows = [...(atp.data?.locations || [])].sort((a, b) => b.promisable - a.promisable || a.location_id.localeCompare(b.location_id));

  return (
    <>
      <PageHeader eyebrow="Customer and analyst" title="Availability">
        What can be promised, now or at any past time. It is worked out from the ledger and events every
        time, never from a stored total, so asking about last week gives last week’s answer.
      </PageHeader>

      <Panel className="filter-panel">
        <div className="filter-row">
          <div className="filter-grow"><SkuPicker value={sku} onChange={setSku} /></div>
          <div>
            <span className="form-label d-block">Channel</span>
            <Segmented label="Channel" options={CHANNELS} value={channel} onChange={setChannel} />
          </div>
          <div>
            <label className="form-label" htmlFor="as-of">As of</label>
            <input id="as-of" type="datetime-local" className="form-control" min="2026-01-01T00:00" max="2026-12-31T00:00"
                   value={asOf} onChange={(event) => setAsOf(event.target.value)} />
          </div>
          {asOf && <button type="button" className="btn btn-link align-self-end" onClick={() => setAsOf("")}>Back to latest</button>}
        </div>
      </Panel>

      <ErrorNote text={atp.error} onRetry={atp.reload} />
      {atp.loading && !atp.data && <Panel><Loading /></Panel>}

      {atp.data && (
        <div className={atp.loading ? "refreshing" : ""}>
          <div className="sku-line">
            <h2>{info.data ? info.data.product_name : sku}</h2>
            {info.data && <p>{sku} · {info.data.brand} · {info.data.colour} {info.data.size} · {money(info.data.unit_price)}</p>}
            {info.data && <Badge code={info.data.status} tone={info.data.status === "ACTIVE" ? "good" : "warn"} />}
          </div>

          <Stats>
            <Stat lead label={`Promisable on ${words(channel).toLowerCase()}`} value={number(atp.data.network.promisable)} note="Across every location it can sell from" />
            <Stat label="ATP" value={number(atp.data.network.atp)} note="Before safety stock and buffers" />
            <Stat label="On hand in the network" value={number(atp.data.network.on_hand)} note="Including damaged stock" />
            <Stat label="On the road" value={number(atp.data.network.in_transit)} note="Counted once, at neither end" />
          </Stats>

          <Panel title="By location" note={`At ${dateTime(atp.data.as_of)}. Best to promise from first.`}>
            {rows.length === 0 && <Empty title="Nothing to promise here">This channel can’t sell this SKU from any location at that time.</Empty>}
            {rows.length > 0 && (
              <div className="table-wrap">
                <table className="table data-table stack-on-phone stack-two">
                  <thead>
                    <tr>
                      <th>Location</th><th className="num">On hand</th><th className="num">Reserved</th><th className="num">Blocked</th>
                      <th className="num">Inbound</th><th className="num">ATP</th><th className="num">Channel ATP</th><th className="num">Safety</th><th className="num">Buffer</th>
                      <th className="num col-lead">Promisable</th><th><span className="visually-hidden">Ledger link</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.location_id}>
                        <td className="cell-title">
                          <strong>{names[row.location_id] || row.location_id}</strong>
                          <small className="cell-note">{row.location_id} · {words(row.location_type)} · {words(row.region)}</small>
                        </td>
                        <td className="num" data-label="On hand">{number(row.on_hand)}</td>
                        <td className="num" data-label="Reserved">{number(row.reserved)}</td>
                        <td className="num" data-label="Blocked">{number(row.blocked)}</td>
                        <td className="num" data-label="Inbound">{number(row.inbound)}</td>
                        <td className={`num ${row.atp < 0 ? "qty-bad" : ""}`} data-label="ATP">{number(row.atp)}</td>
                        <td className="num" data-label="Channel ATP">{number(row.channel_atp)}</td>
                        <td className="num" data-label="Safety">{number(row.safety_stock)}</td>
                        <td className="num" data-label="Buffer">{number(row.buffer_units)}</td>
                        <td className="num col-lead" data-label="Promisable">{number(row.promisable)}</td>
                        <td className="cell-link"><a href={`#/ledger?sku=${sku}&location=${row.location_id}`}>Ledger</a></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="formula" aria-label="The formula">
              <p className="formula-title">How promisable is worked out</p>
              <p>On hand − Reserved − Blocked + Inbound = <strong>ATP</strong></p>
              <p>ATP − Inbound this channel can’t sell = <strong>Channel ATP</strong></p>
              <p>Channel ATP − Safety − Buffer = <strong>Promisable</strong>, never below 0</p>
            </div>
            <p className="table-foot">
              {channel === "MARKETPLACE" && "A marketplace never sells stock still on a truck, so inbound doesn’t reach its promisable number. "}
              {channel === "STORE" && "In store there is no safety stock or buffer. Walk-in sales still can’t take units held for an online order. "}
              A truck past its ETA stops counting as inbound, so ATP can go below zero while a paid hold waits on it.
            </p>
          </Panel>
        </div>
      )}
    </>
  );
}
