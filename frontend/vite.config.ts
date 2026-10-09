import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two pages: the landing page at / and the app at /app/.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { "/api": "http://localhost:8000" } },
  build: {
    rollupOptions: {
      input: { landing: resolve(import.meta.dirname, "index.html"), app: resolve(import.meta.dirname, "app/index.html") },
    },
  },
});
