import axios from "axios";

// Vite passes /api on to FastAPI, see vite.config.js. The long timeout is for running the
// detectors again at full scale, which takes about a minute.
const api = axios.create({ baseURL: "/api", timeout: 180000 });

// The API explains a refusal in "detail". Show that, because it says what actually went wrong.
export function errorText(error) {
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).join(", ");
  if (error.response) return `The API answered with an error (${error.response.status}).`;
  return "Can't reach the API. Is it running on port 8000?";
}

export default api;
