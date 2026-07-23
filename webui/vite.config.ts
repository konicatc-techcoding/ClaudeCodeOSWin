import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// localhost-only 鐵律:host 寫死 127.0.0.1,不提供參數化入口。
// (不是「預設 localhost 但可改」,是沒有改的入口——見 docs/webui-migration-proposal.md §3.1)
const LOCALHOST_ONLY = "127.0.0.1";

export default defineConfig({
  plugins: [react()],
  server: {
    host: LOCALHOST_ONLY,
    port: 5173,
    strictPort: true,
  },
  preview: {
    host: LOCALHOST_ONLY,
    port: 5173,
    strictPort: true,
  },
});
