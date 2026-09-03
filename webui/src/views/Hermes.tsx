// Hermes Dashboard view(P0 交付,P1 原樣搬入獨立元件)。
// 經 Local Bridge(最小寫入例外,規格見 webui/README.md)啟動並以 iframe 內嵌。
// 本元件在 App 中維持常駐(切換 view 時只隱藏),iframe 不因切頁被卸載。
import { useState } from "react";

type HermesStatus = "idle" | "starting" | "stopping" | "online" | "error";

const HERMES_BRIDGE_URL = "http://127.0.0.1:8787";
const HERMES_DASHBOARD_URL = "http://127.0.0.1:9119";

type BridgeResult = { ok?: boolean; error?: string; reused?: boolean; external?: boolean };

export default function HermesView() {
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
    <>
      <header className="topbar">
        {/* topbar 結構與其餘 view 一致(.page-kicker + h1 平排;頁面說明移到
            內容區的 .page-caption),見 App.tsx 的 topbar 改版說明 */}
        <div className="page-identity">
          <span className="page-kicker">HERMES CONTROL</span>
          <h1>Hermes Dashboard</h1>
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
          {/* 頁面說明:僅在未上線時顯示——is-online 時 .hermes-page 是
              滿版高度容器(gap 0),多一個區塊會把 iframe 擠出可視範圍 */}
          {!isOnline && (
            <p className="page-caption">在 AgentOS 內啟動並管理 Hermes(僅限本 Bridge 啟動的 process)。</p>
          )}
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
    </>
  );
}
