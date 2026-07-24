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

export type SystemdStatus = Record<string, { pid: string; last_exit: string; load?: string }>;

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
