// service-unit-light.test.mjs 的測試進入點:把個別服務小燈號的純渲染元件
// (props 注入,不經 fetch)與狀態映射函式接上 react-dom/server 的
// renderToStaticMarkup,供 node --test 在無瀏覽器環境下對「UI 渲染輸出」
// 做狀態映射各分支斷言(2026-07-27 個別燈號拍板)。
// 本檔只在測試中經 rolldown bundle 使用,不進 app bundle。
import { renderToStaticMarkup } from "react-dom/server";
import { ServiceUnitLight, buildUnitLight } from "../../src/ResidentStatus";

export { buildUnitLight };

export function renderUnitLight(status: unknown, unit: string): string {
  return renderToStaticMarkup(<ServiceUnitLight status={status as never} unit={unit} />);
}
