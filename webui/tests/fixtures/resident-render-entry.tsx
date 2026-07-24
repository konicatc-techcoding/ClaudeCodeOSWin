// resident-render.test.mjs 的測試進入點:把燈號的純渲染元件(props 注入,
// 不經 fetch)接上 react-dom/server 的 renderToStaticMarkup,供 node --test
// 在無瀏覽器環境下對「UI 渲染輸出」做五態與零按鈕斷言
// (docs/webui-service-control-proposal.md §1.2/§1.3)。
// 本檔只在測試中經 rolldown bundle 使用,不進 app bundle。
import { renderToStaticMarkup } from "react-dom/server";
import {
  ResidentStatusBadge,
  buildResidentTooltip,
  RESIDENT_POLL_INTERVAL_MS,
} from "../../src/ResidentStatus";

export { buildResidentTooltip, RESIDENT_POLL_INTERVAL_MS };

export function renderBadge(status: unknown): string {
  return renderToStaticMarkup(<ResidentStatusBadge status={status as never} />);
}
