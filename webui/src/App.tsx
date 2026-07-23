import { useState } from "react";

// P0 範圍:只有 Hermes Dashboard 一個 view。
// 範本原有的 Chat / Monitor view 全部是硬編假資料(假 profile 表、假排程、
// 假 token 用量、假版本號、假 agent 數),依提案 §4.1 P0 DoD 第 4 條:
// 未接線的區塊直接不呈現——不是留 disabled 假介面。
// P1 接上唯讀 API 後才逐步加回真實資料的區塊。

type HermesStatus = "idle" | "starting" | "stopping" | "online" | "error";

const HERMES_BRIDGE_URL = "http://127.0.0.1:8787";
const HERMES_DASHBOARD_URL = "http://127.0.0.1:9119";

type BridgeResult = { ok?: boolean; error?: string; reused?: boolean; external?: boolean };

export default function App() {
  const [hermesStatus, setHermesStatus] = useState<HermesStatus>("idle");
  const [hermesNotice, setHermesNotice] = useState("等待本機 AgentOS Bridge 啟動 Hermes Dashboard");
  const [hermesFrameKey, setHermesFrameKey] = useState(0);

  async function startHermesDashboard() {
    setHermesStatus("starting");
    setHermesNotice("正在透過 Local Bridge 啟動 hermes dashboard…");

    try {
      const response = await fetch(`${HERMES_BRIDGE_URL}/api/hermes/dashboard`, { method: "POST" });
      const result = (await response.json()) as BridgeResult;
      if (!response.ok || !result.ok) throw new Error(result.error || "Hermes Dashboard 啟動失敗");
      setHermesStatus("online");
      setHermesNotice(
        result.external
          ? "偵測到已在運行的 Hermes Dashboard(非由本 Bridge 啟動),已直接內嵌"
          : "Hermes Dashboard 已啟動並內嵌在 AgentOS",
      );
    } catch (error) {
      setHermesStatus("error");
      setHermesNotice(error instanceof Error ? error.message : "無法連接本機 AgentOS Bridge");
    }
  }

  function refreshFrame() {
    setHermesFrameKey((k) => k + 1);
    setHermesNotice("已重新整理內嵌畫面");
  }

  async function reloadHermesDashboard() {
    setHermesStatus("starting");
    setHermesNotice("正在重新載入(重啟由 Bridge 啟動的 Dashboard process)…");
    try {
      const response = await fetch(`${HERMES_BRIDGE_URL}/api/hermes/dashboard/reload`, { method: "POST" });
      const result = (await response.json()) as BridgeResult;
      if (!response.ok || !result.ok) throw new Error(result.error || "Hermes Dashboard 重新載入失敗");
      setHermesStatus("online");
      setHermesFrameKey((k) => k + 1);
      setHermesNotice("Hermes Dashboard 已重新載入");
    } catch (error) {
      setHermesStatus("error");
      setHermesNotice(error instanceof Error ? error.message : "無法透過 AgentOS Bridge 重新載入");
    }
  }

  async function stopHermesDashboard() {
    setHermesStatus("stopping");
    setHermesNotice("正在關閉 Hermes Dashboard…");

    try {
      const response = await fetch(`${HERMES_BRIDGE_URL}/api/hermes/dashboard/stop`, { method: "POST" });
      const result = (await response.json()) as BridgeResult;
      if (!response.ok || !result.ok) throw new Error(result.error || "Hermes Dashboard 關閉失敗");
      setHermesStatus("idle");
      setHermesNotice("Hermes Dashboard 已關閉;按下啟動即可重新開啟");
    } catch (error) {
      setHermesStatus("error");
      setHermesNotice(error instanceof Error ? error.message : "無法透過 AgentOS Bridge 關閉 Hermes Dashboard");
    }
  }

  const isOnline = hermesStatus === "online";

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
          <button className="nav-item active" type="button">
            <span className="nav-icon">H</span>
            <span>
              <b>Hermes Dashboard</b>
              <small>Control &amp; Settings</small>
            </span>
            <i>›</i>
          </button>
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="page-identity">
            <span>HERMES CONTROL</span>
            <div>
              <h1>Hermes Dashboard</h1>
              <p>在 AgentOS 內啟動並管理 Hermes(僅限本 Bridge 啟動的 process)。</p>
            </div>
          </div>
          <div className="top-actions">
            <div className="hermes-top-controls">
              <span className="hermes-top-address">
                <b>Hermes Dashboard</b>
                <small>{HERMES_DASHBOARD_URL}</small>
              </span>
              {isOnline && (
                <>
                  <button type="button" onClick={refreshFrame}>↻ 重新整理畫面</button>
                  <button type="button" onClick={reloadHermesDashboard}>⟳ 重新載入</button>
                  <button type="button" className="close-dashboard" onClick={stopHermesDashboard}>關閉</button>
                </>
              )}
              <span className={isOnline ? "online-pill" : "offline-pill"}>
                <i />
                {isOnline ? "Online" : hermesStatus === "starting" ? "Starting" : hermesStatus === "stopping" ? "Stopping" : "Offline"}
              </span>
            </div>
          </div>
        </header>

        <div className="main-content hermes-content">
          <div className={isOnline ? "hermes-page is-online" : "hermes-page"}>
            {!isOnline && (
              <section className="hermes-launch-card compact-launch-card">
                <div className="hermes-launch-copy">
                  <span className="hermes-logo">H</span>
                  <div>
                    <span>HERMES AGENT</span>
                    <h2>
                      {hermesStatus === "starting"
                        ? "正在啟動 Dashboard"
                        : hermesStatus === "stopping"
                          ? "正在關閉 Dashboard"
                          : "Dashboard 尚未啟動"}
                    </h2>
                    <p>{hermesNotice}</p>
                  </div>
                </div>
                <div className="launch-actions">
                  <button
                    type="button"
                    className="launch-primary"
                    onClick={startHermesDashboard}
                    disabled={hermesStatus === "starting" || hermesStatus === "stopping"}
                  >
                    <span>{hermesStatus === "starting" ? "••" : "▶"}</span>
                    {hermesStatus === "starting" ? "啟動中…" : "啟動 Hermes Dashboard"}
                  </button>
                </div>
                <p className={`launch-note ${hermesStatus === "error" ? "error" : ""}`}>
                  <span>{hermesStatus === "error" ? "!" : "i"}</span>
                  {hermesNotice}
                </p>
              </section>
            )}

            <section className="dashboard-preview">
              {isOnline ? (
                <iframe key={hermesFrameKey} className="hermes-frame" src={HERMES_DASHBOARD_URL} title="Hermes Dashboard" />
              ) : (
                <div className="preview-placeholder standalone">
                  <span>H</span>
                  <b>
                    {hermesStatus === "starting"
                      ? "正在啟動 Hermes Dashboard"
                      : hermesStatus === "stopping"
                        ? "正在關閉 Hermes Dashboard"
                        : "等待 Hermes Dashboard 啟動"}
                  </b>
                  <p>{hermesNotice}</p>
                </div>
              )}
            </section>
          </div>
        </div>
      </section>
    </main>
  );
}
