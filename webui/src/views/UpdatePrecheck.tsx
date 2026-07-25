// Hermes 更新——唯讀升級預檢(階段一;設計正本 docs/webui-update-button-proposal.md §3)。
//
// **純唯讀**:本 view 只顯示兩端(Windows/WSL)的升級預檢狀態,**零執行/升級/
// 同步按鈕**——階段二寫入(ff-only merge/依賴重建/服務重啟)未核准,UI 不得出現
// 任何操作入口。唯一允許的互動是「重新整理(重跑唯讀預檢)」的讀取鈕(RefreshButton,
// onClick 只接 reload 重新取數)。此邊界由 update-precheck-render.test.mjs
// 與 scripts/webui_security_check.py 第 11 項靜態鎖定。
//
// **雙基準**(2026-07-24 防重演落地後的修正):每端同時列出所有比較基準——
// backup(私有備份/防重演基準:本機與雲端是否同步)與 upstream(官方上游:
// 有多少新版可吸收);peer(其他基準)僅供參考不計入整體燈。整體燈取
// backup/upstream 兩組較嚴重者,並以 overall_driver 標示是哪一組造成的,
// 避免「備份健康但官方有新版」被誤讀成壞掉。
//
// 五態燈:green 已最新／blue 可 ff-only 前進／orange 帶客製 diverge 需受控
// merge／red 異常需人工檢查／gray 無法查詢。取數只有 apiGet 一條路。
import {
  apiGet,
  type UpdateComparison,
  type UpdateLight,
  type UpdatePrecheckPayload,
  type UpdateTarget,
} from "../api";
import { ErrorNotice, InfoNotice, Panel, RefreshButton, useApiData } from "./common";

const LIGHT_COLORS: Record<UpdateLight, string> = {
  green: "#34d399",
  blue: "#60a5fa",
  orange: "#fb923c",
  red: "#f87171",
  gray: "#6b7280",
};

function numText(value: number | null | undefined): string {
  return value == null ? "—" : String(value);
}

