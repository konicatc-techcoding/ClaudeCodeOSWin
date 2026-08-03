import { useSyncExternalStore } from "react";
import { apiGet } from "./api";
import type { ResidentLight, ResidentStatusPayload } from "./api";

// 背景常駐狀態燈號(純唯讀;設計正本 docs/webui-service-control-proposal.md §1)。
// 寫入部分(重啟/關閉鍵)已於 2026-07-27(v1.1)核准,但集中在獨立元件
// ServiceControl.tsx(bridge 8787 白名單第二群組)——本燈號元件**維持零操作
// 入口**:沒有任何按鈕、沒有任何點擊處理器、只發 GET(apiGet 一條路);
// 也不做停用狀態的假按鈕(mock 清零原則)。此邊界由 resident-render.test.mjs
// 靜態鎖定(讀寫分離:顯示歸這裡,操作歸 ServiceControl)。
// 探測失敗/唯讀 API 未連線 → 優雅退化為灰燈「無法查詢」,不影響其他 view。
//
// 2026-07-27 追加:ServiceControl 的「個別服務小燈號」與本聚合燈**同源取數**
// ——共用下方的模組層 store(單一 setInterval、單一 apiGet 位點),不開第二條
// 輪詢。個別燈的狀態映射(buildUnitLight)與純渲染(ServiceUnitLight)也放在
// 本檔:顯示歸這裡,操作仍歸 ServiceControl(讀寫分離不變;燈號一律走 8799
// 唯讀 API,絕不從 bridge 8787 拿狀態)。

export const RESIDENT_POLL_INTERVAL_MS = 30_000; // 提案 §1.3:前端 30 秒輪詢

// --- 共享輪詢 store(單一 interval,多訂閱者)---
// 聚合燈(ResidentStatus)與個別燈(ServiceControl 經 useResidentStatus)共用
// 同一份快照:第一個訂閱者掛載時啟動輪詢,最後一個卸載時停止;兩個元件
// 常駐 sidebar 時,對 8799 的請求量與單一元件時代完全相同(30 秒一次)。
let residentSnapshot: ResidentStatusPayload | null = null;
const residentListeners = new Set<() => void>();
let residentTimer: ReturnType<typeof setInterval> | null = null;

// 唯讀 API(8799)可達性(2026-08-03,「本機服務」區塊):不另發任何請求,
// 直接以本 store 既有輪詢的成敗當作 8799 存活判斷——最便宜的探測是零額外
// 請求。"unknown" 只出現在第一次輪詢回來之前(顯示灰燈,不假裝已知)。
export type ResidentApiState = "unknown" | "online" | "offline";
let residentApiState: ResidentApiState = "unknown";

async function pollResidentStatus(): Promise<void> {
  try {
    residentSnapshot = await apiGet<ResidentStatusPayload>("/api/resident-status");
    residentApiState = "online";
  } catch {
    residentSnapshot = null; // 探測失敗 → 灰燈「無法查詢」,不噴例外(mock 清零)
    residentApiState = "offline";
  }
  for (const listener of residentListeners) listener();
}

function subscribeResidentStatus(listener: () => void): () => void {
  residentListeners.add(listener);
  if (residentListeners.size === 1) {
    void pollResidentStatus();
    residentTimer = setInterval(() => void pollResidentStatus(), RESIDENT_POLL_INTERVAL_MS);
  }
  return () => {
    residentListeners.delete(listener);
    if (residentListeners.size === 0 && residentTimer !== null) {
      clearInterval(residentTimer);
      residentTimer = null;
    }
  };
}

// 唯讀共享 hook:回傳最新一次輪詢快照(null = 尚未取得/查詢失敗 → 灰燈)
export function useResidentStatus(): ResidentStatusPayload | null {
  return useSyncExternalStore(subscribeResidentStatus, () => residentSnapshot);
}

// 唯讀共享 hook:8799 唯讀 API 本身的可達性(LocalServices「本機服務」區塊
// 取數用)。與快照同一條 30 秒輪詢、同一個訂閱生命週期——零新增請求位點。
export function useResidentApiState(): ResidentApiState {
  return useSyncExternalStore(subscribeResidentStatus, () => residentApiState);
}

const LIGHT_COLORS: Record<ResidentLight, string> = {
  green: "#34d399",
  yellow: "#fbbf24",
  orange: "#fb923c",
  red: "#f87171",
  gray: "#6b7280",
};

const FALLBACK_TEXT = "無法查詢";

