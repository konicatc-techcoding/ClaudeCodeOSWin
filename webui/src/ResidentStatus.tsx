import { useEffect, useState } from "react";
import { apiGet } from "./api";
import type { ResidentLight, ResidentStatusPayload } from "./api";

// 背景常駐狀態燈號(純唯讀;設計正本 docs/webui-service-control-proposal.md §1)。
// 寫入部分(重啟/關閉鍵)已於 2026-07-27(v1.1)核准,但集中在獨立元件
// ServiceControl.tsx(bridge 8787 白名單第二群組)——本燈號元件**維持零操作
// 入口**:沒有任何按鈕、沒有任何點擊處理器、只發 GET(apiGet 一條路);
// 也不做停用狀態的假按鈕(mock 清零原則)。此邊界由 resident-render.test.mjs
// 靜態鎖定(讀寫分離:顯示歸這裡,操作歸 ServiceControl)。
// 探測失敗/唯讀 API 未連線 → 優雅退化為灰燈「無法查詢」,不影響其他 view。

export const RESIDENT_POLL_INTERVAL_MS = 30_000; // 提案 §1.3:前端 30 秒輪詢

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

export default function ResidentStatus() {
  const [status, setStatus] = useState<ResidentStatusPayload | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const payload = await apiGet<ResidentStatusPayload>("/api/resident-status");
        if (!cancelled) setStatus(payload);
      } catch {
        if (!cancelled) setStatus(null); // 探測失敗 → 灰燈,不噴例外
      }
    };
    void load();
    const timer = setInterval(load, RESIDENT_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return <ResidentStatusBadge status={status} />;
}
