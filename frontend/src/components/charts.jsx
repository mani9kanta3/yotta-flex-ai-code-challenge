import { useRef, useState } from "react";
import { compact, dateTime, number, shortDate } from "../format.js";

// Hand-written charts, with no chart library, so every mark on the page can be explained.
// Each one shows its numbers in text as well, so nothing depends on seeing the colour.

// One value per row, longest first. The value sits at the end of the bar.
export function BarList({ rows, format = number }) {
  const largest = Math.max(...rows.map((row) => row.value), 1);
  return (
    <ul className="bar-list">
      {rows.map((row) => (
        <li key={row.label} className="tip-host" data-tip={`${row.label}: ${format(row.value)}${row.note ? ` (${row.note})` : ""}`}>
          <span className="bar-label">{row.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${Math.max((row.value / largest) * 100, 0.6)}%` }} />
            <span className="bar-value">{format(row.value)}{row.note && <small> {row.note}</small>}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

// Parts of one whole, with a legend that always gives the number. Each entity keeps its colour.
export function StackedBar({ rows }) {
  const total = rows.reduce((sum, row) => sum + row.value, 0) || 1;
  return (
    <div>
      <div className="stack" role="img" aria-label={rows.map((row) => `${row.label} ${number(row.value)}`).join(", ")}>
        {rows.map((row) => (
          <span key={row.label} className={`stack-part series-${row.series} tip-host`}
                data-tip={`${row.label}: ${number(row.value)} (${((row.value / total) * 100).toFixed(1)}%)`}
                style={{ flexGrow: row.value }} />
        ))}
      </div>
      <ul className="legend">
        {rows.map((row) => (
          <li key={row.label}>
            <span className={`swatch series-${row.series}`} aria-hidden="true" />
            <span>{row.label}</span>
            <strong>{number(row.value)}</strong>
            <small>{((row.value / total) * 100).toFixed(1)}%</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Round the top of the axis up to a clean number, so the ticks are easy to read.
function cleanTop(value) {
  if (value <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

// A value per month. The busiest month carries its number, and hover gives the others.
export function MonthColumns({ rows }) {
  const top = cleanTop(Math.max(...rows.map((row) => row.value)));
  const largest = Math.max(...rows.map((row) => row.value));
  const ticks = [top, top / 2, 0];
  return (
    <div className="columns" role="img" aria-label={rows.map((row) => `${row.label} ${number(row.value)}`).join(", ")}>
      <div className="column-axis" aria-hidden="true">
        {ticks.map((tick) => <span key={tick}>{compact(tick)}</span>)}
      </div>
      <div className="column-plot">
        {ticks.map((tick) => <span key={tick} className="column-grid" style={{ bottom: `${(tick / top) * 100}%` }} />)}
        {rows.map((row) => (
          <div key={row.label} className="column-slot tip-host" data-tip={`${row.label}: ${number(row.value)}`}>
            {row.value === largest && <span className="column-cap" style={{ bottom: `${(row.value / top) * 100}%` }}>{compact(row.value)}</span>}
            <span className="column-bar" style={{ height: `${(row.value / top) * 100}%` }} />
            <span className="column-label">{row.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const WIDTH = 720;
const HEIGHT = 220;
const LEFT = 44;
const RIGHT = 16;
const TOP = 14;
const BOTTOM = 26;

// Stock over time, drawn in steps, because stock jumps at each movement and doesn't drift.
export function StockLine({ points, opening, openingAt }) {
  const [hover, setHover] = useState(null);
  const box = useRef(null);
  const series = [{ at: openingAt, on_hand: opening }, ...points];
  if (series.length < 2) return <p className="quiet-note">No movements to draw for this stock yet.</p>;

  const times = series.map((point) => new Date(point.at).getTime());
  const start = times[0];
  const span = times[times.length - 1] - start || 1;
  const top = cleanTop(Math.max(...series.map((point) => point.on_hand), 1));
  const x = (time) => LEFT + ((time - start) / span) * (WIDTH - LEFT - RIGHT);
  const y = (value) => TOP + (1 - value / top) * (HEIGHT - TOP - BOTTOM);

  let path = `M${x(times[0])},${y(series[0].on_hand)}`;
  series.forEach((point, index) => {
    if (index > 0) path += `H${x(times[index])}V${y(point.on_hand)}`;
  });
  const baseline = y(0);
  const area = `${path}V${baseline}H${x(times[0])}Z`;
  const last = series[series.length - 1];

  function onMove(event) {
    const rect = box.current.getBoundingClientRect();
    const time = start + ((event.clientX - rect.left) / rect.width * WIDTH - LEFT) / (WIDTH - LEFT - RIGHT) * span;
    let index = 0;
    while (index < times.length - 1 && times[index + 1] <= time) index += 1;
    setHover(index);
  }

  const point = hover === null ? null : series[hover];
  return (
    <div className="line-chart" ref={box} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`On-hand stock over time, ending at ${number(last.on_hand)} units`}>
        {[top, top / 2, 0].map((tick) => (
          <g key={tick}>
            <line x1={LEFT} x2={WIDTH - RIGHT} y1={y(tick)} y2={y(tick)} className={tick === 0 ? "axis-line" : "grid-line"} />
            <text x={LEFT - 8} y={y(tick) + 4} textAnchor="end" className="axis-text">{compact(tick)}</text>
          </g>
        ))}
        <text x={LEFT} y={HEIGHT - 6} className="axis-text">{shortDate(series[0].at)}</text>
        <text x={WIDTH - RIGHT} y={HEIGHT - 6} textAnchor="end" className="axis-text">{shortDate(last.at)}</text>
        <path d={area} className="line-area" />
        <path d={path} className="line-stroke" />
        <circle cx={x(times[times.length - 1])} cy={y(last.on_hand)} r="4.5" className="line-dot" />
        {point && (
          <g>
            <line x1={x(new Date(point.at).getTime())} x2={x(new Date(point.at).getTime())} y1={TOP} y2={baseline} className="axis-line" />
            <circle cx={x(new Date(point.at).getTime())} cy={y(point.on_hand)} r="4.5" className="line-dot" />
          </g>
        )}
      </svg>
      {point && (
        <div className="line-tip" style={{ left: `${Math.min((x(new Date(point.at).getTime()) / WIDTH) * 100, 72)}%` }}>
          <strong>{number(point.on_hand)} on hand</strong>
          <span>{dateTime(point.at)}</span>
        </div>
      )}
    </div>
  );
}
