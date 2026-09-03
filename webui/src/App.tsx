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
import UpdatePrecheckView from "./views/UpdatePrecheck";

// P1:與當時的 Streamlit dashboard(2026-08-15 已退役)對等的五個資料區塊(總覽/Jobs/成本/Memory/Logs,
// 全部經唯讀 API 取數,見 src/api.ts)+ P0 交付的 Hermes Dashboard view。
// 所有畫面數字都來自 fetch,零硬編假資料(P0 DoD 第 4 條在 P1 繼續成立)。
// P2:Stage 3 三項觀測功能——排程健康表(併入總覽,stage3 提案 §4.3)、
// 憑證/Lane 狀態、Hermes Sessions,全部唯讀。
// P3:ClaudeCode CLI(PTY 真終端機,唯一的寫入型 view),設計正本
// docs/webui-pty-terminal-proposal.md。
//
// 2026-08-15 導覽改版(純呈現層,不動任何 view 的行為與取數):
//   (a) 十項平鋪 → 三組(觀測/操作/治理)。掃描時先選組再選項,
//       比一次讀十個標題+十行副標便宜。
//   (b) 副標只在 active 項顯示——原本十行副標常駐,760px 以下又整個
//       display:none,本來就不是必要資訊。
//   (c) ClaudeCode CLI 是唯一寫入型 view,導覽上以 Claude 橘圖示標記,
//       與唯讀項在視覺上分開(色票沿用 --claude-orange,不新增語意色)。
//   ⚠ 既有約定:CLI 必須放在「總覽與 Jobs 之間」(拍板過的順序,
//     tests/ui-static.test.mjs 鎖定)。分組時一度把它移到〔操作〕組首位,
//     2026-09-03 拍板回復原順序:terminal 留在〔觀測〕組第二位,三組結構保留。
//     它仍是寫入型 view,以 Claude 橘圖示(nav-icon-write)與唯讀項區分。
type ViewId = "overview" | "terminal" | "jobs" | "cost" | "memory" | "logs" | "sessions" | "credentials" | "update" | "hermes";

type NavItem = { id: ViewId; icon: string; title: string; sub: string; write?: true };

// 分組:觀測(唯讀資料)/ 操作(有寫入或外部控制)/ 治理(中繼資訊與升級)
const NAV_GROUPS: ReadonlyArray<{ label: string; items: ReadonlyArray<NavItem> }> = [
  {
    label: "觀測",
    items: [
      { id: "overview", icon: "O", title: "總覽", sub: "Worker / Jobs / 排程表" },
      { id: "terminal", icon: ">", title: "ClaudeCode CLI", sub: "CoS 終端機(前台 session)", write: true },
      { id: "jobs", icon: "J", title: "Jobs", sub: "List & Detail" },
      { id: "cost", icon: "$", title: "成本", sub: "Cost Summary" },
      { id: "memory", icon: "M", title: "Memory", sub: "Inbox & Files" },
      { id: "logs", icon: "L", title: "Logs", sub: "Tail Viewer" },
      { id: "sessions", icon: "S", title: "Hermes Sessions", sub: "唯讀 session 列表" },
    ],
  },
  {
    label: "操作",
    items: [
      { id: "hermes", icon: "H", title: "Hermes Dashboard", sub: "Control & Settings", write: true },
    ],
  },
  {
    label: "治理",
    items: [
      { id: "credentials", icon: "C", title: "憑證 / Lane 狀態", sub: "治理中繼資訊(唯讀)" },
      { id: "update", icon: "U", title: "Hermes 更新", sub: "唯讀升級預檢(階段一)" },
    ],
  },
];

const PAGE_META: Record<Exclude<ViewId, "hermes" | "terminal">, { kicker: string; title: string; desc: string }> = {
  overview: { kicker: "OVERVIEW", title: "總覽", desc: "Worker/Adapter 狀態、Job 統計、領域狀態、排程健康表(唯讀)" },
  jobs: { kicker: "JOBS", title: "Jobs", desc: "最近 job 列表與單筆詳細內容(唯讀)" },
  cost: { kicker: "COST", title: "成本", desc: "job 成本統計,依 source 分組(唯讀)" },
  memory: { kicker: "MEMORY", title: "Memory", desc: "inbox 計數與正本記憶檔案清單(唯讀)" },
  logs: { kicker: "LOGS", title: "Logs", desc: "log 檔案尾端檢視(唯讀)" },
  sessions: { kicker: "SESSIONS", title: "Hermes Sessions", desc: "Hermes session 唯讀列表——不含訊息內容,不提供全文檢視" },
  credentials: { kicker: "CREDENTIALS", title: "憑證/Lane 狀態", desc: "Capability lane 治理表+憑證治理中繼資訊(唯讀,絕不顯示憑證值)" },
  update: { kicker: "HERMES UPDATE", title: "Hermes 更新", desc: "升級預檢:兩端版本/落後/能否 ff/客製 diverge/rescue ref/服務狀態——零升級執行鈕(階段二未核准);遠端 fetch 為唯一寫入,僅更新 refs" },
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
            30 秒輪詢);燈號元件本身維持零操作入口。
            設計正本 docs/webui-service-control-proposal.md §1。
            2026-08-03:服務控制鍵與本機服務燈號兩區塊自 sidebar 遷至總覽頁
            (views/Overview.tsx,Worker/Adapter 狀態上方);sidebar 只留這顆
            聚合燈——它常駐訂閱 resident 共享 store,輪詢不因切 view 停止。 */}
        <ResidentStatus />

        <nav className="main-nav" aria-label="主要功能">
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.label}>
              <p className="nav-label">{group.label}</p>
              {group.items.map((item) => {
                const active = activeView === item.id;
                return (
                  <button
                    key={item.id}
                    className={active ? "nav-item active" : "nav-item"}
                    type="button"
                    onClick={() => setActiveView(item.id)}
                  >
                    {/* 寫入型 view 的圖示走 Claude 橘,唯讀走紫 */}
                    <span className={item.write ? "nav-icon nav-icon-write" : "nav-icon"}>{item.icon}</span>
                    <span className="nav-text">
                      <b>{item.title}</b>
                      {/* 副標只在 active 項顯示:平時十行說明是雜訊,
                          需要時(正在這一頁)才有意義 */}
                      {active && <small>{item.sub}</small>}
                    </span>
                    <i>›</i>
                  </button>
                );
              })}
            </div>
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
            {/* topbar 改版:原本 kicker 用 writing-mode 直排(佔 84px 高、
                可讀性差、只是重複右邊標題),改為標題同列的平排小字;
                長說明(update 那段近 80 字)自固定高的 header 移入內容區,
                header 不再被文字撐爆。 */}
            <header className="topbar">
              <div className="page-identity">
                <span className="page-kicker">{PAGE_META[activeView].kicker}</span>
                <h1>{PAGE_META[activeView].title}</h1>
              </div>
            </header>
            <div className="main-content">
              <p className="page-caption">{PAGE_META[activeView].desc}</p>
              {activeView === "overview" && <Overview />}
              {activeView === "jobs" && <JobsView />}
              {activeView === "cost" && <CostView />}
              {activeView === "memory" && <MemoryView />}
              {activeView === "logs" && <LogsView />}
              {activeView === "sessions" && <SessionsView />}
              {activeView === "credentials" && <CredentialsView />}
              {activeView === "update" && <UpdatePrecheckView />}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
