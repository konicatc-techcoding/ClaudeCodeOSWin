// 〔重新整理遠端資訊〕fetch — bridge 白名單第三群組測試(2026-08-04 拍板,
// docs/webui-update-button-proposal.md §9 待拍板項 2;第四個寫入例外)。
// 全部使用 FAKE_GIT_FETCH fixture——**絕不對真實 repo fetch、絕不觸網**。
// 核心鎖定:
//  - 靜態:FETCH_COMMANDS 凍結且恰為拍板四條(順序、repo、remote);
//    **無 --prune**、無 pull/merge/checkout/reset(純加法,不碰工作樹)。
//  - 行為:四條全成 → ok:true + 四筆結果;單條失敗 → per-remote fail-loud
//    (該條帶 exitCode/錯誤,其餘照跑不中止);併發 → 409;
//    body/query 傳參技術上進不來(bridge 不讀 body、route 全字串比對);
//    audit 每條一筆。
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  createBridge,
  AUDIT_LOG_NAME,
  FETCH_COMMANDS,
  FETCH_ROUTE,
  FETCH_TIMEOUT_MS,
  WINDOWS_HERMES_REPO,
  WSL_HERMES_REPO,
} from "../scripts/bridge.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const fakeFetch = resolve(here, "fixtures", "fake-git-fetch.mjs");

const TEST_BRIDGE_PORT = 8793;
const BRIDGE_URL = `http://127.0.0.1:${TEST_BRIDGE_PORT}`;

// 測試注入:四條指令的「形狀」與 production 相同(id/label/bin/args),
// 只是 bin 換成 node fixture;args 帶上 production 的 marker 供失敗注入。
function fakeCommands() {
  return [
    { id: "windows:upstream", label: "Windows ← 官方 upstream", bin: process.execPath, args: [fakeFetch, "win-repo", "fetch", "upstream-win"] },
    { id: "windows:origin", label: "Windows ← 私有備份 origin", bin: process.execPath, args: [fakeFetch, "win-repo", "fetch", "origin-win"] },
    { id: "wsl:origin", label: "WSL ← Windows 整合 tip(origin,本機路徑)", bin: process.execPath, args: [fakeFetch, "wsl-repo", "fetch", "origin-wsl"] },
    { id: "wsl:upstream", label: "WSL ← 官方 upstream", bin: process.execPath, args: [fakeFetch, "wsl-repo", "fetch", "upstream-wsl"] },
  ];
}

function makeBridge(dir, fetchLogPath) {
  process.env.FAKE_FETCH_LOG = fetchLogPath;
  return createBridge({
    bridgePort: TEST_BRIDGE_PORT,
    logDir: dir,
    fetchCommands: fakeCommands(),
    fetchTimeoutMs: 10000,
  });
}

