// jobs-freshness-render.test.mjs 的測試進入點:把 jobs 管線新鮮度的純渲染
// 元件(props 注入,不經 fetch)接上 react-dom/server 的 renderToStaticMarkup,
// 供 node --test 在無瀏覽器環境下對「UI 渲染輸出」做五態與零操作入口斷言。
// 本檔只在測試中經 rolldown bundle 使用,不進 app bundle。
import { renderToStaticMarkup } from "react-dom/server";
import { JobsFreshnessCard } from "../../src/views/JobsFreshness";

export function renderFreshness(payload: unknown): string {
  return renderToStaticMarkup(<JobsFreshnessCard payload={payload as never} />);
}
