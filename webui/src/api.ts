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

// 「憑證 × 模型」交叉一致性檢查(唯讀告警;判定在 dashboard/data_stage3.py
// _credential_model_consistency() 完成,前端只渲染,不重算規則)
// (嚴重度 orange > yellow > green;yellow = 本來判綠但本 store 有配額耗盡
// 條目的暫時狀態,2026-08-05 起)
export type CredentialConsistency = {
  light: "green" | "yellow" | "orange" | "gray";
  text: string;
  effective_provider: string | null;
  entry_count: number | null;
  exhausted_entry_count: number;
};

export type CredentialProfile = {
  auth_json_exists: boolean;
  error?: string;
  mtime?: string | null;
  // 憑證軸:此 store 的 auth.json **存了哪些 provider 的憑證**(僅名稱)。
  // ⚠ 與下面的 effective_provider(模型軸)是兩件不同的事,不可混為一談。
  providers?: string[];
  credential_pool?: Record<string, { entry_count: number; entries: CredentialEntry[] }>;
  // 模型軸(第三條軸,2026-08-04 補):此 store **現在設定用哪個 provider /
  // 哪個 model.default**,來源是 config.yaml(profile 自己的或全域的)。
  effective_provider?: string | null;
  effective_model?: string;
  effective_model_source?: "profile" | "global" | "unknown";
  credential_model_consistency?: CredentialConsistency;
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
  // gateway pid 活性驗證(2026-08-04 事故修正:狀態檔宣稱 running 但 pid 已死
  // 曾誤報就緒一天半)。dead=true = pid 已死/被重用(資料層轉紅);
  // pid_alive: true=驗證過活/null=無法驗證(fail-open,照舊)/false=死。
  gateway: {
    ready: boolean;
    state: string | null;
    detail: string;
    mtime?: string | null;
    dead?: boolean;
    pid?: number | null;
    pid_alive?: boolean | null;
    pid_note?: string;
  } | null;
};

// --- Hermes 更新升級預檢(docs/webui-update-button-proposal.md §3,階段一)---
// 對應 dashboard/data_update.py::get_update_precheck()。**取數純唯讀**
// (GET-only,可帶 fresh=1 繞過快取);UI 零升級/合併/同步執行鈕(階段二
// 寫入未核准)。**唯一寫入例外**:〔重新整理遠端資訊〕fetch 鈕(2026-08-04
// 拍板,隔離於 UpdateFetch.tsx + bridge 第三群組)——只更新 remote-tracking
// refs,不碰工作樹。此邊界由 update-precheck-render.test.mjs 與
// scripts/webui_security_check.py 第 11/13 項靜態鎖定。

export type UpdateLight = "green" | "blue" | "orange" | "red" | "gray";

