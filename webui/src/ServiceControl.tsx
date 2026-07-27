import { useEffect, useState } from "react";

// 服務控制鍵(寫入,白名單第二群組;2026-07-27 使用者核准,設計正本
// docs/webui-service-control-proposal.md v1.1 §2)。宿主=既有 bridge 8787。
//
// 邊界(與 bridge SERVICE_UNIT_WHITELIST / SERVICE_OP_WHITELIST 一致,由
// tests/service-control.test.mjs 斷言兩端枚舉一致、防群組滲透):
// - 單元/動詞皆為本檔案內的凍結枚舉;請求 URL 只能由枚舉值組出,
//   不存在任何自由字串輸入。
// - 破壞性動作(全部三種動詞)一律二次確認,不做「點一下就執行」。
// - 操作後不樂觀更新:顯示黃色「等待狀態收斂」提示,燈號(ResidentStatus,
//   30 秒輪詢)收斂後自然反映真實狀態。
// - bridge(8787)未運行 → 按鈕顯示為不可用狀態並說明原因,不做假按鈕。

const BRIDGE_URL = "http://127.0.0.1:8787";
const SERVICE_ROUTE_PREFIX = "/api/service/";

// 與 bridge 第二群組白名單一致的凍結枚舉(僅常駐 service,不含 timer)
export const SERVICE_UNITS = Object.freeze(["hermes-worker.service", "hermes-telegram.service"] as const);
export const SERVICE_OPS = Object.freeze(["start", "restart", "stop"] as const);

type ServiceUnit = (typeof SERVICE_UNITS)[number];
type ServiceOp = (typeof SERVICE_OPS)[number];

const OP_LABELS: Record<ServiceOp, string> = { start: "啟動", restart: "重啟", stop: "停止" };
const UNIT_LABELS: Record<ServiceUnit, string> = {
  "hermes-worker.service": "worker",
  "hermes-telegram.service": "telegram",
};

// stop 的真實語意(v1.1 §2.4 定案,必須明示於按鈕旁):
export const STOP_SEMANTICS_TEXT =
  "停止後不自動恢復,直到下次 Windows 登入/WSL distro 重啟時由 systemd(依 enable 狀態)重新拉起。";

export const BRIDGE_HEALTH_POLL_MS = 30_000; // bridge 在線探測輪詢
export const CONVERGE_NOTICE_MS = 45_000; // 收斂提示至少涵蓋一輪燈號輪詢(30s)

type PendingAction = { unit: ServiceUnit; op: ServiceOp };
type BridgeState = "unknown" | "online" | "offline";

type ServiceResult = { ok?: boolean; error?: string; exitCode?: number | null };

export default function ServiceControl() {
  const [bridgeState, setBridgeState] = useState<BridgeState>("unknown");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [busyUnit, setBusyUnit] = useState<ServiceUnit | null>(null);
  const [notice, setNotice] = useState<{ kind: "converging" | "error"; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const response = await fetch(`${BRIDGE_URL}/health`, { signal: AbortSignal.timeout(2500) });
        if (!cancelled) setBridgeState(response.ok ? "online" : "offline");
      } catch {
        if (!cancelled) setBridgeState("offline");
      }
    };
    void probe();
    const timer = setInterval(probe, BRIDGE_HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (notice?.kind !== "converging") return;
    const timer = setTimeout(() => setNotice(null), CONVERGE_NOTICE_MS);
    return () => clearTimeout(timer);
  }, [notice]);

  const disabled = bridgeState !== "online" || busyUnit !== null;

  async function execute(action: PendingAction) {
    setPending(null);
    setBusyUnit(action.unit);
    try {
      // URL 只能由兩個凍結枚舉的值組出(action 來自按鈕綁定的枚舉成員)
      const response = await fetch(`${BRIDGE_URL}${SERVICE_ROUTE_PREFIX}${action.unit}/${action.op}`, {
        method: "POST",
      });
      const result = (await response.json()) as ServiceResult;
      if (!response.ok || !result.ok) {
        throw new Error(result.error || `服務操作失敗(HTTP ${response.status})`);
      }
      // 不樂觀更新:只說「已送出」,狀態以燈號下次輪詢收斂為準
      setNotice({
        kind: "converging",
        text: `已送出 ${OP_LABELS[action.op]} ${action.unit}(exit=0)。等待狀態收斂中——上方燈號將於下次輪詢(≤30 秒)反映實際狀態,期間視為黃色未收斂,不做樂觀更新。`,
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: error instanceof Error ? error.message : "無法透過 AgentOS Bridge 執行服務操作",
      });
    } finally {
      setBusyUnit(null);
    }
  }

  return (
    <div className="service-control">
      <p className="service-control-title">服務控制(WSL systemd)</p>
      {SERVICE_UNITS.map((unit) => (
        <div className="service-control-row" key={unit}>
          <span className="service-unit" title={unit}>{UNIT_LABELS[unit]}</span>
          <span className="service-actions">
            {SERVICE_OPS.map((op) => (
              <button
                key={op}
                type="button"
                className={op === "stop" ? "service-btn service-btn-stop" : "service-btn"}
                disabled={disabled}
                onClick={() => setPending({ unit, op })}
              >
                {busyUnit === unit ? "…" : OP_LABELS[op]}
              </button>
            ))}
          </span>
        </div>
      ))}

      {bridgeState !== "online" && (
        <p className="service-note service-note-offline">
          {bridgeState === "unknown"
            ? "正在確認 Bridge(127.0.0.1:8787)狀態…"
            : "Bridge(127.0.0.1:8787)未運行,按鈕已停用——請先 npm run local 啟動。"}
        </p>
      )}

      {/* 二次確認:破壞性動作不做「點一下就執行」(提案 §2.2) */}
      {pending && (
        <div className="service-confirm">
          <p>
            確定要對 <b>{pending.unit}</b> 執行「{OP_LABELS[pending.op]}」?
          </p>
          <span className="service-actions">
            <button type="button" className="service-btn service-btn-stop" onClick={() => execute(pending)}>
              確認{OP_LABELS[pending.op]}
            </button>
            <button type="button" className="service-btn" onClick={() => setPending(null)}>
              取消
            </button>
          </span>
        </div>
      )}

      {notice && (
        <p className={notice.kind === "error" ? "service-note service-note-error" : "service-note service-note-converging"}>
          <span className="service-converge-dot" aria-hidden="true" />
          {notice.text}
        </p>
      )}

      {/* stop 語意明示(v1.1 §2.4 定案文字,不可移除) */}
      <p className="service-note service-stop-semantics">{STOP_SEMANTICS_TEXT}</p>
    </div>
  );
}