// 單一比較基準區塊(backup / upstream / peer 各一)
export function ComparisonBlock({
  comparison,
  isDriver,
}: {
  comparison: UpdateComparison;
  isDriver: boolean;
}) {
  const c = comparison;
  return (
    <div className={`update-basis update-light-${c.light}`}>
      <div className="update-basis-head">
        <span className="update-dot" style={{ background: LIGHT_COLORS[c.light] }} aria-hidden="true" />
        <b>{c.role_label}</b>
        {isDriver && <i className="update-driver">主導整體燈</i>}
        {!c.counts_toward_overall && <i className="update-ref-note">僅供參考</i>}
      </div>
      <p className="update-basis-ref">
        {c.remote} → {c.ref}
        {c.tip ? ` @ ${c.tip}` : ""}
      </p>
      <div className="update-basis-counts">
        <span>
          落後 <b>{numText(c.behind)}</b>
        </span>
        <span>
          領先(客製) <b>{numText(c.ahead)}</b>
        </span>
        <span>{c.can_ff === true ? "可 ff-only" : c.can_ff === false ? "非 ff" : "—"}</span>
      </div>
      <p className="update-basis-summary">{c.summary}</p>
      {c.diverge_commits && c.diverge_commits.length > 0 && (
        <details className="update-diverge">
          <summary>本地分歧 commit（{c.diverge_commits.length}，不展開 diff）</summary>
          <ul>
            {c.diverge_commits.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// 單一 target 卡片(props 注入,不經 fetch)——render 測試從這裡斷言五態與零按鈕。
export function UpdateTargetCard({ target }: { target: UpdateTarget }) {
  const light = target.light;
  const head = target.head;
  const rescue = target.rescue_refs ?? [];
  const comparisons = target.comparisons ?? [];
  // 顯示順序:備份 → 官方 → 其他參考
  const order: Record<string, number> = { backup: 0, upstream: 1, peer: 2 };
  const sorted = [...comparisons].sort((a, b) => (order[a.role] ?? 9) - (order[b.role] ?? 9));

  return (
    <div className={`update-card update-light-${light}`}>
      <div className="update-card-head">
        <span className="update-dot" style={{ background: LIGHT_COLORS[light] }} aria-hidden="true" />
        <div className="update-card-title">
          <b>{target.label}</b>
          <small>{target.repo}</small>
        </div>
        <span className="update-badge" aria-label={`狀態:${target.light_text}`}>
          {target.light_text}
        </span>
      </div>

      <p className="update-advice">{target.advice}</p>

      {target.blocking_reasons && target.blocking_reasons.length > 0 && (
        <ul className="update-reasons">
          {target.blocking_reasons.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      )}

      <div className="update-facts">
        {/* live 版本字串(提案 §3.1 第一項):v<pkg> upstream <base> + local <head>。
            資料層取自 HEAD 的 pyproject.toml blob + merge-base,**未執行 hermes CLI**
            ——顯示層在此只是把字串印出來,不新增任何取數路徑。 */}
        <div className="update-version">
          <span>版本（live）</span>
          <b>{target.live_version?.text ?? "—"}</b>
          <small>{target.live_version?.text ? "版本取自 HEAD 的 pyproject.toml（editable install）；未執行 hermes CLI" : ""}</small>
        </div>
        <div>
          <span>HEAD</span>
          <b>{head?.short ?? "—"}</b>
          <small>{head?.describe ?? ""}</small>
        </div>
        <div>
          <span>branch</span>
          <b>{head?.branch ?? "—"}</b>
          {/* 工作樹狀態:字重+顏色 pill,兩態一眼可辨(dirty 未知時不顯示,不臆測) */}
          {target.dirty != null && (
            <span className={`update-tree update-tree-${target.dirty ? "dirty" : "clean"}`}>
              {target.dirty ? "▲ 工作樹髒" : "✓ 工作樹乾淨"}
            </span>
          )}
        </div>
      </div>

      {sorted.length > 0 ? (
        <div className="update-bases">
          {sorted.map((c) => (
            <ComparisonBlock key={c.remote} comparison={c} isDriver={target.overall_driver === c.role} />
          ))}
        </div>
      ) : (
        <p className="update-service">無可用的比較基準（未偵測到 remote）。</p>
      )}

      {target.service && (
        <p className="update-service">
          服務狀態（{target.service.kind}）：{target.service.detail}
        </p>
      )}

      <div className="update-rescue">
        <span>rescue ref（{target.rescue_count ?? rescue.length}）</span>
        {rescue.length === 0 ? (
          <em>{target.expect_rescue ? "無（預期存在——注意 rollback 錨遺失）" : "無"}</em>
        ) : (
          <ul>
            {rescue.map((ref) => (
              <li key={ref.name}>
                {ref.name} → {ref.object} <i>({ref.type})</i>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// 兩端並列(props 注入)——render 測試主要入口。
export function UpdatePrecheckCards({ payload }: { payload: UpdatePrecheckPayload | null }) {
  if (!payload) {
    return <InfoNotice message="無法取得升級預檢（唯讀 API 未連線）。" />;
  }
  return (
    <div className="update-precheck">
      <InfoNotice message={payload.remote_note} />
      <div className="update-grid">
        {payload.targets.map((target) => (
          <UpdateTargetCard key={target.id} target={target} />
        ))}
      </div>
    </div>
  );
}

export default function UpdatePrecheckView() {
  const { data, error, loading, reload } = useApiData(
    () => apiGet<UpdatePrecheckPayload>("/api/update-precheck"),
    [],
  );
  return (
    <div className="data-page">
      <div className="page-toolbar">
        {/* 唯一允許的操作:重新整理(重跑唯讀預檢)——零執行/升級/同步按鈕 */}
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      {error && <ErrorNotice message={error} />}
      {data && (
        <Panel
          title="Hermes 更新——唯讀升級預檢（階段一）"
          caption="兩端並列，每端同時對「私有備份/防重演基準」與「官方上游」兩個基準比較；顯示版本/落後/能否 ff/客製 diverge/rescue ref/服務狀態。不提供任何執行、升級、同步操作（階段二寫入未核准）。遠端資訊只讀本地 refs，可能過期。"
        >
          <UpdatePrecheckCards payload={data} />
        </Panel>
      )}
    </div>
  );
}
