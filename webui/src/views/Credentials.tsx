// 憑證/Lane 狀態(P2 功能二,stage3 提案 §3):唯讀檢視 capability lane 治理
// 表與各 Hermes profile 的憑證「治理狀態」。
//
// 安全邊界(§3.2/§3.4,UI 層規則):
// - 憑證資料**禁止泛型 JSON dump**(等價於提案對 st.json() 的禁令)——
//   欄位白名單明確列舉(ENTRY_COLUMNS),即使 API 回應意外多出欄位,
//   UI 也不會渲染它(webui/tests/stage3-render.test.mjs 有斷言)。
// - 頁面頂部警語為不可移除的固定文字(CREDENTIAL_PAGE_WARNING)。
// - 純唯讀:沒有任何「清除/重新登入/修復」操作入口(教訓四)。
import {
  apiGet,
  type CapabilityLane,
  type CredentialConsistency,
  type CredentialEntry,
  type CredentialProfile,
  type CredentialStatus,
} from "../api";
import { ErrorNotice, InfoNotice, Panel, RefreshButton, useApiData, WarnNotice } from "./common";

// 交叉檢查燈色(沿用 UpdatePrecheck.tsx / ResidentStatus.tsx 同一組色票,
// 不另立第二套語意色)。yellow = 配額耗盡的暫時狀態,與 ResidentStatus 的
// 暖機態同一顆 #fbbf24(語意一致:暫時性、會自行好轉)。
const CONSISTENCY_COLORS: Record<CredentialConsistency["light"], string> = {
  green: "#34d399",
  yellow: "#fbbf24",
  orange: "#fb923c",
  gray: "#6b7280",
};

// §3.4 警語:逐字沿用提案文字,不可移除。
export const CREDENTIAL_PAGE_WARNING =
  "本頁僅顯示治理層級的中繼資訊(有沒有設定、設定了幾筆、上次更新時間),絕不顯示 token/key 等憑證值本身。";

// lane 表欄位(capability_lanes.yaml 治理欄位+資料層標注的實際生效模型)。
// 「實際生效模型」來源是 profile/全域 config.yaml 的 model.default(資料層
// 白名單讀取),**不是** registry 的 model 欄位——registry 刻意記 null
// (模型由 profile 自身設定決定,不重複記載)。
export const LANE_COLUMNS: ReadonlyArray<readonly [string, string]> = [
  ["id", "id"],
  ["capability", "capability"],
  ["execution", "execution"],
  ["provider", "provider"],
  ["effective_model", "實際生效模型"],
  ["hermes_profile", "hermes_profile"],
  ["status", "status"],
  ["cost_tier", "cost_tier"],
  ["risk_tier", "risk_tier"],
  ["allowed_agents", "allowed_agents"],
  ["intended_use", "intended_use"],
] as const;

export const EFFECTIVE_MODEL_TOOLTIP =
  "來源:該 profile 的 config.yaml model.default(無 profile 值時繼承全域 config.yaml);非 registry 欄位";

// 生效模型 cell:區分 profile 自訂 vs 繼承全域 vs native
export function effectiveModelText(lane: CapabilityLane): string {
  switch (lane.effective_model_source) {
    case "native":
      return "(native session)";
    case "profile":
      return `${lane.effective_model}(profile 自訂)`;
    case "global":
      return `${lane.effective_model}(繼承全域)`;
    default:
      return lane.effective_model ?? "無法查詢";
  }
}

// 憑證 entry 欄位白名單(與 dashboard/data_stage3.py CREDENTIAL_ENTRY_ALLOWLIST 一致)
export const ENTRY_COLUMNS = ["id", "priority", "last_status", "last_refresh", "source", "label"] as const;

// 配額耗盡(Hermes 對 HTTP 429 的既有標記)——資料本來就有,只是過去沒有
// 視覺呈現;此處只做紅色標示,不提供任何修復/重登入入口(唯讀邊界)。
export const EXHAUSTED_STATUS = "exhausted";

// ⚠ 命名紀律(2026-08-04):畫面上有兩條 provider 軸,措辭必須一眼可辨——
// 「憑證 provider」= 此 store 存了誰的憑證;「生效模型 provider」= 現在設定
// 用誰跑模型。兩者過去都叫 provider,是「改了全域 model.provider 卻看不到
// 變化」被誤判成 bug 的直接成因。
export const CREDENTIAL_PROVIDER_LABEL = "憑證 provider";
export const EFFECTIVE_PROVIDER_LABEL = "生效模型 provider";

