// 〔重新整理遠端資訊〕fetch 按鈕 — 第四個寫入例外(2026-08-04 使用者拍板,
// docs/webui-update-button-proposal.md §9 待拍板項 2;bridge 白名單第三群組)。
//
// 邊界(全部拍板內容):
// - **一顆鈕、零參數**:POST bridge 8787 的 /api/repo/fetch-remotes,不帶
//   body、不帶 query——四條 fetch 指令全部凍結在 bridge 端(FETCH_COMMANDS),
//   UI 技術上傳不進任何參數(bridge 不讀 body、route 全字串比對)。
// - **per-remote fail-loud**:bridge 回傳四條各自的成敗(id/label/exitCode/
//   錯誤尾段),本元件逐條顯示——官方 GitHub 逾時但本機 origin 成功時,
//   使用者看得到是哪條壞了;絕不整體靜默。
// - **執行中防連點**:busy 期間按鈕停用並顯示進行中(四條循序,最長 4×60 秒)。
// - **完成後(含部分失敗)呼叫 onCompleted**:由 UpdatePrecheck view 以
//   `fresh=1` 重查預檢(繞過 45 秒快取)——fetch 可能已部分成功,重查無害
//   且必要。
// - 本檔是 UpdatePrecheck.tsx 之外的獨立元件:該 view 檔維持「無直連
//   fetch(、無自訂 <button、onClick 僅 reload」的既有靜態鎖定;寫入面
//   集中在本檔,由 webui_security_check.py 第 13 項單獨鎖定。
import { useState } from "react";

const BRIDGE_URL = "http://127.0.0.1:8787";
export const FETCH_REMOTES_ROUTE = "/api/repo/fetch-remotes";
// 四條 × 60 秒 + 餘裕(bridge 端每條 timeout 60 秒,循序執行)
const CLIENT_TIMEOUT_MS = 4 * 60_000 + 20_000;

export type FetchRemoteResult = {
  id: string;
  label: string;
  ok: boolean;
  exitCode: number | null;
  error: string | null;
};

type FetchRemotesResponse = { ok: boolean; results?: FetchRemoteResult[]; error?: string };

export function UpdateFetchButton({ onCompleted }: { onCompleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<FetchRemoteResult[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function run() {
    if (busy) return; // 防連點(busy 期間按鈕也是 disabled,雙保險)
    setBusy(true);
    setFailure(null);
    setResults(null);
    try {
      const response = await fetch(`${BRIDGE_URL}${FETCH_REMOTES_ROUTE}`, {
        method: "POST",
        signal: AbortSignal.timeout(CLIENT_TIMEOUT_MS),
      });
      const body = (await response.json()) as FetchRemotesResponse;
      if (response.ok) {
        setResults(body.results ?? []);
      } else {
        setFailure(body.error ?? `bridge 回應 ${response.status}`);
      }
    } catch {
      setFailure("無法連線 Local Bridge(127.0.0.1:8787)——請先啟動 npm run local,或逾時");
    } finally {
      setBusy(false);
      // fetch 可能已部分成功——無論成敗都讓預檢以 fresh=1 重查(唯讀)
      onCompleted();
    }
  }

  return (
    <div className="update-fetch">
      <button className="refresh-button" type="button" onClick={run} disabled={busy}>
        {busy ? "遠端重新整理中…(最長約 4 分鐘)" : "⇣ 重新整理遠端資訊(fetch)"}
      </button>
      <small className="update-fetch-note">
        四條固定 git fetch(純加法:不刪任何 refs、不碰工作樹);完成後自動重查預檢
      </small>
      {failure && <p className="update-fetch-failure">{failure}</p>}
      {results && (
        <ul className="update-fetch-results">
          {results.map((r) => (
            <li key={r.id} className={r.ok ? "update-fetch-ok" : "update-fetch-fail"}>
              {r.ok ? "✓" : "✗"} {r.label}
              {!r.ok && (
                <em>
                  {r.exitCode !== null ? `(exit=${r.exitCode})` : ""}
                  {r.error ? ` ${r.error}` : ""}
                </em>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
