// jobs-data-age-render.test.mjs 的測試進入點:把「資料年齡」橫幅與新鮮度卡片
// 的純渲染元件(props 注入,不經 fetch)接上 react-dom/server,供 node --test
// 在無瀏覽器環境下斷言「使用者一定看得到資料多舊」。
// 本檔只在測試中經 rolldown bundle 使用,不進 app bundle。
import { renderToStaticMarkup } from "react-dom/server";
import { DataAgeBanner } from "../../src/views/JobsDataAge";
import { JobsFreshnessCard } from "../../src/views/JobsFreshness";

export function renderDataAge(data: unknown): string {
  return renderToStaticMarkup(<DataAgeBanner data={data as never} />);
}

export function renderFreshness(payload: unknown): string {
  return renderToStaticMarkup(<JobsFreshnessCard payload={payload as never} />);
}
