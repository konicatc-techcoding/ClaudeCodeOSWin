// update-precheck-render.test.mjs 的測試進入點:把升級預檢的純渲染元件
// (props 注入,不經 fetch)接上 react-dom/server 的 renderToStaticMarkup,
// 供 node --test 在無瀏覽器環境下對「UI 渲染輸出」做五態與零執行按鈕斷言
// (docs/webui-update-button-proposal.md §3.2)。
// 本檔只在測試中經 rolldown bundle 使用,不進 app bundle。
import { renderToStaticMarkup } from "react-dom/server";
import { RepoGuardPanel, UpdatePrecheckCards, UpdateTargetCard } from "../../src/views/UpdatePrecheck";

export function renderCards(payload: unknown): string {
  return renderToStaticMarkup(<UpdatePrecheckCards payload={payload as never} />);
}

export function renderCard(target: unknown): string {
  return renderToStaticMarkup(<UpdateTargetCard target={target as never} />);
}

// 未推送 commit 的離線保險快照面板(批次 1 止血)——同樣是 props 注入的純渲染元件。
export function renderGuard(guard: unknown): string {
  return renderToStaticMarkup(<RepoGuardPanel guard={guard as never} />);
}
