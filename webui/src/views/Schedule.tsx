// Cron/Timer 統一排程健康表(P2 功能三,stage3 提案 §4):
// systemd timer(路徑 A)+ Hermes 原生 cron(路徑 B),source 欄位標示機制;
// unpinned agent job 的模型漂移旗標(aligned/DRIFTED/n/a + 花費方向)。
//
// 唯讀邊界(§4.3/§4.7):**明確不放任何操作按鈕**——不提供 pin/對齊/重跑
// 等任何寫入入口;DRIFTED 只標示,由使用者自行決定是否處理。
// (render 測試斷言表格內零 <button>。)
import { apiGet, type ScheduleRow } from "../api";
import { ErrorNotice, InfoNotice, Panel, RefreshButton, useApiData } from "./common";

export const SCHEDULE_COLUMNS: ReadonlyArray<readonly [keyof ScheduleRow, string]> = [
  ["source", "source"],
  ["job_name", "job"],
  ["schedule_expr", "排程"],
  ["deployed", "已部署"],
  ["timer_active", "啟用狀態"],
  ["last_result", "上次結果"],
  ["last_trigger", "上次觸發"],
  ["next_trigger", "下次觸發"],
  ["model_drift", "模型漂移"],
];

function cellText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "是" : "未安裝";
  return String(value);
}

// 純渲染元件(props 注入):render 測試從這裡斷言 DRIFTED 標示與零按鈕。
export function ScheduleTable({ rows }: { rows: ScheduleRow[] }) {
  if (rows.length === 0) {
    return <InfoNotice message="沒有任何排程資料(repo 無 .timer 檔且原生 cron store 不存在)。" />;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {SCHEDULE_COLUMNS.map(([key, label]) => (
              <th key={key}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={`${row.source}-${row.job_name}-${i}`}>
              {SCHEDULE_COLUMNS.map(([key]) => {
                if (key === "model_drift") {
                  return (
                    <td key={key}>
                      {row.model_drift === "DRIFTED" ? (
                        <span className="drift-flag">
                          DRIFTED
                          {row.drift_cost_direction ? `(${row.drift_cost_direction})` : ""}
                        </span>
                      ) : (
                        row.model_drift
                      )}
                    </td>
                  );
                }
                return <td key={key}>{cellText(row[key])}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SchedulePanel() {
  const { data, error, loading, reload } = useApiData(
    () => apiGet<ScheduleRow[]>("/api/schedule-table"),
    [],
  );
  return (
    <Panel
      title="Cron/Timer 排程表"
      caption="systemd timer + Hermes 原生 cron 的統一排程健康表(唯讀);模型漂移旗標只偵測、只標示,不提供任何 pin/對齊動作。DRIFTED = 該 unpinned job 下次觸發會因花費保護 fail-closed。"
      actions={<RefreshButton onClick={reload} loading={loading} />}
    >
      {error && <ErrorNotice message={error} />}
      {data && <ScheduleTable rows={data} />}
    </Panel>
  );
}
