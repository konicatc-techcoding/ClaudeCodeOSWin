// Memory:與 dashboard/app.py tab_memory 對等——
// inbox pending/processed/failed 計數、正本記憶檔案清單。
import { apiGet, type InboxCounts } from "../api";
import { ErrorNotice, InfoNotice, Metric, Panel, RefreshButton, useApiData } from "./common";

type MemoryData = { counts: InboxCounts; files: string[] };

async function loadMemory(): Promise<MemoryData> {
  const [counts, files] = await Promise.all([
    apiGet<InboxCounts>("/api/memory/inbox-counts"),
    apiGet<string[]>("/api/memory/files"),
  ]);
  return { counts, files };
}

export default function MemoryView() {
  const { data, error, loading, reload } = useApiData(loadMemory, []);

  return (
    <div className="data-page">
      <div className="page-toolbar">
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      {error && <ErrorNotice message={error} />}
      {data && (
        <>
          <Panel title="Memory Inbox">
            <div className="metric-row">
              <Metric label="待整併 (pending)" value={data.counts.pending} />
              <Metric label="已整併 (processed)" value={data.counts.processed} />
              <Metric label="已拒收 (failed)" value={data.counts.failed} />
            </div>
          </Panel>
          <Panel title="正本記憶檔案">
            {data.files.length === 0 ? (
              <InfoNotice message="memory/ 底下沒有正本檔案" />
            ) : (
              <ul className="file-list">
                {data.files.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
