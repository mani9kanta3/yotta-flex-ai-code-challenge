import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The page asks for /api and Vite passes it on to FastAPI, so both look like one
// site to the browser and the API needs no cross-origin settings.
const api = { "/api": "http://localhost:8000" };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: api },
  preview: { port: 5173, proxy: api },
});
