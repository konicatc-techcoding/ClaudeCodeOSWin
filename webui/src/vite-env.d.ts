/// <reference types="vite/client" />

interface ImportMetaEnv {
  // P3 PTY:per-boot 隨機 token,由 agentos-local.mjs 經環境變數注入
  // (不落磁碟、不進 git);非經 npm run local 啟動時為 undefined。
  readonly VITE_AGENTOS_PTY_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
