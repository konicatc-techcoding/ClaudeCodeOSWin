// jobs 資料的「年齡」橫幅(2026-09-04 拓撲修正)。
//
// 為什麼非有不可:runtime jobs.db **只存在 WSL**,唯讀 API 跑在 Windows,
// 所以 Jobs 頁/成本頁/新鮮度燈號讀的是 WSL 定期推來的**快照**,不是即時資料。
// 這幾頁原本的呈現隱含「這是當下狀態」——改讀快照後那個隱含前提就不成立了,
// **不能讓使用者以為看到的是現在**。故凡是吃 jobs.db 的頁面一律掛這條橫幅。
//
// 語意由後端(dashboard/data_jobs_snapshot.py)決定,前端只渲染:
//   live    本機就有 runtime db → 即時,無年齡問題(綠)
//   fresh   快照在新鮮門檻內(綠)
//   stale   偏舊:數字仍可看,但「一切正常」這種結論不能只靠它(黃)
//   expired 過期:燈號整體已轉灰,數字只能當歷史追溯(灰)
//   never   從未產生快照(灰)   error 快照/manifest 壞掉(灰)
//
// 唯讀:零操作入口(不含任何按鈕、不含任何事件處理器),取數只經 apiGet。
import { apiGet, type JobsDataAge, type JobsFreshnessPayload } from "../api";
import { useApiData } from "./common";

const STATUS_TEXT: Record<string, string> = {
  live: "即時（runtime db）",
  fresh: "快照．新鮮",
  stale: "快照．偏舊",
  expired: "快照．已過期",
  never: "沒有快照",
  error: "快照不可用",
};

// 沿用既有四色 token(同 fresh-light-*);刻意不含 red。
const STATUS_TONE: Record<string, "green" | "yellow" | "gray"> = {
  live: "green",
  fresh: "green",
  stale: "yellow",
  expired: "gray",
  never: "gray",
  error: "gray",
};

// 純渲染元件(props 注入)——render 測試由此斷言「年齡一定看得到」。
export function DataAgeBanner({ data }: { data: JobsDataAge | null }) {
  if (!data || !data.data_status) {
    // 後端沒給資料年齡欄位時,**寧可明說不知道**,也不預設成「即時」。
    return (
      <p className="data-age data-age-gray">
        <b>資料年齡：未知</b>
        <span>後端未回報資料來源與年齡——不要假設這是即時資料。</span>
      </p>
    );
  }
  const tone = STATUS_TONE[data.data_status] ?? "gray";
  const label = STATUS_TEXT[data.data_status] ?? data.data_status;
  return (
    <p className={`data-age data-age-${tone}`}>
      <b>
        資料年齡：{label}
        {data.data_status !== "live" && data.data_age_text ? `（${data.data_age_text}）` : ""}
      </b>
      {data.data_captured_at && <i className="data-age-stamp">快照時間：{data.data_captured_at}</i>}
      <span>{data.data_summary}</span>
      {data.data_note && <em className="data-age-note">{data.data_note}</em>}
    </p>
  );
}

// 給「不自己取新鮮度」的頁面用(例如成本頁):獨立取數,只渲染橫幅。
// 與新鮮度卡片共用同一個唯讀端點與同一份判定,不另起一套資料年齡邏輯。
export default function JobsDataAgePanel() {
  const { data } = useApiData(() => apiGet<JobsFreshnessPayload>("/api/jobs-freshness"), []);
  return <DataAgeBanner data={data} />;
}
