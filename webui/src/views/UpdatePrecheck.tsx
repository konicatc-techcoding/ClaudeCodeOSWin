// Hermes 更新——唯讀升級預檢(階段一;設計正本 docs/webui-update-button-proposal.md §3)。
//
// **本檔純唯讀**:本 view 只顯示兩端(Windows/WSL)的升級預檢狀態,**零執行/
// 升級/同步按鈕**——階段二寫入(ff-only merge/依賴重建/服務重啟)未核准。
// 本檔的互動:「重新整理(重跑唯讀預檢)」讀取鈕(RefreshButton,onClick 只接
// reload),以及掛載獨立元件 UpdateFetch.tsx 的〔重新整理遠端資訊〕鈕
// (2026-08-04 拍板的第四個寫入例外,提案 §9 項 2——寫入面**不在本檔**,
// 集中於 UpdateFetch.tsx + bridge 第三群組;本檔維持無直連網路呼叫、
// 無自訂按鈕元素的既有靜態鎖定)。此邊界由 update-precheck-render.test.mjs
// 與 scripts/webui_security_check.py 第 11/13 項靜態鎖定。
// fetch 完成後以 `fresh=1` 重查預檢(繞過資料層 45 秒快取,否則按完看到的
// 還是舊資料——2026-08-04 實測教訓)。
//
// **多基準**(2026-07-24 防重演落地後的修正):每端同時列出所有比較基準——
// backup(私有備份/防重演基準:本機與雲端是否同步)、upstream(官方上游:
// 有多少新版可吸收)、follow(應跟隨的權威基準 = Windows 整合 tip;
// 2026-08-03 新增,提案 §10.1);peer(其他基準)僅供參考不計入整體燈。
// 整體燈取**計入組**(counts_toward_overall)中較嚴重者,並以 overall_driver
// 標示是哪一組造成的,避免「備份健康但官方有新版」被誤讀成壞掉。
// **follow 組的語意與 upstream 相反**——落後 = 該同步了(藍);領先/分歧 = 異常
// (橙)。顯示順序把 follow 排在最前:那是 WSL 端最該被看見的一條。
// **2026-08-03 拍板(選項 b)**:follow 存在的 target 上,upstream 組由後端
// 降為資訊性(counts_toward_overall=false)——照常顯示數字/diverge/summary,
// 但與 peer 一樣標「僅供參考」,整體燈由 follow 獨力驅動(WSL 現況);
// 無 follow 的 target(Windows)不受影響。UI 不依 role 自行判斷計入與否,
// 「僅供參考」標記一律由後端 counts_toward_overall 欄位驅動。
//
// 五態燈:green 已最新／blue 可 ff-only 前進／orange 帶客製 diverge 需受控
// merge／red 異常需人工檢查／gray 無法查詢。取數只有 apiGet 一條路。
import { useRef } from "react";
import {
  apiGet,
  type RepoGuardLight,
  type RepoGuardStatus,
  type UpdateComparison,
  type UpdateLight,
  type UpdatePrecheckPayload,
  type UpdateTarget,
} from "../api";
import { UpdateFetchButton } from "../UpdateFetch";
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
  // 顯示順序:應跟隨的權威基準(Windows 整合 tip)→ 備份 → 官方 → 其他參考
  const order: Record<string, number> = { follow: 0, backup: 1, upstream: 2, peer: 3 };
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

// 未推送 commit 的離線保險快照(批次 1 止血)——**唯讀顯示,零操作入口**。
// 資料隨 /api/update-precheck 同一個 payload 回來(不另開取數 URL),後端
// dashboard/data_repo_guard.py 只讀 guard 產出的 _latest.json,**不會觸發
// guard 執行**;沒有排程(拍板不建 Task Scheduler),所以要誠實顯示快照多舊。
//
// **與上方 backup 組的橙燈不打架**:backup 組講「現在有沒有未 push」(live git,
// ahead>0 → 橙);本區塊講「萬一被吃掉救不救得回來」(舊快照,green/yellow/gray,
// 刻意不用 orange)。文案也明講「此為快照,不代表目前暴露狀態」。
const GUARD_LIGHT_COLORS: Record<RepoGuardLight, string> = {
  green: "#34d399",
  yellow: "#fbbf24", // 沿用既有黃(.cred-consistency-yellow / .service-note-converging 同一顆)
  gray: "#6b7280",
};

