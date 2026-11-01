import { useEffect, useState } from "react";
import api from "./api.js";
import Availability from "./pages/Availability.jsx";
import Evidence from "./pages/Evidence.jsx";
import Exceptions from "./pages/Exceptions.jsx";
import Ledger from "./pages/Ledger.jsx";
import OrderTrace from "./pages/OrderTrace.jsx";
import Orders from "./pages/Orders.jsx";
import Overview from "./pages/Overview.jsx";
import Trucks from "./pages/Trucks.jsx";
import { useLoad } from "./components/ui.jsx";
import { dateTime, number } from "./format.js";

// Grouped by who the brief says uses this, so a reviewer can find each actor's view.
const NAV = [
  { group: "Network", items: [{ path: "", label: "Overview", who: "Everyone" }] },
  { group: "Customer and analyst", items: [
    { path: "availability", label: "Availability", who: "ATP by channel" },
    { path: "orders", label: "Orders", who: "Place and trace" },
  ] },
  { group: "Store and warehouse", items: [
    { path: "ledger", label: "Stock ledger", who: "Every movement" },
    { path: "trucks", label: "Trucks", who: "In transit" },
  ] },
  { group: "Operations manager", items: [
    { path: "exceptions", label: "Exceptions", who: "What needs action" },
    { path: "evidence", label: "Evidence", who: "How we know it works" },
  ] },
];

// The page comes from the address, so a page can be linked to and survives a reload.
function readHash() {
  const [path, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  const [page = "", id = ""] = path.split("/");
  return { page, id, params: Object.fromEntries(new URLSearchParams(query)) };
}

function useRoute() {
  const [route, setRoute] = useState(readHash);
  useEffect(() => {
    const update = () => {
      setRoute(readHash());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}

function Page({ route }) {
  if (route.page === "availability") return <Availability skuId={route.params.sku} />;
  if (route.page === "orders" && route.id) return <OrderTrace key={route.id} orderId={route.id} />;
  if (route.page === "orders") return <Orders />;
  if (route.page === "ledger") return <Ledger skuId={route.params.sku} locationId={route.params.location} />;
  if (route.page === "trucks") return <Trucks />;
  if (route.page === "exceptions") return <Exceptions />;
  if (route.page === "evidence") return <Evidence />;
  return <Overview />;
}

export default function App() {
  const route = useRoute();
  const health = useLoad(() => api.get("/health"), []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#/">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20"><path d="M4 17h16M4 12h10M4 7h16" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>
          </span>
          <span>
            <strong>Unified Inventory</strong>
            <small>Stores, warehouses, online</small>
          </span>
        </a>

        <nav className="nav-groups" aria-label="Pages">
          {NAV.map((section) => (
            <div key={section.group} className="nav-group">
              <p className="nav-heading">{section.group}</p>
              {section.items.map((item) => (
                <a key={item.label} href={`#/${item.path}`}
                   className={`nav-link-item ${route.page === item.path ? "active" : ""}`}
                   aria-current={route.page === item.path ? "page" : undefined}>
                  <span>{item.label}</span>
                  <small>{item.who}</small>
                </a>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          {health.data && (
            <>
              <p><span className="live-dot" aria-hidden="true" /> {health.data.scale ? `${health.data.scale} dataset` : "Unknown dataset"} loaded</p>
              <p>{number(health.data.row_counts.inventory_movement)} movements</p>
              <p>Simulated time: {dateTime(health.data.as_of)}</p>
            </>
          )}
          {health.error && <p>API not reachable</p>}
        </div>
      </aside>

      <main className="content">
        <Page route={route} />
      </main>
    </div>
  );
}
