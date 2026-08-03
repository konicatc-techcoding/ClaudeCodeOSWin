// 唯讀 API client(P1,提案 §3.3 獨立資料層):
// UI 取數只有 fetch 唯讀 API 一條路——不裝任何 SQLite/檔案讀取依賴,
// 寫入能力在 UI 層技術上不存在。API base 寫死 127.0.0.1(localhost-only 鐵律)。
export const API_BASE_URL = "http://127.0.0.1:8799";

export const API_START_COMMAND = ".venv/Scripts/python.exe dashboard/api.py";

export type StatusCounts = Record<string, number>;

export type Job = {
  id: string;
  status: string;
  source: string;
  thread_id: string | null;
  attempts: number;
  max_attempts: number;
  cost_usd: number | null;
  created_at: string;
  completed_at: string | null;
} & Record<string, unknown>;

export type CostSummary = {
  total: number;
  avg: number;
  count: number;
  by_source: { source: string; total: number; n: number }[];
};

// /api/systemd-status:2026-07-28 起由 dashboard/data_systemd_wsl.py 供數
// (Windows 側經 `wsl -d Ubuntu` 的唯讀快照;distro 未運作絕不喚醒)。
// 三分支誠實狀態:"ok"=查得到(單元不在 units 裡才是真的「未安裝」);
// "wsl_down"=WSL distro 未運作(未探測);"unavailable"=無法查詢。
export type SystemdUnitInfo = { pid: string; last_exit: string; load?: string };

export type SystemdStatusPayload = {
  status: "ok" | "wsl_down" | "unavailable";
  status_text: string;
  reason: string | null;
  checked_at: string;
  distro: { name: string; running: boolean | null; detail: string };
  units: Record<string, SystemdUnitInfo> | null;
  timers: Record<string, { next: string; last: string }> | null;
};

export type InboxCounts = { pending: number; processed: number; failed: number };

export type Domain = { id?: string; status?: string } & Record<string, unknown>;

export type AdapterConfig = Record<string, Record<string, unknown>>;

export type Health = { ok: boolean; readonly: boolean; jobs_db_exists: boolean };

export type LogTail = { name: string; content: string };

// 與 dashboard/data.py JOB_STATUSES / app.py 的來源清單一致
export const JOB_STATUSES = ["queued", "running", "completed", "failed", "dead_letter"] as const;
export const JOB_SOURCES = ["telegram", "cron", "rss", "manual", "sat"] as const;
export const LOG_FILES = [
  "worker.log",
  "telegram_adapter.log",
  "cron_adapter.log",
  "rss_adapter.log",
] as const;

// --- P2:Stage 3 三項觀測功能(dashboard/data_stage3.py,全部唯讀)---

// capability_lanes.yaml 已 commit 進 git,全部欄位皆公開治理資料
export type CapabilityLane = {
  id?: string;
  capability?: string;
  execution?: string;
  provider?: string;
  model?: string | null;
  hermes_profile?: string;
  status?: string;
  cost_tier?: string;
  risk_tier?: string;
  allowed_agents?: string[];
  intended_use?: string;
  guardrails?: unknown;
  // 實際生效模型(資料層由 profile/全域 config.yaml 的 model 白名單值標注;
  // registry 的 model 欄位刻意為 null,不是生效值)
  effective_model?: string;
  effective_model_source?: "native" | "profile" | "global" | "unknown";
  effective_provider?: string | null;
} & Record<string, unknown>;

// 憑證 entry:只會有資料層白名單的六個欄位(stage3 提案 §3.2)
export type CredentialEntry = {
  id: string | null;
  priority: number | null;
  last_status: string | null;
  last_refresh: string | null;
  source: string | null;
  label: string | null;
};

export type CredentialProfile = {
  auth_json_exists: boolean;
  error?: string;
  mtime?: string | null;
  providers?: string[];
  credential_pool?: Record<string, { entry_count: number; entries: CredentialEntry[] }>;
};

export type CredentialStatus = {
  available: boolean;
  reason?: string;
  profiles: Record<string, CredentialProfile>;
};

export type ScheduleRow = {
  source: "systemd" | "hermes-native";
  job_name: string;
  schedule_expr: string;
  deployed: boolean | string;
  timer_active: string;
  last_result: string;
  next_trigger: string;
  last_trigger: string;
  model_drift: "aligned" | "DRIFTED" | "n/a";
  drift_cost_direction: string | null;
};

export type HermesSession = {
  session_id: string;
  session_source: string;
  title: string | null;
  model: string | null;
  started_at: string | null;
  ended_at: string | null;
  message_count: number | null;
};

// 與 HermesSessionAdapter 的 session_source 值域一致
export const SESSION_SOURCES = ["cli", "tui", "telegram", "cron"] as const;

// --- 背景常駐狀態燈號(docs/webui-service-control-proposal.md §1,唯讀)---
// 對應 dashboard/data_resident.py::get_resident_status() 的回傳結構。

export type ResidentLight = "green" | "yellow" | "orange" | "red" | "gray";

export type ResidentUnitState = {
  state: string;
  sub: string;
  load?: string;
  resident?: boolean;
};

