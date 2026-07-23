import { useState } from "react";
import CostView from "./views/Cost";
import HermesView from "./views/Hermes";
import JobsView from "./views/Jobs";
import LogsView from "./views/Logs";
import MemoryView from "./views/Memory";
import Overview from "./views/Overview";

// P1:與既有 Streamlit dashboard 對等的五個資料區塊(總覽/Jobs/成本/Memory/Logs,
// 全部經唯讀 API 取數,見 src/api.ts)+ P0 交付的 Hermes Dashboard view。
// 所有畫面數字都來自 fetch,零硬編假資料(P0 DoD 第 4 條在 P1 繼續成立)。

type ViewId = "overview" | "jobs" | "cost" | "memory" | "logs" | "hermes";

const NAV_ITEMS: ReadonlyArray<{ id: ViewId; icon: string; title: string; sub: string }> = [
  { id: "overview", icon: "O", title: "總覽", sub: "Worker / Jobs / Domains" },
  { id: "jobs", icon: "J", title: "Jobs", sub: "List & Detail" },
  { id: "cost", icon: "$", title: "成本", sub: "Cost Summary" },
  { id: "memory", icon: "M", title: "Memory", sub: "Inbox & Files" },
  { id: "logs", icon: "L", title: "Logs", sub: "Tail Viewer" },
  { id: "hermes", icon: "H", title: "Hermes Dashboard", sub: "Control & Settings" },
];

const PAGE_META: Record<Exclude<ViewId, "hermes">, { kicker: string; title: string; desc: string }> = {
  overview: { kicker: "OVERVIEW", title: "總覽", desc: "Worker/Adapter 狀態、Job 統計、領域狀態(唯讀)" },
  jobs: { kicker: "JOBS", title: "Jobs", desc: "最近 job 列表與單筆詳細內容(唯讀)" },
  cost: { kicker: "COST", title: "成本", desc: "job 成本統計,依 source 分組(唯讀)" },
  memory: { kicker: "MEMORY", title: "Memory", desc: "inbox 計數與正本記憶檔案清單(唯讀)" },
  logs: { kicker: "LOGS", title: "Logs", desc: "log 檔案尾端檢視(唯讀)" },
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

        {activeView !== "hermes" && (
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
            </div>
          </>
        )}
      </section>
    </main>
  );
}