function bytesText(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function RepoGuardPanel({ guard }: { guard: RepoGuardStatus | null | undefined }) {
  if (!guard) {
    return <InfoNotice message="未取得離線保險快照狀態（後端未提供 repo_guard 欄位）。" />;
  }
  return (
    <div className={`guard-panel guard-light-${guard.overall_light}`}>
      <div className="guard-head">
        <span
          className="update-dot"
          style={{ background: GUARD_LIGHT_COLORS[guard.overall_light] }}
          aria-hidden="true"
        />
        <b>未推送 commit 的離線保險（bundle 快照）</b>
        <span className="update-ref-note">
          {guard.scheduled ? "已排程" : "無排程（手動重跑）"}
        </span>
      </div>
      <p className="guard-note">{guard.note}</p>
      <div className="guard-targets">
        {guard.targets.map((t) => (
          <div key={t.id} className={`guard-target guard-light-${t.light}`}>
            <div className="guard-target-head">
              <span
                className="update-dot"
                style={{ background: GUARD_LIGHT_COLORS[t.light] }}
                aria-hidden="true"
              />
              <b>{t.label}</b>
              <span className="update-badge" aria-label={`保險狀態:${t.light_text}`}>
                {t.light_text}
              </span>
            </div>
            <p className="guard-target-summary">{t.summary}</p>
            <div className="guard-target-facts">
              <span>
                最近一次保險 <b>{t.age_text ?? "—"}</b>
              </span>
              <span>
                當時保全 <b>{numText(t.covered_commits)}</b> 個 commit
              </span>
              <span>
                bundle <b>{bytesText(t.bundle_bytes)}</b>
              </span>
            </div>
            {t.covered_refs.length > 0 && (
              <p className="guard-target-refs">涵蓋 ref：{t.covered_refs.join("、")}</p>
            )}
          </div>
        ))}
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
      <RepoGuardPanel guard={payload.repo_guard} />
    </div>
  );
}

export default function UpdatePrecheckView() {
  // fetch 按鈕完成後的下一次取數帶 fresh=1(繞過資料層 45 秒快取拿新 refs);
  // 一般 reload 照常吃快取。兩個 URL 都是字面常數,無其他參數化入口。
  const freshNext = useRef(false);
  const { data, error, loading, reload } = useApiData(() => {
    const useFresh = freshNext.current;
    freshNext.current = false;
    return apiGet<UpdatePrecheckPayload>(useFresh ? "/api/update-precheck?fresh=1" : "/api/update-precheck");
  }, []);
  return (
    <div className="data-page">
      <div className="page-toolbar">
        {/* 本檔的讀取操作:重新整理(重跑唯讀預檢)——仍零執行/升級/同步按鈕。
            〔重新整理遠端資訊〕fetch 鈕是獨立元件(寫入面在 UpdateFetch.tsx
            + bridge 第三群組),完成後以 fresh=1 重查。 */}
        <RefreshButton onClick={reload} loading={loading} />
        <UpdateFetchButton
          onCompleted={() => {
            freshNext.current = true;
            reload();
          }}
        />
      </div>
      {error && <ErrorNotice message={error} />}
      {data && (
        <Panel
          title="Hermes 更新——唯讀升級預檢（階段一）"
          caption="兩端並列，每端同時對所有可辨識基準比較——「Windows 整合 tip（本端是否跟上）」「私有備份/防重演基準」「官方上游」；顯示版本/落後/能否 ff/客製 diverge/rescue ref/服務狀態。零升級/合併/同步執行鈕（階段二寫入未核准）；〔重新整理遠端資訊〕為本頁唯一寫入——只 fetch 更新 remote-tracking refs，不碰工作樹與本地 branch。遠端資訊平時只讀本地 refs，可能過期。"
        >
          <UpdatePrecheckCards payload={data} />
        </Panel>
      )}
    </div>
  );
}
