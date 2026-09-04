// Jobs:與 dashboard/app.py tab_jobs 對等——
// 可篩選(筆數/狀態/來源)的最近 job 列表;貼 job id 看完整內容+對應 log。
import { useState } from "react";
import { apiGet, ApiError, JOB_SOURCES, JOB_STATUSES, type Job, type LogTail } from "../api";
import { ErrorNotice, InfoNotice, Panel, RefreshButton, useApiData } from "./common";
import JobsFreshnessPanel from "./JobsFreshness";
import JobsDataAgePanel from "./JobsDataAge";

const TABLE_COLUMNS = [
  "id",
  "status",
  "source",
  "thread_id",
  "attempts",
  "max_attempts",
  "cost_usd",
  "created_at",
  "completed_at",
] as const;

export default function JobsView() {
  const [limit, setLimit] = useState(50);
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");

  const query = new URLSearchParams({ limit: String(limit) });
  if (statusFilter) query.set("status", statusFilter);
  if (sourceFilter) query.set("source", sourceFilter);
  const { data: jobs, error, loading, reload } = useApiData(
    () => apiGet<Job[]>(`/api/jobs?${query.toString()}`),
    [limit, statusFilter, sourceFilter],
  );

  return (
    <div className="data-page">
      <div className="page-toolbar">
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      {error && <ErrorNotice message={error} />}

      {/* 管線新鮮度置於列表之上:這一頁本來就看得到 source/status,但**要人
          主動去篩才看得到異常**。先給結論(哪個 source 死了/退化),再讓人往
          下翻明細——與總覽頁共用同一個元件與同一個唯讀端點,不另起判準。 */}
      <JobsFreshnessPanel />

      {/* 表格是最容易被誤讀成「即時狀態」的東西:Windows 側這些列來自 WSL
          推來的快照,故在表格正上方再標一次資料年齡(獨立取數,失敗不影響表格)。 */}
      <JobsDataAgePanel />

      <Panel title="最近 Jobs">
        <div className="form-row">
          <div className="form-field">
            <label htmlFor="jobs-limit">顯示筆數(5–500)</label>
            <input
              id="jobs-limit"
              type="number"
              min={5}
              max={500}
              step={5}
              value={limit}
              onChange={(e) => setLimit(Math.min(500, Math.max(5, Number(e.target.value) || 5)))}
            />
          </div>
          <div className="form-field">
            <label htmlFor="jobs-status">篩選狀態</label>
            <select id="jobs-status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">(全部)</option>
              {JOB_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="jobs-source">篩選來源</label>
            <select id="jobs-source" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
              <option value="">(全部)</option>
              {JOB_SOURCES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>

        {jobs && jobs.length === 0 && <InfoNotice message="沒有符合條件的 job" />}
        {jobs && jobs.length > 0 && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  {TABLE_COLUMNS.map((col) => (
                    <th key={col}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    {TABLE_COLUMNS.map((col) => (
                      <td key={col}>{job[col] == null ? "" : String(job[col])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <JobDetailPanel />
    </div>
  );
}

function JobDetailPanel() {
  const [jobIdInput, setJobIdInput] = useState("");
  const [detail, setDetail] = useState<{ job: Job; log: string } | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function lookup() {
    const jobId = jobIdInput.trim();
    if (!jobId) return;
    setLoading(true);
    setDetail(null);
    setDetailError(null);
    try {
      const job = await apiGet<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
      let log = "";
      try {
        const tail = await apiGet<LogTail>(`/api/logs/${encodeURIComponent(jobId)}.log?lines=200`);
        log = tail.content;
      } catch {
        log = `(找不到 ${jobId}.log)`;
      }
      setDetail({ job, log });
    } catch (err) {
      setDetailError(
        err instanceof ApiError && err.status === 404 ? "找不到這個 job id" : err instanceof Error ? err.message : "讀取失敗",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <Panel title="單筆 Job 詳細內容">
      <div className="form-row">
        <div className="form-field grow">
          <label htmlFor="job-id-input">貼上 job id</label>
          <input
            id="job-id-input"
            type="text"
            value={jobIdInput}
            onChange={(e) => setJobIdInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void lookup();
            }}
            placeholder="job id"
          />
        </div>
        <button className="refresh-button" type="button" onClick={() => void lookup()} disabled={loading || !jobIdInput.trim()}>
          {loading ? "查詢中…" : "查詢"}
        </button>
      </div>
      {detailError && <ErrorNotice message={detailError} />}
      {detail && (
        <>
          <pre className="code-block">{JSON.stringify(detail.job, null, 2)}</pre>
          <h3 className="sub-heading">對應的 log</h3>
          <pre className="code-block">{detail.log}</pre>
        </>
      )}
    </Panel>
  );
}
