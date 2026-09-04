// 總覽:與 dashboard/app.py tab_overview 對等——
// Worker/Adapter 常駐狀態(systemd)、Adapter 設定狀態、Job 狀態統計、領域狀態。
import {
  apiGet,
  JOB_STATUSES,
  type AdapterConfig,
  type Domain,
  type Health,
  type StatusCounts,
  type SystemdStatusPayload,
} from "../api";
import { ErrorNotice, InfoNotice, Metric, Panel, RefreshButton, useApiData, WarnNotice } from "./common";
import JobsFreshnessPanel from "./JobsFreshness";
import LocalServices from "../LocalServices";
import ServiceControl from "../ServiceControl";
import SchedulePanel from "./Schedule";

// 與 dashboard/app.py 的 SYSTEMD_LABELS 一致
const SYSTEMD_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["hermes-worker.service", "Worker"],
  ["hermes-telegram.service", "Telegram Adapter"],
  ["hermes-cron-daily-memory-check.timer", "Cron(daily-memory-check)"],
  ["hermes-rss.timer", "RSS Adapter"],
];

type OverviewData = {
  health: Health;
  systemd: SystemdStatusPayload;
  adapterConfig: AdapterConfig;
  statusCounts: StatusCounts;
  domains: Domain[];
};

async function loadOverview(): Promise<OverviewData> {
  const [health, systemd, adapterConfig, statusCounts, domains] = await Promise.all([
    apiGet<Health>("/api/health"),
    apiGet<SystemdStatusPayload>("/api/systemd-status"),
    apiGet<AdapterConfig>("/api/adapter-config"),
    apiGet<StatusCounts>("/api/status-counts"),
    apiGet<Domain[]>("/api/domains"),
  ]);
  return { health, systemd, adapterConfig, statusCounts, domains };
}

// 純渲染元件(props 注入,供 render 測試):三分支誠實狀態文字——
// (a) status "ok":真實狀態,查得到但單元不在 units 裡才顯示「未安裝」;
// (b) "wsl_down":「WSL 未運作」(distro 未運作,資料層未探測、不喚醒);
// (c) "unavailable":「無法查詢」。**不得把「查不到」顯示成「未安裝」。**
export function SystemdMetricRow({ systemd }: { systemd: SystemdStatusPayload }) {
  return (
    <>
      {systemd.status !== "ok" && systemd.reason && <InfoNotice message={systemd.reason} />}
      <div className="metric-row">
        {SYSTEMD_LABELS.map(([unit, label]) => {
          if (systemd.status !== "ok") {
            return (
              <Metric
                key={unit}
                label={label}
                value={systemd.status === "wsl_down" ? "WSL 未運作" : "無法查詢"}
              />
            );
          }
          const info = systemd.units?.[unit];
          if (!info) return <Metric key={unit} label={label} value="未安裝" />;
          // 嚴格比對 active(舊寫法 includes("active") 會把 inactive 誤判成
          // 運作中——換源修正時一併矯正,與排程表 timer_active 判準一致)
          const running = info.last_exit.split("/")[0] === "active";
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
    </>
  );
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

      {/* Jobs 管線新鮮度(2026-09-04):**放在本頁最上方**,因為總覽是進站
          第一頁,而「管線這幾天到底有沒有在跑」比下方任何一張卡都優先——
          2026-08 那 31 天靜默,正是因為這個維度在 UI 上根本不存在,而
          〔Job 狀態統計〕的全時段累計把它完全稀釋掉了。獨立取數(自帶
          RefreshButton),API 或設定壞掉只讓這一塊變灰,不影響本頁其他區塊。 */}
      <JobsFreshnessPanel />

      {/* 服務控制鍵+本機服務燈號(2026-08-03 自 sidebar 遷入,置於
          Worker/Adapter 狀態上方):兩欄 grid 左右並排,窄視窗由 auto-fit
          自然換行堆疊(不出橫向捲軸)。兩元件的安全語意(二次確認全文字、
          stop 語意明示、bridge 離線停用、白名單枚舉)全在元件內部,搬掛載點
          不改行為;取數皆為模組層共享 store(resident/bridge/PTY 各單一
          輪詢),搬動不新增輪詢——切出總覽 view 後 bridge/PTY store 因最後
          訂閱者退場而停止輪詢(合理省資源),sidebar 聚合燈常駐訂閱的
          resident 輪詢不受影響。不依賴本頁 API 取數,API 掛掉也照常顯示。 */}
      <div className="service-overview-grid">
        <ServiceControl />
        <LocalServices />
      </div>

      {data && (
        <>
          <Panel
            title="Worker / Adapter 狀態"
            caption="狀態來源是 wsl -d Ubuntu systemctl --user(Windows 側經 WSL 查詢,distro 未運作時不喚醒)。WSL 未運作 →「WSL 未運作」;查詢失敗 →「無法查詢」;查得到但單元不存在才是「未安裝」。"
          >
            <SystemdMetricRow systemd={data.systemd} />
          </Panel>

          {/* P2 功能三:統一排程健康表(stage3 提案 §4.3——併入總覽,
              與上方 SYSTEMD_LABELS 摘要卡片互補,不取代)。獨立取數:
              排程表 API 失敗不影響本頁其他區塊(兩路徑獨立退化精神)。 */}
          <SchedulePanel />

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
