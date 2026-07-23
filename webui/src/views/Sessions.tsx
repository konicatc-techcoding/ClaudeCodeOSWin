// Hermes Sessions(P2 功能一,stage3 提案 §2):唯讀列出 Hermes sessions。
//
// 安全邊界(§2.3):欄位白名單=session_id/session_source/title/model/
// started_at/ended_at/message_count——不含 metadata 的 session_key/chat_id
// 等路由中繼資料(資料層即不回傳);**不做「點進去看對話全文」**
// (iter_events 不被呼叫,列表層天然不含 messages.content)。
import { useState } from "react";
import { apiGet, SESSION_SOURCES, type HermesSession } from "../api";
import { ErrorNotice, InfoNotice, Panel, RefreshButton, useApiData } from "./common";

// §2.3 表格欄位白名單(明確列舉,不用泛型 dump)
export const SESSION_COLUMNS: ReadonlyArray<readonly [keyof HermesSession, string]> = [
  ["session_id", "session_id"],
  ["session_source", "source"],
  ["title", "title"],
  ["model", "model"],
  ["started_at", "started_at"],
  ["ended_at", "ended_at"],
  ["message_count", "messages"],
];

function cellText(value: unknown): string {
  if (value == null) return "";
  return String(value);
}

// 純渲染元件(props 注入):render 測試從這裡斷言欄位白名單語意。
export function SessionsTable({ sessions }: { sessions: HermesSession[] }) {
  if (sessions.length === 0) {
    return (
      <InfoNotice message="沒有 session 資料(state.db 不存在,或篩選條件下沒有 session)。" />
    );
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {SESSION_COLUMNS.map(([key, label]) => (
              <th key={key}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sessions.map((session) => (
            <tr key={session.session_id}>
              {SESSION_COLUMNS.map(([key]) => (
                <td key={key}>{cellText(session[key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SessionsView() {
  const [source, setSource] = useState<string>("");
  const { data, error, loading, reload } = useApiData(
    () =>
      apiGet<HermesSession[]>(
        source ? `/api/hermes/sessions?source=${encodeURIComponent(source)}` : "/api/hermes/sessions",
      ),
    [source],
  );
  return (
    <div className="data-page">
      <div className="page-toolbar">
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      <div className="form-row">
        <div className="form-field">
          <label htmlFor="sessions-source">篩選 source</label>
          <select id="sessions-source" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">(全部)</option>
            {SESSION_SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && <ErrorNotice message={error} />}
      {data && (
        <Panel
          title="Hermes Sessions"
          caption="唯讀列表(snapshot 讀取,Hermes 運行中仍可用);不含訊息內容,不提供點入檢視全文。"
        >
          <SessionsTable sessions={data} />
        </Panel>
      )}
    </div>
  );
}
