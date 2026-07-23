// 總覽:與 dashboard/app.py tab_overview 對等——
// Worker/Adapter 常駐狀態(systemd)、Adapter 設定狀態、Job 狀態統計、領域狀態。
import {
  apiGet,
  JOB_STATUSES,
  type AdapterConfig,
  type Domain,
  type Health,
  type StatusCounts,
  type SystemdStatus,
} from "../api";
import { ErrorNotice, InfoNotice, Metric, Panel, RefreshButton, useApiData, WarnNotice } from "./common";

// 與 dashboard/app.py 的 SYSTEMD_LABELS 一致
const SYSTEMD_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["hermes-worker.service", "Worker"],
  ["hermes-telegram.service", "Telegram Adapter"],
  ["hermes-cron-daily-memory-check.timer", "Cron(daily-memory-check)"],
  ["hermes-rss.timer", "RSS Adapter"],
];

type OverviewData = {
  health: Health;
  systemd: SystemdStatus;
  adapterConfig: AdapterConfig;
  statusCounts: StatusCounts;
  domains: Domain[];
};

async function loadOverview(): Promise<OverviewData> {
  const [health, systemd, adapterConfig, statusCounts, domains] = await Promise.all([
    apiGet<Health>("/api/health"),
    apiGet<SystemdStatus>("/api/systemd-status"),
    apiGet<AdapterConfig>("/api/adapter-config"),
    apiGet<StatusCounts>("/api/status-counts"),
    apiGet<Domain[]>("/api/domains"),
  ]);
  return { health, systemd, adapterConfig, statusCounts, domains };
}

export default function Overview() {
  const { data, error, loading, reload } = useApiData(loadOverview, []);

  return (
    <div className="data-page">
      <div className="page-toolbar">
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      {error && <ErrorNotice message={error} />}
      {data && !data.health.jobs_db_exists && (
        <WarnNotice message="hermes/jobs.db 尚未建立(runtime 資料,不在 git 裡)。worker 或 adapter 第一次執行後就會建立;在那之前 Jobs/成本分頁會是空的。" />
      )}
      {data && (
        <>
          <Panel
            title="Worker / Adapter 狀態"
            caption="狀態來源是 systemctl --user(WSL 環境);此環境查不到時全部顯示「未安裝」。"
          >
            <div className="metric-row">
              {SYSTEMD_LABELS.map(([unit, label]) => {
                const info = data.systemd[unit];
                if (!info) return <Metric key={unit} label={label} value="未安裝" />;
                const running = info.last_exit.split("/")[0].includes("active");
                return (
                  <Metric
                    key={unit}
                    label={label}
                    value={running ? "運作中" : "已停止(等排程)"}
                    sub={info.last_exit}
                  />
                );
              })}
            </div>
          </Panel>

          <Panel
            title="Adapter 設定狀態"
            caption="只顯示「有沒有設定、設定了幾筆」,不會顯示 bot token 等密鑰本身。"
          >
            <pre className="code-block">{JSON.stringify(data.adapterConfig, null, 2)}</pre>
          </Panel>

          <Panel title="Job 狀態統計">
            <div className="metric-row">
              {JOB_STATUSES.map((status) => (
                <Metric key={status} label={status} value={data.statusCounts[status] ?? 0} />
              ))}
            </div>
          </Panel>

          <Panel title="領域狀態">
            {data.domains.length === 0 ? (
              <InfoNotice message="找不到 registry/agents.yaml" />
            ) : (
              <DomainTable domains={data.domains} />
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function DomainTable({ domains }: { domains: Domain[] }) {
  const columns = Array.from(new Set(domains.flatMap((d) => Object.keys(d))));
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {domains.map((domain, i) => (
            <tr key={String(domain.id ?? i)}>
              {columns.map((col) => (
                <td key={col}>{formatCell(domain[col])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
