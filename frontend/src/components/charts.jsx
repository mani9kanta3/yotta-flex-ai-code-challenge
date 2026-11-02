import { useEffect, useRef, useState } from "react";
import { compact, dateTime, monthName, number } from "../format.js";

// Hand-written charts, with no chart library, so every mark on the page can be explained.
// Each one shows its numbers in text as well, so nothing depends on seeing the colour.

// One value per row, longest first. Bars start at the same place and values line up on the right.
export function BarList({ rows, format = number }) {
  const largest = Math.max(...rows.map((row) => row.value), 1);
  return (
    <ul className="bar-list">
      {rows.map((row) => (
        <li key={row.label}>
          <span className="bar-label">{row.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${Math.max((row.value / largest) * 100, 0.6)}%` }} />
          </span>
          <span className="bar-value">{format(row.value)}{row.note && <small>{row.note}</small>}</span>
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
          <span key={row.label} className={`stack-part series-${row.series}`}
                title={`${row.label}: ${number(row.value)} (${((row.value / total) * 100).toFixed(1)}%)`}
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

// A value per month. The busiest month is darker and carries its number. Hover gives the others.
export function MonthColumns({ rows }) {
  const largest = Math.max(...rows.map((row) => row.value));
  const top = cleanTop(largest);
  const ticks = [top, top / 2, 0];
  return (
    <div className="columns" role="img" aria-label={rows.map((row) => `${row.label} ${number(row.value)}`).join(", ")}>
      <div className="column-axis" aria-hidden="true">
        {ticks.map((tick) => <span key={tick}>{compact(tick)}</span>)}
      </div>
      <div className="column-plot">
        {ticks.map((tick) => <span key={tick} className="column-grid" style={{ bottom: `${(tick / top) * 100}%` }} />)}
        {rows.map((row, index) => (
          <div key={row.label} title={`${row.label}: ${number(row.value)}`} className="column-slot">
            {row.value === largest && (
              <span className={`column-cap ${index > rows.length / 2 ? "cap-left" : ""}`} style={{ bottom: `${(row.value / top) * 100}%` }}>
                {compact(row.value)}
              </span>
            )}
            <span className={`column-bar ${row.value === largest ? "column-peak" : ""}`} style={{ height: `${(row.value / top) * 100}%` }} />
            <span className="column-label">{row.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const LEFT = 40;
const RIGHT = 16;
const TOP = 16;
const BOTTOM = 26;

// The first day of each month between two times, for the labels along the bottom.
function monthStarts(startText, endText) {
  const starts = [];
  const end = new Date(endText).getTime();
  const date = new Date(startText);
  date.setDate(1);
  date.setHours(0, 0, 0, 0);
  while (date.getTime() <= end) {
    if (date.getTime() >= new Date(startText).getTime()) starts.push(new Date(date));
    date.setMonth(date.getMonth() + 1);
  }
  return starts;
}

// Stock over time, drawn in steps, because stock jumps at each movement and doesn't drift.
export function StockLine({ points, opening, openingAt }) {
  const [hover, setHover] = useState(null);
  const [width, setWidth] = useState(0);
  const box = useRef(null);

  // Draw at the chart's real width, so the labels stay readable on a phone instead of shrinking.
  // Nothing is drawn until it is measured, because a guessed width could stretch a phone's page.
  useEffect(() => {
    setWidth(box.current.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);

  const series = [{ at: openingAt, on_hand: opening }, ...points];
  const height = width < 500 ? 190 : 250;
  const times = series.map((point) => new Date(point.at).getTime());
  const start = times[0];
  const span = times[times.length - 1] - start || 1;
  const top = cleanTop(Math.max(...series.map((point) => point.on_hand), 1));
  const x = (time) => LEFT + ((time - start) / span) * (width - LEFT - RIGHT);
  const y = (value) => TOP + (1 - value / top) * (height - TOP - BOTTOM);

  let path = `M${x(times[0])},${y(series[0].on_hand)}`;
  series.forEach((point, index) => {
    if (index > 0) path += `H${x(times[index])}V${y(point.on_hand)}`;
  });
  const baseline = y(0);
  const area = `${path}V${baseline}H${x(times[0])}Z`;
  const last = series[series.length - 1];
  const months = series.length < 2 ? [] : monthStarts(series[0].at, last.at);
  const everyNthMonth = Math.ceil(months.length / Math.max(Math.floor(width / 70), 1)) || 1;

  function onMove(event) {
    const rect = box.current.getBoundingClientRect();
    const time = start + ((event.clientX - rect.left - LEFT) / (width - LEFT - RIGHT)) * span;
    let index = 0;
    while (index < times.length - 1 && times[index + 1] <= time) index += 1;
    setHover(index);
  }

  const point = hover === null ? null : series[hover];
  const pointX = point ? x(new Date(point.at).getTime()) : 0;
  return (
    <div className="line-chart" ref={box} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      {series.length < 2 && <p className="quiet-note">No movements to draw for this stock yet.</p>}
      {series.length >= 2 && width > 0 && (
        <svg width={width} height={height} role="img" aria-label={`On-hand stock over time, ending at ${number(last.on_hand)} units`}>
          {[top, top / 2, 0].map((tick) => (
            <g key={tick}>
              <line x1={LEFT} x2={width - RIGHT} y1={y(tick)} y2={y(tick)} className={tick === 0 ? "axis-line" : "grid-line"} />
              <text x={LEFT - 8} y={y(tick) + 4} textAnchor="end" className="axis-text">{compact(tick)}</text>
            </g>
          ))}
          {months.map((month, index) => index % everyNthMonth === 0 && (
            <text key={month.getTime()} x={x(month.getTime())} y={height - 7} textAnchor="middle" className="axis-text">
              {monthName(`${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, "0")}`)}
            </text>
          ))}
          <path d={area} className="line-area" />
          <path d={path} className="line-stroke" />
          <circle cx={x(times[times.length - 1])} cy={y(last.on_hand)} r="4" className="line-dot" />
          {point && (
            <g>
              <line x1={pointX} x2={pointX} y1={TOP} y2={baseline} className="axis-line" />
              <circle cx={pointX} cy={y(point.on_hand)} r="4" className="line-dot" />
            </g>
          )}
        </svg>
      )}
      {point && (
        <div className="line-tip" style={pointX > width / 2 ? { right: width - pointX + 10 } : { left: pointX + 10 }}>
          <strong>{number(point.on_hand)} on hand</strong>
          <span>{dateTime(point.at)}</span>
        </div>
      )}
    </div>
  );
}
