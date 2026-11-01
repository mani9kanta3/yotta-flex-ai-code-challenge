import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "bootstrap/dist/css/bootstrap.min.css";
import "./styles.css";
import App from "./App.jsx";

// Bootstrap's own forms and tables follow the same light or dark setting as our colours.
const dark = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  document.documentElement.setAttribute("data-bs-theme", dark.matches ? "dark" : "light");
}
applyTheme();
dark.addEventListener("change", applyTheme);

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
