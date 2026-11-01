import { useState } from "react";
import api from "../api.js";
import { Badge, ErrorNote, Loading, PageHeader, Panel, SkuPicker, Tile, useDefaultSku, useLoad } from "../components/ui.jsx";
import { dateTime, money, number, words } from "../format.js";

const CHANNELS = ["WEB", "APP", "MARKETPLACE", "STORE"];

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
      <PageHeader eyebrow="Customer and analyst" title="What can be promised, right now or at any past time">
        Worked out from the ledger and event history every time, never from a stored total, so asking about
        last week gives the answer it would have given last week.
      </PageHeader>

      <Panel>
        <div className="filter-row">
          <div className="filter-grow"><SkuPicker value={sku} onChange={setSku} /></div>
          <div>
            <span className="form-label d-block">Channel</span>
            <div className="btn-group" role="group" aria-label="Channel">
              {CHANNELS.map((code) => (
                <button key={code} type="button" className={`btn btn-sm ${code === channel ? "btn-primary" : "btn-outline-secondary"}`}
                        aria-pressed={code === channel} onClick={() => setChannel(code)}>{words(code)}</button>
              ))}
            </div>
          </div>
          <div>
            <label className="form-label" htmlFor="as-of">As of</label>
            <input id="as-of" type="datetime-local" className="form-control form-control-sm" min="2026-01-01T00:00" max="2026-12-31T00:00"
                   value={asOf} onChange={(event) => setAsOf(event.target.value)} />
          </div>
          {asOf && <button type="button" className="btn btn-sm btn-link align-self-end" onClick={() => setAsOf("")}>Back to latest</button>}
        </div>
      </Panel>

      <ErrorNote text={atp.error} />
      {atp.loading && !atp.data && <Loading />}

      {atp.data && (
        <>
          <div className="sku-line">
            <h2>{sku}</h2>
            {info.data && <p>{info.data.product_name} · {info.data.brand} · {info.data.colour} {info.data.size} · {money(info.data.unit_price)}</p>}
            {info.data && <Badge code={info.data.status} tone={info.data.status === "ACTIVE" ? "good" : "warn"} />}
          </div>

          <div className="tiles tiles-four">
            <Tile label={`Promisable on ${words(channel).toLowerCase()}`} value={number(atp.data.network.promisable)} note="Across every location it can sell from" />
            <Tile label="ATP before safety stock and buffers" value={number(atp.data.network.atp)} note="On hand − reserved − blocked + inbound" />
            <Tile label="On hand in the network" value={number(atp.data.network.on_hand)} note="Including damaged stock" />
            <Tile label="On the road" value={number(atp.data.network.in_transit)} note="Counted once, at neither end" />
          </div>

          <Panel title="Every term, by location" note={`At ${dateTime(atp.data.as_of)}. Best to promise from first.`}>
            <div className="formula" aria-label="The formula">
              <span>On hand</span><b>−</b><span>Reserved</span><b>−</b><span>Blocked</span><b>+</b><span>Inbound</span><b>=</b><span className="strong">ATP</span>
              <b>−</b><span>Safety</span><b>−</b><span>Buffer</span><b>=</b><span className="strong">Promisable</span>
            </div>
            {rows.length === 0 && <p className="quiet-note">This channel can’t sell this SKU from any location at that time.</p>}
            {rows.length > 0 && (
              <div className="table-wrap">
                <table className="table data-table">
                  <thead>
                    <tr>
                      <th>Location</th><th className="num">On hand</th><th className="num">Reserved</th><th className="num">Blocked</th>
                      <th className="num">Inbound</th><th className="num">ATP</th><th className="num">Safety</th><th className="num">Buffer</th><th className="num">Promisable</th><th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.location_id}>
                        <td>
                          <strong>{names[row.location_id] || row.location_id}</strong>
                          <small className="cell-note">{row.location_id} · {words(row.location_type)} · {words(row.region)}</small>
                        </td>
                        <td className="num">{number(row.on_hand)}</td>
                        <td className="num">{number(row.reserved)}</td>
                        <td className="num">{number(row.blocked)}</td>
                        <td className="num">{number(row.inbound)}</td>
                        <td className="num strong">{number(row.atp)}</td>
                        <td className="num">{number(row.safety_stock)}</td>
                        <td className="num">{number(row.buffer_units)}</td>
                        <td className="num strong">{number(row.promisable)}</td>
                        <td><a className="row-link" href={`#/ledger?sku=${sku}&location=${row.location_id}`}>Ledger</a></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="table-foot">
              {channel === "MARKETPLACE" && "A marketplace never sells stock still on a truck, so inbound doesn’t reach its promisable number. "}
              {channel === "STORE" && "In store there is no safety stock or buffer. Walk-in sales still can’t take units held for an online order. "}
              A truck past its ETA stops counting as inbound, so ATP can go below zero while a paid hold waits on it.
            </p>
          </Panel>
        </>
      )}
    </>
  );
}
