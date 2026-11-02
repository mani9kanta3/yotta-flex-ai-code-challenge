import api from "../api.js";
import { BarList } from "../components/charts.jsx";
import { ErrorNote, Loading, PageHeader, Panel, Stat, Stats, Verdict, useLoad } from "../components/ui.jsx";
import { number, percent, words } from "../format.js";

const KIND_NAMES = {
  EXPIRY: "A hold expiring",
  TRUCK_ARRIVAL: "A truck arriving",
  CUSTOMER_CANCEL: "A customer cancelling",
  LATE_TRUCK_CANCEL: "A late truck: hold cancelled or moved",
};

function sumOf(rows, key) {
  return rows.reduce((sum, row) => sum + row[key], 0);
}

export default function Evidence() {
  const metrics = useLoad(() => api.get("/metrics"), []);
  if (metrics.error) return (<><PageHeader title="Evidence" /><ErrorNote text={metrics.error} onRetry={metrics.reload} /></>);
  if (!metrics.data) return <Loading page />;

  const m = metrics.data;
  const validation = m.validation;
  const kinds = Object.entries(m.checkpoints?.by_kind || {});
  const detectors = Object.entries(m.detection || {});

  return (
    <>
      <PageHeader eyebrow="Operations manager and reviewer" title="Evidence">
        How we know it works. Everything here was measured on the {m.dataset.scale} dataset, seed {m.dataset.seed},
        by <code>eval.report</code>. Live orders placed on this site don’t change it.
      </PageHeader>

      <Stats>
        <Stat lead label="Clean baseline" value={`${m.clean_baseline?.checks - m.clean_baseline?.failed} of ${m.clean_baseline?.checks}`} note="Checks passed before anything is injected" />
        <Stat label="Final data" value={`${validation?.passed} of ${validation?.checks}`} note={`${validation?.expected_failures.length} failures, each matched to labelled record IDs`} />
        <Stat label="Unexpected failures" value={number(validation?.unexpected_failures.length)} note="A missed label or an unrelated failure counts here" />
        <Stat label="Allocations traceable" value={percent(m.traceability.allocation_share, 0)} note={`${number(m.traceability.allocations_linked)} of ${number(m.traceability.allocations)}`} />
      </Stats>

      <div className="note note-warn" role="note">
        <strong>Only {number(validation?.decisions_checked)} of {number(validation?.decisions_total)} order decisions were recalculated in SQL in this run.</strong>{" "}
        It is a sample, because each one costs several queries. The ATP checkpoints below are a small sample too.
        The validation checks themselves look at every row.
      </div>

      <Panel title="Failures on the final data" note="The validator fails these on purpose, because the labelled problems are in there. It doesn’t read the labels. A separate command compares the failing IDs with them.">
        <div className="table-wrap">
          <table className="table data-table stack-on-phone">
            <thead><tr><th>Check that failed</th><th>Explained by</th><th className="num">Records</th><th>IDs match the labels</th></tr></thead>
            <tbody>
              {validation?.expected_failures.map((row) => (
                <tr key={row.check}>
                  <td className="cell-title">{row.check.charAt(0).toUpperCase() + row.check.slice(1)}</td>
                  <td data-label="Explained by">{words(row.issue)}</td>
                  <td className="num" data-label="Records">{number(row.violations)}</td>
                  <td data-label="IDs match the labels"><Verdict good>Exactly</Verdict></td>
                </tr>
              ))}
              {validation?.unexpected_failures.map((row) => (
                <tr key={row.check}>
                  <td className="cell-title">{row.check}</td>
                  <td data-label="Explained by">{row.problems.join(", ")}</td>
                  <td className="num" data-label="Records">–</td>
                  <td data-label="IDs match the labels"><Verdict good={false}>No</Verdict></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Detector scores against the ground truth" note="Decoys are valid records that look wrong. Flagging one counts against precision.">
        <div className="table-wrap">
          <table className="table data-table stack-on-phone">
            <thead><tr><th>Problem</th><th className="num">Labelled</th><th className="num">Flagged</th><th className="num">Precision</th><th className="num">Recall</th><th className="num">F1</th><th className="num">Decoys</th><th className="num">Decoys flagged</th></tr></thead>
            <tbody>
              {detectors.map(([code, row]) => (
                <tr key={code}>
                  <td className="cell-title">{words(code)}</td>
                  <td className="num" data-label="Labelled">{number(row.labelled)}</td>
                  <td className="num" data-label="Flagged">{number(row.flagged)}</td>
                  <td className="num strong" data-label="Precision">{percent(row.precision, 0)}</td>
                  <td className="num strong" data-label="Recall">{percent(row.recall, 0)}</td>
                  <td className="num" data-label="F1">{percent(row.f1, 0)}</td>
                  <td className="num" data-label="Decoys">{number(row.decoys)}</td>
                  <td className="num" data-label="Decoys flagged">{number(row.decoys_flagged)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="table-foot"><strong>What this doesn’t show:</strong> the anomalies and the detectors come from the same author, and each detector tests the exact rule its anomaly breaks. So 100% shows the two are consistent. It isn’t real-world accuracy.</p>
      </Panel>

      <div className="grid-two">
        <Panel title="ATP worked out a second way" note="Plain Python against SQL, one second before and at moments no order decision looks at">
          <ul className="check-list">
            {kinds.map(([code, row]) => (
              <li key={code}>
                <span>{KIND_NAMES[code] || words(code)}</span>
                <Verdict good={row.matched === row.checked && row.checked > 0}>{row.matched} of {row.checked}</Verdict>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Rules holding across the data">
          <ul className="check-list">
            <li><span>Oversell attempts refused</span><Verdict good={m.oversell.blocked_rate === 1}>{number(m.oversell.blocked)} of {number(m.oversell.attempts)}</Verdict></li>
            <li><span>Used holds leading to a sale and a parcel</span><Verdict good={m.traceability.shipment_share === 1}>{number(m.traceability.consumed_with_sale_and_parcel)} of {number(m.traceability.consumed_holds)}</Verdict></li>
            <li><span>Clean rows changed by injection</span><Verdict good={m.unrelated_problems.length === 0}>{number(m.unrelated_problems.length)} problems</Verdict></li>
            <li><span>Identical from the same seed, including ground truth</span><Verdict good={m.reproducibility?.different.length === 0}>{m.reproducibility ? `${m.reproducibility.files_compared} files` : "Not run"}</Verdict></li>
          </ul>
        </Panel>
      </div>

      <div className="grid-two">
        <Panel title="What happened to promises and holds">
          <dl className="fact-list">
            {m.promises.kept.map((row) => (
              <div key={row.supply_type}>
                <dt>Shipped by the first promise, {words(row.supply_type).toLowerCase()}</dt>
                <dd>{percent(row.on_time_share)} <small>{number(row.on_time)} of {number(row.shipped)}</small></dd>
              </div>
            ))}
            <div><dt>Holds that expired unpaid</dt><dd>{percent(m.holds.expiry_rate)} <small>{number(m.holds.expired)} of {number(m.holds.placed)}</small></dd></div>
            <div><dt>Promises updated for a late truck</dt><dd>{number(m.promises.repromised)}</dd></div>
            <div><dt>Holds moved off a late truck</dt><dd>{number(m.promises.moved)}</dd></div>
            <div><dt>Holds cancelled past the 3-day limit</dt><dd>{number(m.promises.cancelled)}</dd></div>
            <div><dt>Short picks</dt><dd>Not measured <small>The generator doesn’t produce any</small></dd></div>
          </dl>
          <p className="table-foot">Late-truck rates are set on purpose in the generator, so they say nothing about real carriers.</p>
        </Panel>

        <Panel title="Shape of the data">
          <dl className="fact-list">
            <div><dt>Sales from the top tenth of SKUs</dt><dd>{percent(m.concentration.top_tenth_units_share)} <small>{number(m.concentration.top_tenth_skus)} SKUs</small></dd></div>
            <div><dt>Stocked SKU-location pairs</dt><dd>{number(m.network.pairs)}</dd></div>
            <div><dt>Damage events</dt><dd>{number(m.network.damage_events)}</dd></div>
            <div><dt>Stock on the road at the end</dt><dd>{number(m.network.in_transit_units)} <small>{number(m.network.in_transit_trucks)} trucks</small></dd></div>
            <div><dt>One full ATP answer</dt><dd>{m.atp_speed.median_ms} ms <small>median, slowest {m.atp_speed.slowest_ms} ms</small></dd></div>
          </dl>
          <p className="section-label">Orders by channel</p>
          <BarList rows={m.mix.channels.map((row) => ({ label: words(row.channel), value: row.orders }))} />
        </Panel>
      </div>

      {m.timings && (
        <Panel title="How long the last full run took" note={`${number(Math.round(sumOf(Object.values(m.timings).map((seconds) => ({ seconds })), "seconds")))} seconds in total`}>
          <BarList rows={Object.entries(m.timings).map(([label, seconds]) => ({ label: label.charAt(0).toUpperCase() + label.slice(1), value: seconds }))} format={(value) => `${value}s`} />
        </Panel>
      )}

      <Panel title="Where this data comes from" note={m.dataset.data_source.note}>
        <p className="code-line"><code>{m.dataset.generator_command}</code></p>
        <p className="table-foot">Synthetic, with no external data. Not generated: {m.dataset.not_generated_yet.join(", ")}. Written with Python {m.dataset.python_version}.</p>
        <div className="table-wrap file-table">
          <table className="table data-table compact-table">
            <thead><tr><th>File</th><th className="num">Rows</th><th>SHA-256, first 12</th></tr></thead>
            <tbody>
              {Object.entries(m.dataset.files).map(([name, info]) => (
                <tr key={name}><td>{name}</td><td className="num">{number(info.rows)}</td><td className="mono">{info.sha256}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