export type ResidentStatusPayload = {
  light: ResidentLight;
  text: string;
  detail: string;
  checked_at: string;
  distro: { name: string; running: boolean | null; detail: string };
  // units/gateway 為 null = 該層未探測(前一層未通過即止步,探測零副作用)
  units: Record<string, ResidentUnitState> | null;
  resident_units: string[];
  gateway: { ready: boolean; state: string | null; detail: string; mtime?: string | null } | null;
};

// --- Hermes 更新唯讀升級預檢(docs/webui-update-button-proposal.md §3,階段一)---
// 對應 dashboard/data_update.py::get_update_precheck()。**純唯讀**:UI 只顯示
// 預檢結果,零執行/升級/同步按鈕(階段二寫入未核准);唯一允許的操作是
// 「重新整理(重跑唯讀預檢)」的讀取鈕。此邊界由 update-precheck-render.test.mjs
// 與 scripts/webui_security_check.py 第 11 項靜態鎖定。

export type UpdateLight = "green" | "blue" | "orange" | "red" | "gray";

export type UpdateTargetService = {
  kind?: string;
  detail?: string;
  ready?: boolean | null;
  state?: string | null;
  units?: Record<string, ResidentUnitState> | null;
  resident_units?: string[];
} & Record<string, unknown>;

export type UpdateRescueRef = { name: string; object: string; type: string };

// 比較基準角色:upstream=官方上游(有無新版可吸收)、backup=私有備份/防重演
// 基準(本機與雲端是否同步)、follow=應跟隨的權威基準(Windows 整合 tip;
// 2026-08-03 新增,提案 §10.1)、peer=其他基準(僅供參考,不計入整體燈)。
// 角色由資料層**依 remote URL** 判定,不依 remote 名稱——兩端 remote 命名不同;
// follow 的判準是「路徑正規化後等於 Windows hermes-agent repo」,不是「本機路徑」。
// **follow 的燈號語意與 upstream 相反**:落後 = 該同步了(藍,資訊態);
// 領先/分歧 = 異常(橙)——WSL 理論上不該有 Windows 沒有的 commit。
// 計入整體燈與否一律以後端的 counts_toward_overall 欄位為準(UI 不依 role
// 自行判斷):peer 永不計入;**follow 存在的 target 上 upstream 也降為資訊性**
// (2026-08-03 拍板,選項 b——該端語意是跟隨者,對官方落後是預期常態),
// 此時整體燈由 follow 組獨力驅動;無 follow 的 target(Windows)行為不變。
export type UpdateRole = "upstream" | "backup" | "follow" | "peer";

export type UpdateComparison = {
  remote: string;
  url: string | null;
  role: UpdateRole;
  role_label: string;
  ref: string;
  tip: string | null;
  applicable: boolean;
  counts_toward_overall: boolean;
  behind: number | null;
  ahead: number | null;
  can_ff: boolean | null;
  diverged: boolean | null;
  diverge_commits: string[];
  // 本地歷史與該 remote 的共同祖先(live 版本字串的 `upstream <sha>` 來源)
  merge_base?: string | null;
  light: UpdateLight;
  light_text: string;
  summary: string;
} & Record<string, unknown>;

// live 版本字串(提案 §3.1 第一項)。資料層取自 **HEAD 的 pyproject.toml blob**
// (`git show`,唯讀凍結字面)+ merge-base + 既有 ahead 數——**不執行 hermes CLI**
// (那會 spawn process 並可能寫 ~/.hermes/.update_check),故取數零副作用。
export type UpdateLiveVersion = {
  package: string | null;
  upstream_base: string | null;
  local: string | null;
  carried: number | null;
  // 組好的字串,如 "v0.19.0 upstream 3910ab28 + local 97011887 (+12 carried commits)"
  text: string | null;
  source?: string;
} & Record<string, unknown>;

export type UpdateTarget = {
  id: string;
  label: string;
  runner?: string;
  light: UpdateLight;
  light_text: string;
  advice: string;
  blocking_reasons: string[];
  // 整體燈由哪一組造成:"upstream" / "backup" / "target"(目標端層級的紅)
  overall_driver?: string | null;
  queryable: boolean;
  repo?: string;
  head?: { short: string | null; describe: string | null; branch: string | null } | null;
  live_version?: UpdateLiveVersion | null;
  dirty?: boolean | null;
  remotes?: string[];
  comparisons?: UpdateComparison[];
  rescue_refs?: UpdateRescueRef[];
  rescue_count?: number;
  expect_custom?: boolean;
  expect_rescue?: boolean;
  service?: UpdateTargetService | null;
} & Record<string, unknown>;

export type UpdatePrecheckPayload = {
  checked_at: string;
  remote_note: string;
  stage: string;
  targets: UpdateTarget[];
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`);
  } catch {
    throw new ApiError(
      0,
      `無法連線唯讀 API(${API_BASE_URL})。請先在 repo 根目錄啟動:${API_START_COMMAND}`,
    );
  }
  if (!response.ok) {
    let message = `API 錯誤(HTTP ${response.status})`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body?.error) message = body.error;
    } catch {
      // 非 JSON 錯誤內容,保留預設訊息
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}