export type UpdateTargetService = {
  kind?: string;
  detail?: string;
  ready?: boolean | null;
  state?: string | null;
  // gateway pid 活性驗證(2026-08-04;Windows 端 service 欄與常駐燈共用
  // data_resident 同一份 helper):dead=true = 狀態檔宣稱 running 但 pid
  // 已死/被重用,detail 帶誠實說明(含狀態檔停更時間)。
  dead?: boolean | null;
  pid?: number | null;
  pid_alive?: boolean | null;
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

// 未推送 commit 的離線保險快照(批次 1 止血;資料層 dashboard/data_repo_guard.py)。
// **語意不等於「目前有沒有未推送 commit」**——那是各端 backup 組的 ahead(橙燈)。
// 本組回答的是「萬一被 Install 鈕 reset --hard 吃掉,救不救得回來」,資料來自
// 上一次 guard 執行留下的 manifest,故一律附帶「這份快照有多舊」。
// 燈只有 green(新鮮)/yellow(過期)/gray(從未執行或無法查詢)——**刻意不用 orange**,
// 橙保留給預檢的「未 push」,兩者不搶同一顆燈色。
export type RepoGuardLight = "green" | "yellow" | "gray";

export type RepoGuardTarget = {
  id: string;
  label: string;
  status: "fresh" | "stale" | "never" | "error";
  light: RepoGuardLight;
  light_text: string;
  summary: string;
  created_at: string | null;
  age_hours: number | null;
  age_text: string | null;
  bundle: string | null;
  bundle_bytes: number | null;
  // 「當時保全的 commit 數」——不是目前暴露數(manifest 只在有暴露時才寫出)
  covered_commits: number | null;
  covered_refs: string[];
  dirty_files: number | null;
} & Record<string, unknown>;

export type RepoGuardStatus = {
  checked_at: string;
  store_root: string | null;
  fresh_hours: number;
  scheduled: boolean;
  note: string;
  overall_light: RepoGuardLight;
  targets: RepoGuardTarget[];
} & Record<string, unknown>;

export type UpdatePrecheckPayload = {
  checked_at: string;
  remote_note: string;
  stage: string;
  targets: UpdateTarget[];
  // 同一個唯讀端點附帶回來(不另開取數 URL);後端讀不到時整塊可能不存在
  repo_guard?: RepoGuardStatus | null;
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

// --- jobs 管線「新鮮度」燈號(2026-09-04;資料層 dashboard/data_jobs_freshness.py)---
// 對應 GET /api/jobs-freshness。判準與門檻與 Slack 看門狗
// (scripts/jobs_freshness_watchdog.py)共用同一個 core 模組與同一份
// registry/jobs_watchdog.yaml——UI 與告警不會各判各的。
// 端點唯讀:不送任何 Slack、不寫任何東西、不觸發任何 job。
//
// ⚠️ **資料年齡**(2026-09-04 拓撲修正):runtime jobs.db 只存在 WSL,而唯讀 API
// 跑在 Windows,所以這裡讀的是 WSL 定期推來的**快照**。payload 的 data_* 欄位
// 就是「這份資料多舊」,**呈現層必須顯示**——絕不能讓人以為看到的是當下狀態。
// 快照偏舊時後端會把綠燈降黃、過期時整體轉灰(前端不重算這條規則)。
//
// 燈色語意(後端 STATE_LIGHTS 決定,前端只渲染,不重算規則):
//   trigger_dead / executor_dead → orange(最高嚴重度)
//   executor_degraded            → yellow
//   healthy                      → green
//   inconclusive                 → gray(**正常的「還在跑」,刻意不是警示色**)
//   端點 unavailable             → gray + reason(灰 ≠ 沒事)
// 不使用 red:紅在本系統既有語意是常駐/服務層級的「不可用」(ResidentLight /
// UpdateLight),管線新鮮度不搶那顆燈。
export type FreshnessLight = "green" | "yellow" | "orange" | "gray";

export type FreshnessState =
  | "healthy"
  | "inconclusive"
  | "trigger_dead"
  | "executor_dead"
  | "executor_degraded";

export type FreshnessSource = {
  source: string;
  description: string | null;
  state: FreshnessState | string;
  state_label: string;
  state_short: string;
  light: FreshnessLight;
  // 是否屬於「看門狗會告警」的狀態——inconclusive/healthy 皆為 false
  alerting: boolean;
  reason: string;
  expect_enqueue: boolean;
  lookback_hours: number;
  enqueued: number;
  completed: number;
  dead_letter: number;
  stuck: number;
  last_completed_at: string | null;
  last_completed_age_hours: number | null;
  last_completed_age_text: string;
  // 資料年齡造成的降級(後端決定):light_before_data_age 保留原本的燈色,
  // 讓「為什麼變黃/變灰」可被追溯;data_stale=true 代表這一列的結論算自舊快照。
  light_before_data_age?: FreshnessLight | null;
  data_stale?: boolean;
} & Record<string, unknown>;

export type FreshnessThresholds = {
  lookback_hours: number;
  min_expected_enqueued: number;
  stuck_backlog_hours: number;
  dead_letter_ratio_threshold: number;
  min_terminal_sample: number;
};

// 資料來源與年齡(dashboard/data_jobs_snapshot.py 判定;Jobs/成本/新鮮度共用)。
export type JobsDataSourceKind = "runtime" | "snapshot" | "missing";
export type JobsDataStatus = "live" | "fresh" | "stale" | "expired" | "never" | "error";

export type JobsDataAge = {
  data_source?: JobsDataSourceKind;
  data_status?: JobsDataStatus;
  data_captured_at?: string | null;
  data_age_hours?: number | null;
  data_age_text?: string | null;
  data_age_label?: string;
  data_trusted?: boolean;
  data_summary?: string;
  data_note?: string;
  data_fresh_hours?: number;
  data_expire_hours?: number;
  data_snapshot_dir?: string | null;
  data_jobs_count?: number | null;
};

export type JobsFreshnessPayload = JobsDataAge & {
  checked_at: string;
  status: "ok" | "unavailable";
  available: boolean;
  reason: string | null;
  note: string;
  config_path: string;
  jobs_db: string;
  overall_light: FreshnessLight;
  overall_text: string;
  summary: string;
  thresholds: FreshnessThresholds | null;
  alerting_states: string[];
  sources: FreshnessSource[];
} & Record<string, unknown>;
