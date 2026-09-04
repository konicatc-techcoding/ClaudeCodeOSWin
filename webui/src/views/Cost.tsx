// 成本:與 dashboard/app.py tab_cost 對等——總成本/平均成本/計費 job 數、
// 依 source 分組(長條圖+表格)。
import { apiGet, type CostSummary } from "../api";
import { ErrorNotice, InfoNotice, Metric, Panel, RefreshButton, useApiData } from "./common";
import JobsDataAgePanel from "./JobsDataAge";

export default function CostView() {
  const { data, error, loading, reload } = useApiData(() => apiGet<CostSummary>("/api/cost-summary"), []);

  const maxTotal = data && data.by_source.length > 0 ? Math.max(...data.by_source.map((r) => r.total)) : 0;

  return (
    <div className="data-page">
      <div className="page-toolbar">
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      {error && <ErrorNotice message={error} />}
      {/* 成本數字同樣來自 jobs.db 快照(Windows 側沒有 runtime db)——
          先講資料多舊,再看數字。 */}
      <JobsDataAgePanel />
      {data && (
        <Panel title="成本統計">
          <div className="metric-row">
            <Metric label="總成本 (USD)" value={`$${data.total.toFixed(4)}`} />
            <Metric label="平均成本 (USD)" value={`$${data.avg.toFixed(4)}`} />
            <Metric label="計費 job 數" value={data.count} />
          </div>

          {data.by_source.length === 0 ? (
            <InfoNotice message="目前沒有任何有成本紀錄的 job" />
          ) : (
            <>
              <div className="bar-chart">
                {data.by_source.map((row) => (
                  <div key={row.source} className="bar-row">
                    <span>{row.source}</span>
                    <div className="bar-track">
                      <div
                        className="bar-fill"
                        style={{ width: `${maxTotal > 0 ? (row.total / maxTotal) * 100 : 0}%` }}
                      />
                    </div>
                    <em>${row.total.toFixed(4)}</em>
                  </div>
                ))}
              </div>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>source</th>
                      <th>total</th>
                      <th>n</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_source.map((row) => (
                      <tr key={row.source}>
                        <td>{row.source}</td>
                        <td>${row.total.toFixed(4)}</td>
                        <td>{row.n}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Panel>
      )}
    </div>
  );
}