function readFetchLog(fetchLogPath) {
  if (!existsSync(fetchLogPath)) return [];
  return readFileSync(fetchLogPath, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

// ---------------------------------------------------------------------------
// 靜態:production 凍結常數與原始碼鎖定(不執行任何指令)
// ---------------------------------------------------------------------------

test("production FETCH_COMMANDS 凍結且恰為拍板四條(順序/repo/remote 逐字)", () => {
  assert.ok(Object.isFrozen(FETCH_COMMANDS));
  assert.equal(FETCH_COMMANDS.length, 4, "恰四條,一條不多一條不少");
  const shapes = FETCH_COMMANDS.map((c) => ({ id: c.id, bin: c.bin, args: [...c.args] }));
  assert.deepEqual(shapes, [
    { id: "windows:upstream", bin: "git", args: ["-C", WINDOWS_HERMES_REPO, "fetch", "upstream"] },
    { id: "windows:origin", bin: "git", args: ["-C", WINDOWS_HERMES_REPO, "fetch", "origin"] },
    { id: "wsl:origin", bin: "wsl.exe", args: ["-d", "Ubuntu", "--exec", "git", "-C", WSL_HERMES_REPO, "fetch", "origin"] },
    { id: "wsl:upstream", bin: "wsl.exe", args: ["-d", "Ubuntu", "--exec", "git", "-C", WSL_HERMES_REPO, "fetch", "upstream"] },
  ]);
  for (const command of FETCH_COMMANDS) {
    assert.ok(Object.isFrozen(command) && Object.isFrozen(command.args), `${command.id} 須凍結`);
  }
  assert.equal(FETCH_ROUTE, "/api/repo/fetch-remotes");
  assert.equal(FETCH_TIMEOUT_MS, 60000, "每條 timeout 拍板值 60 秒");
});

test("禁止事項靜態鎖定:指令集無 --prune;args 無任何寫工作樹子指令", () => {
  for (const command of FETCH_COMMANDS) {
    const args = [...command.args];
    assert.ok(!args.includes("--prune"), `${command.id} 不得帶 --prune(純加法,絕不刪 refs)`);
    for (const forbidden of ["pull", "merge", "checkout", "reset", "rebase", "clean", "--force", "--mirror"]) {
      assert.ok(!args.includes(forbidden), `${command.id} 不得含 ${forbidden}`);
    }
    // 每條的 git 子指令必須就是 fetch
    assert.ok(args.includes("fetch"), `${command.id} 必須是 fetch`);
  }
});

test("bridge 原始碼:fetch execFile 為固定字面;無 --prune 等禁區字面", async () => {
  const source = await readFile(join(root, "scripts", "bridge.mjs"), "utf8");
  assert.ok(
    source.includes("execFile(command.bin, [...command.args], { timeout: fetchTimeoutMs }"),
    "fetch 的 execFile 必須是「凍結指令 + 展開 args」固定字面",
  );
  const codeOnly = source.replace(/(?<!:)\/\/.*$/gm, "");
  for (const forbidden of ["--prune", '"pull"', '"merge"', '"checkout"', '"reset"', "--force", "--mirror"]) {
    assert.ok(!codeOnly.includes(forbidden), `bridge 程式碼不得含 fetch 禁區字面: ${forbidden}`);
  }
});

// ---------------------------------------------------------------------------
// HTTP 行為(FAKE fixture,絕不碰真實 git/repo)
// ---------------------------------------------------------------------------

test("fetch-remotes HTTP 行為(四條全成/部分失敗/傳參被拒/併發/audit)", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "webui-fetch-test-"));
  const fetchLogPath = join(dir, "fetch.log");
  const bridge = makeBridge(dir, fetchLogPath);
  await bridge.listen();

  t.after(async () => {
    await bridge.shutdown();
    rmSync(dir, { recursive: true, force: true });
    delete process.env.FAKE_FETCH_LOG;
    delete process.env.FAKE_FETCH_FAIL;
    delete process.env.FAKE_FETCH_DELAY_MS;
  });

  await t.test("四條全成:ok:true,results 恰四筆且順序=拍板順序", async () => {
    const r = await fetch(`${BRIDGE_URL}${FETCH_ROUTE}`, { method: "POST" });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.equal(body.ok, true);
    assert.deepEqual(body.results.map((x) => x.id),
      ["windows:upstream", "windows:origin", "wsl:origin", "wsl:upstream"]);
    assert.ok(body.results.every((x) => x.ok && x.exitCode === 0 && x.error === null));
    assert.equal(readFetchLog(fetchLogPath).length, 4, "恰執行四條指令");
  });

  await t.test("request body 完全被忽略:注入指令進不到 fixture argv", async () => {
    const before = readFetchLog(fetchLogPath).length;
    const r = await fetch(`${BRIDGE_URL}${FETCH_ROUTE}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commands: [{ bin: "calc.exe" }], remote: "evil", prune: true }),
    });
    assert.equal(r.status, 200);
    const spawned = readFetchLog(fetchLogPath).slice(before);
    assert.equal(spawned.length, 4);
    for (const entry of spawned) {
      assert.ok(!JSON.stringify(entry).includes("evil"), "body 內容不得進入 argv");
      assert.ok(!JSON.stringify(entry).includes("prune"), "body 內容不得進入 argv");
    }
  });

  await t.test("UI 傳參被拒:帶 query string / 子路徑 / 錯誤方法 → 404,零執行", async () => {
    const before = readFetchLog(fetchLogPath).length;
    for (const attempt of [
      { url: `${BRIDGE_URL}${FETCH_ROUTE}?remote=evil`, method: "POST" },
      { url: `${BRIDGE_URL}${FETCH_ROUTE}?prune=1`, method: "POST" },
      { url: `${BRIDGE_URL}${FETCH_ROUTE}/extra`, method: "POST" },
      { url: `${BRIDGE_URL}${FETCH_ROUTE}`, method: "GET" },
      { url: `${BRIDGE_URL}${FETCH_ROUTE}`, method: "DELETE" },
    ]) {
      const r = await fetch(attempt.url, { method: attempt.method });
      assert.equal(r.status, 404, `${attempt.method} ${attempt.url} 應 404(route 全字串比對)`);
    }
    assert.equal(readFetchLog(fetchLogPath).length, before, "被拒請求不得執行任何指令");
  });

  await t.test("per-remote fail-loud:一條失敗其餘照跑,各自回報成敗", async () => {
    const before = readFetchLog(fetchLogPath).length;
    process.env.FAKE_FETCH_FAIL = "upstream-win"; // 只讓 windows:upstream 失敗
    try {
      const r = await fetch(`${BRIDGE_URL}${FETCH_ROUTE}`, { method: "POST" });
      const body = await r.json();
      assert.equal(r.status, 200, "部分失敗仍 200——成敗在 results 逐條呈現");
      assert.equal(body.ok, false, "整體 ok 須反映有失敗");
      const byId = Object.fromEntries(body.results.map((x) => [x.id, x]));
      assert.equal(byId["windows:upstream"].ok, false);
      assert.equal(byId["windows:upstream"].exitCode, 128);
      assert.match(byId["windows:upstream"].error, /Could not resolve host/, "失敗細節須帶出 stderr 尾段");
      for (const okId of ["windows:origin", "wsl:origin", "wsl:upstream"]) {
        assert.equal(byId[okId].ok, true, `${okId} 不得被前面的失敗中止`);
      }
      assert.equal(readFetchLog(fetchLogPath).length - before, 4, "失敗後其餘三條仍執行(不中止)");
    } finally {
      delete process.env.FAKE_FETCH_FAIL;
    }
  });

  await t.test("併發防護:一輪進行中再按 → 409,不多跑任何指令", async () => {
    const before = readFetchLog(fetchLogPath).length;
    process.env.FAKE_FETCH_DELAY_MS = "800"; // 拉長每條執行時間,製造窗口
    try {
      const [first, second] = await Promise.all([
        fetch(`${BRIDGE_URL}${FETCH_ROUTE}`, { method: "POST" }),
        (async () => {
          await new Promise((r) => setTimeout(r, 200)); // 確保第一輪已 in-flight
          return fetch(`${BRIDGE_URL}${FETCH_ROUTE}`, { method: "POST" });
        })(),
      ]);
      const statuses = [first.status, second.status].sort();
      assert.deepEqual(statuses, [200, 409], "同時只允許一輪(第二發 409)");
      assert.equal(readFetchLog(fetchLogPath).length - before, 4, "409 那發零執行");
    } finally {
      delete process.env.FAKE_FETCH_DELAY_MS;
    }
  });

  await t.test("audit:每條指令一筆(含成敗)+ 併發拒絕一筆,沿用同一份 log", async () => {
    const auditPath = join(dir, AUDIT_LOG_NAME);
    assert.ok(existsSync(auditPath));
    const text = readFileSync(auditPath, "utf8");
    assert.match(text, /\| fetch:windows:upstream \| 成功 exit=0/);
    assert.match(text, /\| fetch:windows:upstream \| 失敗 exit=128/);
    assert.match(text, /\| fetch:wsl:upstream \| 成功 exit=0/);
    assert.match(text, /\| fetch:- \| 拒絕\(已有一輪 fetch 進行中\)/);
  });
});

// ---------------------------------------------------------------------------
// UI 元件靜態鎖定(寫入面隔離;渲染層零其他入口)
// ---------------------------------------------------------------------------

test("UpdateFetch.tsx:URL 凍結字面、POST 零 body、防連點、群組互不滲透", async () => {
  const source = await readFile(join(root, "src", "UpdateFetch.tsx"), "utf8");
  assert.ok(source.includes('"http://127.0.0.1:8787"'), "bridge URL 為凍結字面");
  assert.ok(source.includes('"/api/repo/fetch-remotes"'), "route 為凍結字面");
  assert.ok(source.includes("`${BRIDGE_URL}${FETCH_REMOTES_ROUTE}`"), "請求 URL 僅由兩個凍結字面組出");
  assert.ok(!source.includes("body:"), "POST 不得帶 body(零參數)");
  assert.ok(source.includes("disabled={busy}") && source.includes("if (busy) return"),
    "執行中防連點(disabled + handler 提前返回)");
  assert.ok(source.includes("onCompleted()"), "完成後須通知外層以 fresh 重查預檢");
  assert.ok(!source.includes("/api/hermes/") && !source.includes("/api/service/"),
    "不得引用第一/第二群組端點(群組互不滲透)");
  for (const forbidden of ["--prune", "hermes update", "--ff-only"]) {
    assert.ok(!source.includes(forbidden), `UI 不得出現 ${forbidden}`);
  }
});