// tooltip(title 屬性)顯示分層細節:distro / 各常駐單元 / gateway。
// 資料已在同一份回應裡,不另發請求(提案 §1.3)。
export function buildResidentTooltip(status: ResidentStatusPayload | null): string {
  if (!status) return "背景服務狀態無法查詢(唯讀 API 未連線)";
  const lines: string[] = [status.detail];
  lines.push(`WSL distro(${status.distro.name}):${status.distro.detail}`);
  if (status.units) {
    for (const unit of status.resident_units) {
      const info = status.units[unit];
      lines.push(`${unit}:${info ? `${info.state}/${info.sub}` : "未載入"}`);
    }
  } else {
    lines.push("systemd 單元:未探測(distro 未運作或查詢失敗)");
  }
  if (status.gateway) {
    lines.push(`gateway:${status.gateway.detail}`);
  }
  lines.push(`查詢時間:${status.checked_at}`);
  return lines.join("\n");
}

// 純渲染部分(props 注入,不經 fetch)——供 resident-render.test.mjs 直接測試
export function ResidentStatusBadge({ status }: { status: ResidentStatusPayload | null }) {
  const light: ResidentLight = status?.light ?? "gray";
  const text = status?.text ?? FALLBACK_TEXT;
  return (
    <div
      className={`resident-status resident-light-${light}`}
      title={buildResidentTooltip(status)}
      aria-label={`背景服務:${text}`}
    >
      <span className="resident-dot" style={{ background: LIGHT_COLORS[light] }} aria-hidden="true" />
      <span className="resident-text">背景服務:{text}</span>
    </div>
  );
}

// --- 個別服務小燈號(2026-07-27 拍板;顯示在 ServiceControl 每列服務名旁)---
// 資料同源:與聚合燈共用同一份 /api/resident-status 快照(8799 唯讀),
// 絕不從 bridge(8787,寫入面)拿狀態。gateway 暖機判斷直接繼承資料層
// 結果欄位(data_resident.py 第三層,3.5 分鐘慢啟動教訓),前端不自己計時。

// gateway 暖機歸屬單元:gateway 就緒層描述的是 worker 帶起的 hermes gateway
// (資料層只在常駐單元全 active 時才探測它),未就緒時把黃燈映射到 worker
// 這顆個別燈;telegram 不受 gateway 暖機影響。
const GATEWAY_WARMUP_UNIT = "hermes-worker.service";

export type UnitLightInfo = { light: ResidentLight; text: string; detail: string };

// 狀態映射(拍板項 2):active=綠(gateway 暖機中則黃)、activating=黃、
// inactive=紅、failed=紅、查無資料/distro 未運作/API 失敗=灰「無法查詢」;
// 其餘罕見 state(deactivating 等)誠實顯示原文,灰燈。
export function buildUnitLight(status: ResidentStatusPayload | null, unit: string): UnitLightInfo {
  if (!status) {
    return { light: "gray", text: FALLBACK_TEXT, detail: "唯讀 API(8799)未連線,服務狀態無法查詢" };
  }
  if (!status.units) {
    // 資料層在 distro 未運作時止步不探測服務層(避免喚醒)——誠實標示未探測
    const detail =
      status.distro.running === false
        ? `WSL distro ${status.distro.name} 未運作,服務層未探測(避免喚醒 distro)`
        : "服務層未探測或查詢失敗";
    return { light: "gray", text: FALLBACK_TEXT, detail };
  }
  const info = status.units[unit];
  if (!info) {
    return { light: "gray", text: FALLBACK_TEXT, detail: `${unit} 未載入(systemd 查無此單元)` };
  }
  if (info.state === "active") {
    if (unit === GATEWAY_WARMUP_UNIT && status.gateway && !status.gateway.ready) {
      return { light: "yellow", text: "暖機中", detail: `active/${info.sub};${status.gateway.detail}` };
    }
    return { light: "green", text: "active", detail: `運作中(active/${info.sub})` };
  }
  if (info.state === "activating") {
    return { light: "yellow", text: "activating", detail: `啟動中(activating/${info.sub})` };
  }
  if (info.state === "failed") {
    return { light: "red", text: "failed", detail: `啟動失敗(failed/${info.sub})` };
  }
  if (info.state === "inactive") {
    return { light: "red", text: "inactive", detail: `已停止(inactive/${info.sub})` };
  }
  return { light: "gray", text: info.state, detail: `狀態:${info.state}/${info.sub}` };
}

// 純渲染(props 注入,不經 fetch)——供 service-unit-light.test.mjs 直接測試。
// 文字+tooltip 都給狀態名,不只靠顏色(拍板項 2)。
export function ServiceUnitLight({ status, unit }: { status: ResidentStatusPayload | null; unit: string }) {
  const { light, text, detail } = buildUnitLight(status, unit);
  return (
    <span
      className={`service-unit-light service-unit-light-${light}`}
      title={`${unit}:${text}\n${detail}`}
      aria-label={`${unit} 狀態:${text}`}
    >
      <span className="service-unit-light-dot" style={{ background: LIGHT_COLORS[light] }} aria-hidden="true" />
      <span className="service-unit-light-text">{text}</span>
    </span>
  );
}

export default function ResidentStatus() {
  const status = useResidentStatus();
  return <ResidentStatusBadge status={status} />;
}