export const MODEL_SOURCE_TEXT: Record<string, string> = {
  profile: "profile 自訂",
  global: "繼承全域 config.yaml",
  unknown: "無法查詢",
};

function cellText(value: unknown): string {
  // null/undefined 顯示明確的「—」而非空白(capability_lanes.yaml 允許
  // model: null——hermes lane 的模型由 profile 自身設定決定,registry 不
  // 重複記載;空白會被誤讀為「沒帶到值」)
  if (value == null) return "—";
  if (Array.isArray(value)) return value.map((v) => cellText(v)).join(", ");
  if (typeof value === "object") return "(略)"; // 白名單外的巢狀物件不展開
  return String(value);
}

export function LaneTable({ lanes }: { lanes: CapabilityLane[] }) {
  if (lanes.length === 0) {
    return <InfoNotice message="找不到 registry/capability_lanes.yaml 或其中沒有 lane。" />;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {LANE_COLUMNS.map(([key, label]) => (
              <th key={key} title={key === "effective_model" ? EFFECTIVE_MODEL_TOOLTIP : undefined}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lanes.map((lane, i) => (
            <tr key={String(lane.id ?? i)}>
              {LANE_COLUMNS.map(([key]) => (
                <td key={key}>{key === "effective_model" ? effectiveModelText(lane) : cellText(lane[key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EntryTable({ entries }: { entries: CredentialEntry[] }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          {ENTRY_COLUMNS.map((col) => (
            <th key={col}>{col}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {entries.map((entry, i) => {
          // 配額耗盡:整列上紅底 + last_status 上紅色 pill(資料早就有,
          // 過去只是沒有視覺呈現)。純標示,無任何操作入口。
          const exhausted = entry.last_status === EXHAUSTED_STATUS;
          return (
            <tr key={`${entry.id ?? i}`} className={exhausted ? "cred-entry-exhausted" : undefined}>
              {ENTRY_COLUMNS.map((col) =>
                col === "last_status" && exhausted ? (
                  <td key={col}>
                    <span className="cred-status-exhausted" title="此憑證條目配額已耗盡(HTTP 429)">
                      {cellText(entry[col])}
                    </span>
                  </td>
                ) : (
                  <td key={col}>{cellText(entry[col])}</td>
                ),
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// 「生效模型」三欄(模型軸)+ 交叉檢查燈——與下方憑證軸區塊刻意用不同的
// 標題措辭與視覺,避免兩條 provider 軸再度撞名(2026-08-04 需求核心)。
export function EffectiveModelBlock({ profile }: { profile: CredentialProfile }) {
  const check = profile.credential_model_consistency;
  const sourceText = MODEL_SOURCE_TEXT[profile.effective_model_source ?? "unknown"] ?? "無法查詢";
  return (
    <div className="cred-model-axis">
      <h3>生效模型設定(來源:config.yaml)</h3>
      <dl className="cred-model-facts">
        <div>
          <dt>{EFFECTIVE_PROVIDER_LABEL}</dt>
          <dd>{cellText(profile.effective_provider)}</dd>
        </div>
        <div>
          <dt>生效模型(model.default)</dt>
          <dd>{cellText(profile.effective_model)}</dd>
        </div>
        <div>
          <dt>設定來源</dt>
          <dd>{sourceText}</dd>
        </div>
      </dl>
      {check && (
        <p className={`cred-consistency cred-consistency-${check.light}`}>
          <span
            className="cred-consistency-dot"
            style={{ background: CONSISTENCY_COLORS[check.light] }}
            aria-hidden="true"
          />
          {check.text}
        </p>
      )}
    </div>
  );
}

export function ProfileCredentialBlock({ name, profile }: { name: string; profile: CredentialProfile }) {
  const pool = profile.credential_pool ?? {};
  const providerNames = Object.keys(pool);
  const check = profile.credential_model_consistency;
  return (
    <details className="cred-profile" open={name === "(global-root)"}>
      <summary>
        {check && (
          <span
            className="cred-consistency-dot"
            style={{ background: CONSISTENCY_COLORS[check.light] }}
            title={check.text}
            aria-hidden="true"
          />
        )}
        <b>{name}</b>
        {/* 摺疊狀態下也要看得到生效模型——2026-08-04 的誤解正是「改了全域
            model.provider,任何一頁都沒變化」,故摘要行就要照得到模型軸 */}
        <small className="cred-summary-model">
          {EFFECTIVE_PROVIDER_LABEL} {cellText(profile.effective_provider)} / {cellText(profile.effective_model)}
        </small>
        <small>
          {!profile.auth_json_exists
            ? "auth.json 不存在"
            : profile.error
              ? `錯誤:${profile.error}`
              : `${providerNames.length} 個${CREDENTIAL_PROVIDER_LABEL},mtime ${profile.mtime ?? "?"}`}
        </small>
      </summary>
      <div className="cred-profile-body">
        {/* 模型軸永遠顯示——即使 auth.json 不存在/壞掉,「現在設定用哪個
            模型」仍是可知且必須看得到的事實(這正是本次補洞的重點) */}
        <EffectiveModelBlock profile={profile} />
        {profile.auth_json_exists && !profile.error && (
          <>
            <p className="cred-providers">
              {CREDENTIAL_PROVIDER_LABEL}(此 store 存了哪些 provider 的憑證,僅名稱):
              {(profile.providers ?? []).join(", ") || "(無)"}
            </p>
            {providerNames.length === 0 && <InfoNotice message="credential_pool 為空。" />}
            {providerNames.map((provider) => (
              <div key={provider} className="cred-pool-block">
                <h3>
                  {CREDENTIAL_PROVIDER_LABEL}:{provider}
                  <small>{pool[provider].entry_count} 筆憑證 entry</small>
                </h3>
                <EntryTable entries={pool[provider].entries} />
              </div>
            ))}
          </>
        )}
      </div>
    </details>
  );
}

// 純渲染元件(props 注入資料):render 測試由這裡進入,斷言三層白名單的
// UI 層——即使餵入含秘密欄位的資料,渲染輸出也不含那些值。
export function CredentialsPage({
  lanes,
  credentials,
}: {
  lanes: CapabilityLane[];
  credentials: CredentialStatus;
}) {
  return (
    <div className="data-page">
      {/* §3.4:頁面最上方固定顯示、不可移除的警語 */}
      <WarnNotice message={CREDENTIAL_PAGE_WARNING} />

      <Panel
        title="Capability Lane 治理表"
        caption="registry/capability_lanes.yaml(已 commit 進 git 的公開治理資料,唯讀);「實際生效模型」欄來自各 profile/全域 config.yaml 的 model 設定值(非 registry 欄位,registry 的 model 刻意為 null)。"
      >
        <LaneTable lanes={lanes} />
      </Panel>

      <Panel
        title="Hermes 憑證治理狀態"
        caption={
          "每個 store 兩條軸並列:「生效模型設定」來自 config.yaml(現在設定用哪個 provider/model)," +
          "「憑證 provider」來自 auth.json(此 store 存了哪些 provider 的憑證)——兩者不是同一件事。" +
          "憑證欄位只取白名單(id/priority/last_status/last_refresh/source/label + entry 筆數),其餘在資料層即被捨棄;" +
          "交叉檢查燈號為唯讀告警,無任何修復入口。"
        }
      >
        {!credentials.available ? (
          <InfoNotice message={credentials.reason ?? "此環境無法查詢。"} />
        ) : (
          Object.entries(credentials.profiles).map(([name, profile]) => (
            <ProfileCredentialBlock key={name} name={name} profile={profile} />
          ))
        )}
      </Panel>
    </div>
  );
}

type CredentialsData = { lanes: CapabilityLane[]; credentials: CredentialStatus };

async function loadCredentials(): Promise<CredentialsData> {
  const [lanes, credentials] = await Promise.all([
    apiGet<CapabilityLane[]>("/api/capability-lanes"),
    apiGet<CredentialStatus>("/api/credential-status"),
  ]);
  return { lanes, credentials };
}

export default function CredentialsView() {
  const { data, error, loading, reload } = useApiData(loadCredentials, []);
  return (
    <div className="data-page">
      <div className="page-toolbar">
        <RefreshButton onClick={reload} loading={loading} />
      </div>
      {error && <ErrorNotice message={error} />}
      {data && <CredentialsPage lanes={data.lanes} credentials={data.credentials} />}
    </div>
  );
}
