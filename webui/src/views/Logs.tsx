// Logs:與 dashboard/app.py tab_logs 對等——選一個 log 檔案看最後 N 行。
import { useState } from "react";
import { apiGet, LOG_FILES, type LogTail } from "../api";
import { ErrorNotice, Panel, RefreshButton, useApiData } from "./common";

export default function LogsView() {
  const [logChoice, setLogChoice] = useState<string>(LOG_FILES[0]);
  const [numLines, setNumLines] = useState(200);

  const { data, error, loading, reload } = useApiData(
    () => apiGet<LogTail>(`/api/logs/${encodeURIComponent(logChoice)}?lines=${numLines}`),
    [logChoice, numLines],
  );

  return (
    <div className="data-page">
      <div className="page-toolbar">
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      {error && <ErrorNotice message={error} />}

      <Panel title="Logs">
        <div className="form-row">
          <div className="form-field">
            <label htmlFor="log-choice">選擇 log 檔案</label>
            <select id="log-choice" value={logChoice} onChange={(e) => setLogChoice(e.target.value)}>
              {LOG_FILES.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="log-lines">顯示行數:{numLines}</label>
            <input
              id="log-lines"
              type="range"
              min={20}
              max={1000}
              step={20}
              value={numLines}
              onChange={(e) => setNumLines(Number(e.target.value))}
            />
          </div>
        </div>
        {data && <pre className="code-block log-block">{data.content}</pre>}
      </Panel>
    </div>
  );
}
