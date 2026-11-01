import { useEffect, useState } from "react";
import api from "../api.js";
import { StockLine } from "../components/charts.jsx";
import { Badge, ErrorNote, Loading, PageHeader, Panel, SkuPicker, Tile, Verdict, useDefaultSku, useLoad } from "../components/ui.jsx";
import { dateTime, number, words } from "../format.js";

function signed(value) {
  return value > 0 ? `+${number(value)}` : number(value);
}

export default function Ledger({ skuId, locationId }) {
  const [sku, setSku] = useDefaultSku(skuId);
  const [location, setLocation] = useState(locationId || "");
  const [limit, setLimit] = useState(25);

  const info = useLoad(() => (sku ? api.get(`/skus/${sku}`) : Promise.resolve({ data: null })), [sku]);
  const places = info.data?.stocked_at || [];

  // When the SKU changes, keep the location if this SKU is stocked there, or take its first.
  useEffect(() => {
    if (places.length > 0 && !places.some((place) => place.location_id === location)) {
      setLocation(places[0].location_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info.data]);

  useEffect(() => {
    if (skuId) setSku(skuId);
    if (locationId) setLocation(locationId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skuId, locationId]);

  const inPlaces = places.some((place) => place.location_id === location);
  const ledger = useLoad(
    () => (sku && location && inPlaces ? api.get("/movements", { params: { sku_id: sku, location_id: location, limit } }) : Promise.resolve({ data: null })),
    [sku, location, limit, inPlaces]
  );
  const data = ledger.data;

  return (
    <>
      <PageHeader eyebrow="Store and warehouse operator" title="Every movement of one SKU at one location">
        The ledger can only be added to, and the database itself refuses a change. Stock after each row is
        worked out from the opening balance, then checked against the stored closing balance.
      </PageHeader>

      <Panel>
        <div className="filter-row">
          <div className="filter-grow"><SkuPicker value={sku} onChange={setSku} /></div>
          <div className="filter-grow">
            <label className="form-label" htmlFor="ledger-location">Location</label>
            <select id="ledger-location" className="form-select" value={location} onChange={(event) => setLocation(event.target.value)}>
              {places.map((place) => <option key={place.location_id} value={place.location_id}>{place.location_name} ({place.location_id})</option>)}
            </select>
          </div>
        </div>
      </Panel>

      <ErrorNote text={ledger.error} />
      {ledger.loading && !data && <Loading />}

      {data && (
        <>
          <div className="tiles tiles-four">
            <Tile label="On hand now" value={number(data.current_on_hand)} note={`${number(data.current_blocked)} blocked, opened at ${number(data.opening.on_hand)}, ${number(data.total_rows)} rows since`} />
            <Tile label="Ledger at the closing snapshot" value={number(data.at_snapshot_on_hand)} note={`${number(data.at_snapshot_blocked)} blocked. Opening plus rows up to then`} />
            <Tile label="Stored closing balance" value={data.stored_closing ? number(data.stored_closing.on_hand) : "–"} note={data.stored_closing ? `${number(data.stored_closing.blocked)} blocked, at ${dateTime(data.stored_closing.as_of_at)}` : "None stored"} />
            <div className="tile">
              <p className="tile-label">Reconciles</p>
              <p className="tile-value"><Verdict good={data.reconciles}>{data.reconciles ? "Yes" : "No"}</Verdict></p>
              <p className="tile-note">{data.reconciles ? "The ledger at that moment equals the stored balance" : "The stored balance differs from the ledger at that moment. ATP keeps using the ledger."}</p>
            </div>
          </div>

          <Panel title="On hand over time" note="Move across the chart for the stock at any point">
            <StockLine points={data.history} opening={data.opening.on_hand} openingAt={data.opening.as_of_at} />
          </Panel>

          <Panel title="Ledger rows" note={`Newest first. Showing ${number(data.rows.length)} of ${number(data.total_rows)}.`}>
            <div className="table-wrap">
              <table className="table data-table">
                <thead>
                  <tr><th>When</th><th>Movement</th><th>Bucket</th><th className="num">Change</th><th className="num">On hand after</th><th className="num">Blocked after</th><th>Reason</th><th>Document</th><th>Link</th></tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.movement_id}>
                      <td className="nowrap">{dateTime(row.occurred_at)}<small className="cell-note">{row.movement_id}</small></td>
                      <td>{words(row.movement_type)}</td>
                      <td>{row.stock_bucket === "BLOCKED" ? <Badge code="BLOCKED" tone="warn" /> : words(row.stock_bucket)}</td>
                      <td className={`num strong ${row.quantity < 0 ? "qty-out" : "qty-in"}`}>{signed(row.quantity)}</td>
                      <td className="num">{number(row.on_hand_after)}</td>
                      <td className="num">{number(row.blocked_after)}</td>
                      <td>{words(row.reason_code)}</td>
                      <td className="nowrap small-text">{row.source_doc}</td>
                      <td className="nowrap">
                        {row.order_id && <a href={`#/orders/${row.order_id}`}>Order</a>}
                        {!row.order_id && row.related_movement_id && <span className="small-text">Sale {row.related_movement_id}</span>}
                        {!row.order_id && row.shipment_id && <span className="small-text">{row.shipment_id}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data.rows.length < data.total_rows && limit < 200 && (
              <div className="pager">
                <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setLimit(200)}>Show up to 200 rows</button>
              </div>
            )}
          </Panel>
        </>
      )}
    </>
  );
}
