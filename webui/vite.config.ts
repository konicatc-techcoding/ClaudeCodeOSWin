import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// localhost-only 鐵律:host 寫死 127.0.0.1,不提供參數化入口。
// (不是「預設 localhost 但可改」,是沒有改的入口——見 docs/webui-migration-proposal.md §3.1)
const LOCALHOST_ONLY = "127.0.0.1";

// P3:cors 明確關閉——SPA 對自身 dev server 是同源請求,不需要 CORS;
// 關閉後其他 origin 的網頁無法讀取 dev server 回應(轉譯後的模組內含
// per-boot PTY token,不給任何跨來源讀取管道)。這是收緊,不放寬任何既有判準。
export default defineConfig({
  plugins: [react()],
  server: {
    host: LOCALHOST_ONLY,
    port: 5173,
    strictPort: true,
    cors: false,
  },
  preview: {
    host: LOCALHOST_ONLY,
    port: 5173,
    strictPort: true,
    cors: false,
  },
});
