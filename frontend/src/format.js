// How numbers and times look on the page. Everything is simulated time with no time zone,
// so these read the text as it is and never convert it through the browser's clock.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function number(value) {
  return value === null || value === undefined ? "–" : Number(value).toLocaleString("en-IN");
}

export function compact(value) {
  const n = Number(value);
  if (Math.abs(n) >= 1000000) return `${(n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1)}M`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, "")}K`;
  return String(n);
}

export function money(value) {
  return `₹${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function percent(share, digits = 1) {
  return share === null || share === undefined ? "–" : `${(share * 100).toFixed(digits)}%`;
}

export function dateTime(text) {
  if (!text) return "–";
  const [date, time = ""] = text.split("T");
  const [year, month, day] = date.split("-");
  return `${Number(day)} ${MONTHS[Number(month) - 1]} ${year}, ${time.slice(0, 5)}`;
}

export function shortDate(text) {
  const [, month, day] = text.split("T")[0].split("-");
  return `${Number(day)} ${MONTHS[Number(month) - 1]}`;
}

export function monthName(text) {
  return MONTHS[Number(text.split("-")[1]) - 1];
}

// ORDER_SHIPPED reads better as "Order shipped".
export function words(code) {
  if (!code) return "–";
  if (code === "NDC") return code;
  const text = code.replaceAll("_", " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
