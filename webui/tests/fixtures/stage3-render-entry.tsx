// stage3-render.test.mjs 的測試進入點:把三個 P2 純渲染元件(props 注入,
// 不經 fetch)接上 react-dom/server 的 renderToStaticMarkup,供 node --test
// 在無瀏覽器環境下取得「UI 渲染輸出」做三層假密鑰斷言(webui-migration
// 提案 §4.3 P2 DoD 第 2 條;比照範本 tests/rendered-html.test.mjs 的
// rendered-HTML 測試形態,改為元件測試)。
// 本檔只在測試中經 esbuild bundle 使用,不進 app bundle。
import { renderToStaticMarkup } from "react-dom/server";
import {
  CredentialsPage,
  CREDENTIAL_PAGE_WARNING,
  CREDENTIAL_PROVIDER_LABEL,
  EFFECTIVE_PROVIDER_LABEL,
} from "../../src/views/Credentials";
import { SystemdMetricRow } from "../../src/views/Overview";
import { ScheduleTable } from "../../src/views/Schedule";
import { SessionsTable } from "../../src/views/Sessions";

export const WARNING_TEXT = CREDENTIAL_PAGE_WARNING;
// 兩條 provider 軸的欄名(2026-08-04 正名):測試由此比對,避免測試自己
// 硬編中文字串而與元件實際欄名脫鉤。
export const CREDENTIAL_PROVIDER_LABEL_TEXT = CREDENTIAL_PROVIDER_LABEL;
export const EFFECTIVE_PROVIDER_LABEL_TEXT = EFFECTIVE_PROVIDER_LABEL;

export function renderCredentials(lanes: unknown, credentials: unknown): string {
  return renderToStaticMarkup(
    <CredentialsPage lanes={lanes as never} credentials={credentials as never} />,
  );
}

export function renderSchedule(rows: unknown): string {
  return renderToStaticMarkup(<ScheduleTable rows={rows as never} />);
}

export function renderSessions(sessions: unknown): string {
  return renderToStaticMarkup(<SessionsTable sessions={sessions as never} />);
}

// 總覽「Worker / Adapter 狀態」卡片(2026-07-28:systemd 狀態改經 WSL 查詢,
// 三分支誠實狀態文字——測試斷言「查不到」不再偽裝成「未安裝」)。
export function renderSystemdMetrics(systemd: unknown): string {
  return renderToStaticMarkup(<SystemdMetricRow systemd={systemd as never} />);
}
