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
