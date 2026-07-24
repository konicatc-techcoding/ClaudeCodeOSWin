import { useState } from "react";
import CostView from "./views/Cost";
import CredentialsView from "./views/Credentials";
import HermesView from "./views/Hermes";
import JobsView from "./views/Jobs";
import LogsView from "./views/Logs";
import MemoryView from "./views/Memory";
import Overview from "./views/Overview";
import ResidentStatus from "./ResidentStatus";
import SessionsView from "./views/Sessions";
import TerminalView from "./views/Terminal";

// P1:與既有 Streamlit dashboard 對等的五個資料區塊(總覽/Jobs/成本/Memory/Logs,
// 全部經唯讀 API 取數,見 src/api.ts)+ P0 交付的 Hermes Dashboard view。
// 所有畫面數字都來自 fetch,零硬編假資料(P0 DoD 第 4 條在 P1 繼續成立)。
// P2:Stage 3 三項觀測功能——排程健康表(併入總覽,stage3 提案 §4.3)、
// 憑證/Lane 狀態、Hermes Sessions,全部唯讀。

// P3:ClaudeCode CLI(PTY 真終端機,唯一的寫入型 view)——nav 位置在
// 總覽與 Jobs 之間(使用者原話指定),設計正本 docs/webui-pty-terminal-proposal.md。
type ViewId = "overview" | "terminal" | "jobs" | "cost" | "memory" | "logs" | "sessions" | "credentials" | "hermes";

const NAV_ITEMS: ReadonlyArray<{ id: ViewId; icon: string; title: string; sub: string }> = [
  { id: "overview", icon: "O", title: "總覽", sub: "Worker / Jobs / 排程表" },
  { id: "terminal", icon: ">", title: "ClaudeCode CLI", sub: "CoS 終端機(前台 session)" },
  { id: "jobs", icon: "J", title: "Jobs", sub: "List & Detail" },
  { id: "cost", icon: "$", title: "成本", sub: "Cost Summary" },
  { id: "memory", icon: "M", title: "Memory", sub: "Inbox & Files" },
  { id: "logs", icon: "L", title: "Logs", sub: "Tail Viewer" },
  { id: "sessions", icon: "S", title: "Hermes Sessions", sub: "唯讀 session 列表" },
  { id: "credentials", icon: "C", title: "憑證/Lane 狀態", sub: "治理中繼資訊(唯讀)" },
  { id: "hermes", icon: "H", title: "Hermes Dashboard", sub: "Control & Settings" },
];

const PAGE_META: Record<Exclude<ViewId, "hermes" | "terminal">, { kicker: string; title: string; desc: string }> = {
  overview: { kicker: "OVERVIEW", title: "總覽", desc: "Worker/Adapter 狀態、Job 統計、領域狀態、排程健康表(唯讀)" },
  jobs: { kicker: "JOBS", title: "Jobs", desc: "最近 job 列表與單筆詳細內容(唯讀)" },
  cost: { kicker: "COST", title: "成本", desc: "job 成本統計,依 source 分組(唯讀)" },
  memory: { kicker: "MEMORY", title: "Memory", desc: "inbox 計數與正本記憶檔案清單(唯讀)" },
  logs: { kicker: "LOGS", title: "Logs", desc: "log 檔案尾端檢視(唯讀)" },
  sessions: { kicker: "SESSIONS", title: "Hermes Sessions", desc: "Hermes session 唯讀列表——不含訊息內容,不提供全文檢視" },
  credentials: { kicker: "CREDENTIALS", title: "憑證/Lane 狀態", desc: "Capability lane 治理表+憑證治理中繼資訊(唯讀,絕不顯示憑證值)" },
};

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>("overview");

  return (
    <main className="dashboard-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <strong>AgentOS</strong>
            <span>Control Center</span>
          </div>
        </div>

        {/* 背景常駐狀態燈號:唯讀、全 view 共用頂部、常駐掛載(切 view 不重置
            30 秒輪詢);零操作按鈕——寫入部分(重啟/關閉鍵)未核准,無任何入口。
            設計正本 docs/webui-service-control-proposal.md §1。 */}
        <ResidentStatus />

        <p className="nav-label">WORKSPACE</p>
        <nav className="main-nav" aria-label="主要功能">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={activeView === item.id ? "nav-item active" : "nav-item"}
              type="button"
              onClick={() => setActiveView(item.id)}
            >
              <span className="nav-icon">{item.icon}</span>
              <span>
                <b>{item.title}</b>
                <small>{item.sub}</small>
              </span>
              <i>›</i>
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        {/* Hermes view 常駐掛載(僅隱藏),iframe 不因切換 view 被卸載 */}
        <div style={activeView === "hermes" ? undefined : { display: "none" }}>
          <HermesView />
        </div>

        {/* ClaudeCode CLI view 常駐掛載(僅隱藏):切換 view 不關閉 WS,
            進行中的 claude session 不因換頁被斷線(避免誤觸 60 秒 grace) */}
        <div style={activeView === "terminal" ? undefined : { display: "none" }}>
          <TerminalView />
        </div>

        {activeView !== "hermes" && activeView !== "terminal" && (
          <>
            <header className="topbar">
              <div className="page-identity">
                <span>{PAGE_META[activeView].kicker}</span>
                <div>
                  <h1>{PAGE_META[activeView].title}</h1>
                  <p>{PAGE_META[activeView].desc}</p>
                </div>
              </div>
            </header>
            <div className="main-content">
              {activeView === "overview" && <Overview />}
              {activeView === "jobs" && <JobsView />}
              {activeView === "cost" && <CostView />}
              {activeView === "memory" && <MemoryView />}
              {activeView === "logs" && <LogsView />}
              {activeView === "sessions" && <SessionsView />}
              {activeView === "credentials" && <CredentialsView />}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
