// Jobs 管線「新鮮度」燈號（2026-09-04；資料層 dashboard/data_jobs_freshness.py）。
//
// 為什麼要有這一塊:2026-08-05 起執行鏈全線 dead_letter,31 天無人察覺。
// 既有觀測面只有「全時段累計」(status-counts)——28 筆 cron dead_letter 混在
// 758 筆歷史 completed 裡,數字上一點都不刺眼。這張卡補的是**新鮮度**這個
// 維度:「最近這個 window 內,這條管線到底有沒有在跑」。
//
// 唯讀邊界:本卡片零操作入口(除了共用的讀取型 RefreshButton),取數只經
// apiGet;端點本身不送 Slack、不寫任何東西、不觸發任何 job。
//
// 燈色**完全由後端給**(dashboard/data_jobs_freshness.STATE_LIGHTS),前端只
// 渲染,不在這裡重算判準或門檻——UI 與 Slack 看門狗共用同一份 core 判準與
// 同一份 registry/jobs_watchdog.yaml。五態 → 色:
//   trigger_dead / executor_dead → orange;executor_degraded → yellow;
//   healthy → green;inconclusive → gray(**正常的「還在跑」,不是警示**)。
import {
  apiGet,
  type FreshnessLight,
  type FreshnessSource,
  type JobsFreshnessPayload,
} from "../api";
import { ErrorNotice, InfoNotice, Panel, RefreshButton, useApiData } from "./common";

// 沿用既有 token(同 .update-light-* / .guard-light-* 的四顆);不新增色彩系統。
// 刻意不含 red——紅在本系統是常駐/服務層級的「不可用」語意,不搶那顆燈。
const FRESH_LIGHT_COLORS: Record<FreshnessLight, string> = {
  green: "#34d399",
  yellow: "#fbbf24",
  orange: "#fb923c",
  gray: "#6b7280",
};

function SourceRow({ row }: { row: FreshnessSource }) {
  return (
    <div className={`fresh-source fresh-light-${row.light}`}>
      <div className="fresh-source-head">
        <span
          className="update-dot"
          style={{ background: FRESH_LIGHT_COLORS[row.light] }}
          aria-hidden="true"
        />
        <b>{row.source}</b>
        <span className="update-badge" aria-label={`新鮮度狀態:${row.state_label}`}>
          {row.state_short}
        </span>
        <i className="update-ref-note">{row.state}</i>
      </div>
      <p className="fresh-source-reason">{row.reason}</p>
      <div className="fresh-source-facts">
        <span>
          window <b>{row.lookback_hours}h</b>
        </span>
        <span>
          進件 <b>{row.enqueued}</b>
        </span>
        <span>
          完成 <b>{row.completed}</b>
        </span>
        <span>
          死信 <b>{row.dead_letter}</b>
        </span>
        <span>
          卡住 <b>{row.stuck}</b>
        </span>
      </div>
      <p className="fresh-source-last">
        最後一次成功：{row.last_completed_age_text}
        {row.last_completed_at ? `（${row.last_completed_at}）` : ""}
        {row.expect_enqueue ? "｜有排程觸發器" : "｜事件驅動（零進件屬正常）"}
      </p>
    </div>
  );
}

// 純渲染元件(props 注入)——render 測試由此斷言五態與零操作入口。
export function JobsFreshnessCard({ payload }: { payload: JobsFreshnessPayload | null }) {
  if (!payload) {
    return <InfoNotice message="未取得 jobs 管線新鮮度（唯讀 API 未連線）。" />;
  }
  return (
    <div className={`fresh-panel fresh-light-${payload.overall_light}`}>
      <div className="fresh-head">
        <span
          className="update-dot"
          style={{ background: FRESH_LIGHT_COLORS[payload.overall_light] }}
          aria-hidden="true"
        />
        <b>管線新鮮度</b>
        <span className="update-badge" aria-label={`整體新鮮度:${payload.overall_text}`}>
          {payload.overall_text}
        </span>
        <i className="update-ref-note">即時計算（非快照）</i>
      </div>
      <p className="fresh-summary">{payload.summary}</p>
      {/* unavailable:灰燈 + 誠實原因。灰 ≠ 沒事,文案上必須講清楚。 */}
      {!payload.available && payload.reason && (
        <p className="fresh-unavailable">無法判斷的原因：{payload.reason}</p>
      )}
      {payload.thresholds && (
        <p className="fresh-thresholds">
          門檻（registry/jobs_watchdog.yaml）：window {payload.thresholds.lookback_hours}h、
          最少進件 {payload.thresholds.min_expected_enqueued} 筆、卡住判準{" "}
          {payload.thresholds.stuck_backlog_hours}h、死信比例{" "}
          {Math.round(payload.thresholds.dead_letter_ratio_threshold * 100)}%（樣本 ≥
          {payload.thresholds.min_terminal_sample}）
        </p>
      )}
      {payload.sources.length > 0 && (
        <div className="fresh-sources">
          {payload.sources.map((row) => (
            <SourceRow key={row.source} row={row} />
          ))}
        </div>
      )}
      <p className="fresh-note">{payload.note}</p>
    </div>
  );
}

export default function JobsFreshnessPanel() {
  // 獨立取數(比照 SchedulePanel):本區塊失敗不影響同頁其他區塊。
  const { data, error, loading, reload } = useApiData(
    () => apiGet<JobsFreshnessPayload>("/api/jobs-freshness"),
    [],
  );
  return (
    <Panel
      title="Jobs 管線新鮮度"
      caption="回答「這條管線最近有沒有在跑」——這是全時段累計數字看不出來的維度（2026-08 的 31 天靜默就是這樣被埋掉的）。即時對 jobs.db 唯讀計算，判準與門檻與 Slack 看門狗共用同一份 registry/jobs_watchdog.yaml。橙＝觸發端或執行端死了；黃＝死信比例超標；綠＝健康；灰＝進行中或無法判斷（灰不等於沒事）。"
      actions={<RefreshButton onClick={reload} loading={loading} />}
    >
      {error && <ErrorNotice message={error} />}
      {data && <JobsFreshnessCard payload={data} />}
    </Panel>
  );
}
