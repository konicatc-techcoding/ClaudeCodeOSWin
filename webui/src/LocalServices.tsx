import { useSyncExternalStore } from "react";
import { useResidentApiState } from "./ResidentStatus";
import type { ResidentApiState } from "./ResidentStatus";
import { useBridgeHealth } from "./ServiceControl";
import type { BridgeState } from "./ServiceControl";

// 「本機服務」狀態區(2026-08-03 拍板,任務 2;同日稍晚自 sidebar 遷至
// 總覽頁,與 ServiceControl 並排):顯示 Web UI 觀測面
// stack 四個本機服務(唯讀 API 8799 / Bridge 8787 / PTY 8801 / UI 5173)的
// 開/關燈號。**純唯讀、零操作鍵**:UI 死了畫面上的按鈕也死——啟動必須在
// UI 外做(scripts/start_webui_stack),這裡誠實面對這個結構,只給燈號與
// 一行啟動指引,不做假啟動鈕。
//
// 取數來源(全部沿用既有節奏,零新增高頻輪詢):
// - 8799:ResidentStatus 共享 store 的輪詢成敗(useResidentApiState)——
//   零額外請求,30 秒節奏不變。
// - 8787:ServiceControl 抽出的 bridge health 共享 store(useBridgeHealth)
//   ——與按鈕可用性判斷同源,單一 interval。
// - 8801:本檔的 PTY health 共享 store(下方)——重用 Terminal view 的檢查
//   方式(同一 GET /health、同一 2.5 秒 timeout),但 Terminal 的探測是
//   事件驅動(進 view / 手動重試),本 store 才是唯一的週期輪詢,同為
//   30 秒節奏。
// - 5173:本頁能載入即代表 Vite dev server 運行,恆綠並誠實註明判斷依據,
//   不發請求。

// localhost-only:與 Terminal.tsx 相同的 PTY health endpoint(寫死 127.0.0.1)
const PTY_HEALTH_URL = "http://127.0.0.1:8801/health";
// 與 RESIDENT_POLL_INTERVAL_MS / BRIDGE_HEALTH_POLL_MS 同值——沿用既有節奏
export const PTY_HEALTH_POLL_MS = 30_000;

export type ProbeState = "unknown" | "online" | "offline";

// --- PTY(8801)health 共享輪詢 store(單一 interval,多訂閱者)---
let ptySnapshot: ProbeState = "unknown";
const ptyListeners = new Set<() => void>();
let ptyTimer: ReturnType<typeof setInterval> | null = null;

async function pollPtyHealth(): Promise<void> {
  try {
    const response = await fetch(PTY_HEALTH_URL, { signal: AbortSignal.timeout(2500) });
    ptySnapshot = response.ok ? "online" : "offline";
  } catch {
    ptySnapshot = "offline";
  }
  for (const listener of ptyListeners) listener();
}

function subscribePtyHealth(listener: () => void): () => void {
  ptyListeners.add(listener);
  if (ptyListeners.size === 1) {
    void pollPtyHealth();
    ptyTimer = setInterval(() => void pollPtyHealth(), PTY_HEALTH_POLL_MS);
  }
  return () => {
    ptyListeners.delete(listener);
    if (ptyListeners.size === 0 && ptyTimer !== null) {
      clearInterval(ptyTimer);
      ptyTimer = null;
    }
  };
}

export function usePtyHealth(): ProbeState {
  return useSyncExternalStore(subscribePtyHealth, () => ptySnapshot);
}

// 燈色沿用個別服務燈號的既有視覺(ResidentStatus LIGHT_COLORS 同色票)
const PROBE_COLORS: Record<ProbeState, string> = {
  online: "#34d399",
  offline: "#f87171",
  unknown: "#6b7280",
};

const PROBE_TEXT: Record<ProbeState, string> = {
  online: "on",
  offline: "off",
  unknown: "檢查中",
};

export type LocalServiceRow = {
  id: string;
  name: string;
  port: number;
  state: ProbeState;
  detail: string;
};

// 純資料組裝(props 可注入,供 local-services.test.mjs 直接斷言)
export function buildLocalServiceRows(
  api: ResidentApiState,
  bridge: BridgeState,
  pty: ProbeState,
): LocalServiceRow[] {
  return [
    {
      id: "api",
      name: "唯讀 API",
      port: 8799,
      state: api,
      detail: "dashboard 唯讀 API(取數:resident 共享輪詢的成敗,零額外請求)",
    },
    {
      id: "bridge",
      name: "Bridge",
      port: 8787,
      state: bridge,
      detail: "AgentOS Local Bridge(取數:既有 bridge health 共享探測)",
    },
    {
      id: "pty",
      name: "PTY",
      port: 8801,
      state: pty,
      detail: "PTY server(ClaudeCode CLI view 的終端後端)",
    },
    {
      id: "ui",
      name: "UI",
      port: 5173,
      state: "online",
      detail: "Vite dev server——本頁能載入即代表運行(恆綠,不發請求)",
    },
  ];
}

// 純渲染(props 注入)——零按鈕、零點擊處理器,只有燈號與文字
export function LocalServicesPanel({ rows }: { rows: LocalServiceRow[] }) {
  return (
    <div className="service-control local-services">
      <p className="service-control-title">本機服務(Web UI stack)</p>
      {rows.map((row) => (
        <div className="service-control-row" key={row.id}>
          <span className="service-unit" title={row.detail}>
            {row.name}
          </span>
          <span
            className={`service-unit-light service-unit-light-${row.state === "unknown" ? "gray" : row.state === "online" ? "green" : "red"}`}
            title={`127.0.0.1:${row.port}:${PROBE_TEXT[row.state]}\n${row.detail}`}
            aria-label={`${row.name}(port ${row.port})狀態:${PROBE_TEXT[row.state]}`}
          >
            <span
              className="service-unit-light-dot"
              style={{ background: PROBE_COLORS[row.state] }}
              aria-hidden="true"
            />
            <span className="service-unit-light-text">
              :{row.port} {PROBE_TEXT[row.state]}
            </span>
          </span>
        </div>
      ))}
      <p className="service-note local-services-note">
        未全綠時,在 UI 外執行 scripts/start_webui_stack(.vbs 零黑窗/.ps1)補啟動缺的服務;UI(5173)恆綠是因為本頁能載入即代表它在運行。
      </p>
    </div>
  );
}

export default function LocalServices() {
  const api = useResidentApiState();
  const bridge = useBridgeHealth();
  const pty = usePtyHealth();
  return <LocalServicesPanel rows={buildLocalServiceRows(api, bridge, pty)} />;
}
