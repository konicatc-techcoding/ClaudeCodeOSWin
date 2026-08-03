// 白名單第二群組(WSL systemd 服務控制)安全規格測試——
// docs/webui-service-control-proposal.md v1.1 §2.2 逐條鎖定:
// 兩群組白名單枚舉完整性(防互相滲透)、指令固定模板不可參數化、
// 非白名單一律 400+audit、重複操作 409、audit 格式(時間/單元/動詞/exit code)、
// UI 端枚舉一致+二次確認+stop 語意明示+bridge 離線不做假按鈕。
// 全部使用 FAKE_WSL fixture——絕不對真實 systemd 單元執行任何操作。
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  createBridge,
  FIXED_COMMAND,
  SERVICE_UNIT_WHITELIST,
  SERVICE_OP_WHITELIST,
  SERVICE_COMMAND,
  SERVICE_ROUTES,
  SERVICE_ROUTE_PREFIX,
  AUDIT_LOG_NAME,
} from "../scripts/bridge.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const fakeWsl = resolve(here, "fixtures", "fake-wsl.mjs");

const TEST_BRIDGE_PORT = 8791; // 與 bridge.test.mjs(8790)分開
const BRIDGE_URL = `http://127.0.0.1:${TEST_BRIDGE_PORT}`;
// dashboard 探測指向必然離線的 port——本測試不涉及第一群組操作
const TEST_DASHBOARD_URL = "http://127.0.0.1:9399";

const EXPECTED_UNITS = ["hermes-worker.service", "hermes-telegram.service"];
const EXPECTED_OPS = ["start", "stop", "restart"];

function makeTempDir() {
  return mkdtempSync(join(tmpdir(), "webui-service-test-"));
}

function makeBridge(logDir, spawnLogPath) {
  process.env.FAKE_WSL_SPAWN_LOG = spawnLogPath;
  return createBridge({
    dashboardUrl: TEST_DASHBOARD_URL,
    bridgePort: TEST_BRIDGE_PORT,
    logDir,
    // 測試注入假 wsl:形狀與 SERVICE_COMMAND 相同(bin + 凍結前綴 args),
    // fixture 收到的 argv 即模板附加的 [op, unit] 兩個枚舉值
    serviceCommand: { bin: process.execPath, args: [fakeWsl] },
    serviceTimeoutMs: 15000,
  });
}

function readSpawnLog(spawnLogPath) {
  if (!existsSync(spawnLogPath)) return [];
  return readFileSync(spawnLogPath, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

// ---------------------------------------------------------------------------
// 白名單枚舉完整性(兩群組各自窮舉,防互相滲透)
// ---------------------------------------------------------------------------

test("第二群組枚舉:單元恰為兩個常駐 service(不含 timer),動詞恰為三種,皆凍結", () => {
  assert.deepEqual([...SERVICE_UNIT_WHITELIST], EXPECTED_UNITS);
  assert.deepEqual([...SERVICE_OP_WHITELIST], EXPECTED_OPS);
  assert.ok(Object.isFrozen(SERVICE_UNIT_WHITELIST));
  assert.ok(Object.isFrozen(SERVICE_OP_WHITELIST));
  // 指令固定模板:wsl -d Ubuntu systemctl --user(凍結,不可參數化)
  assert.equal(SERVICE_COMMAND.bin, "wsl.exe");
  assert.deepEqual([...SERVICE_COMMAND.args], ["-d", "Ubuntu", "systemctl", "--user"]);
  assert.ok(Object.isFrozen(SERVICE_COMMAND));
  assert.ok(Object.isFrozen(SERVICE_COMMAND.args));
});

test("第二群組 route 表:恰為 2 單元 × 3 動詞 = 6 條,值全部來自枚舉", () => {
  const expectedPaths = [];
  for (const unit of EXPECTED_UNITS) {
    for (const op of EXPECTED_OPS) expectedPaths.push(`/api/service/${unit}/${op}`);
  }
  assert.deepEqual([...SERVICE_ROUTES.keys()].sort(), expectedPaths.sort());
  assert.equal(SERVICE_ROUTES.size, 6);
  for (const { unit, op } of SERVICE_ROUTES.values()) {
    assert.ok(SERVICE_UNIT_WHITELIST.includes(unit));
    assert.ok(SERVICE_OP_WHITELIST.includes(op));
  }
});

test("群組互不滲透:第一群組指令無 systemctl,第二群組模板無 dashboard;路徑前綴不重疊", () => {
  // 第一群組(dashboard)凍結常數維持原樣——擴充第二群組不得動到它
  assert.equal(FIXED_COMMAND.bin, "hermes");
  assert.deepEqual([...FIXED_COMMAND.args], ["dashboard", "--host", "127.0.0.1", "--port", "9119", "--no-open"]);
  assert.ok(!FIXED_COMMAND.args.includes("systemctl"));
  assert.ok(![...SERVICE_COMMAND.args].includes("dashboard"));
  assert.ok(SERVICE_ROUTE_PREFIX.startsWith("/api/service/"));
  for (const path of SERVICE_ROUTES.keys()) {
    assert.ok(!path.startsWith("/api/hermes/"), "第二群組 route 不得落在第一群組命名空間");
  }
});

test("模板不可參數化:bridge 原始碼的服務 execFile 呼叫必須是固定字面", async () => {
  const source = await readFile(join(root, "scripts", "bridge.mjs"), "utf8");
  assert.ok(
    source.includes("execFile(serviceCommand.bin, [...serviceCommand.args, op, unit]"),
    "服務控制的 execFile 必須是「凍結模板 + 枚舉 op/unit」的固定字面",
  );
  // 全檔恰兩處 execFile:taskkill(第一群組)+ 服務控制(第二群組)
  const execFileCalls = source.match(/execFile\(/g) ?? [];
  assert.equal(execFileCalls.length, 2, "execFile 呼叫位點僅允許 taskkill 與服務控制兩處");
  // 禁區動詞(提案 §0.2)在 bridge 程式碼技術上不存在——
  // 先剝離 // 註解再掃(「明確不做」的說明文字不是入口),與
  // scripts/webui_security_check.py 既有慣例一致
  const codeOnly = source.replace(/(?<!:)\/\/.*$/gm, "");
  for (const forbidden of ['"enable"', '"disable"', '"mask"', "daemon-reload", "--terminate"]) {
    assert.ok(!codeOnly.includes(forbidden), `bridge 程式碼不得含禁區動詞: ${forbidden}`);
  }
});

// ---------------------------------------------------------------------------
// HTTP 行為(以 FAKE_WSL fixture 執行,絕不碰真實 systemd)
// ---------------------------------------------------------------------------

test("服務控制 HTTP 行為(白名單/400/409/exit code/audit)", async (t) => {
  const dir = makeTempDir();
  const spawnLogPath = join(dir, "wsl-spawn.log");
  const bridge = makeBridge(dir, spawnLogPath);
  await bridge.listen();

  t.after(async () => {
    await bridge.shutdown();
    rmSync(dir, { recursive: true, force: true });
    delete process.env.FAKE_WSL_SPAWN_LOG;
    delete process.env.FAKE_WSL_EXIT_CODE;
    delete process.env.FAKE_WSL_DELAY_MS;
  });

  await t.test("六條白名單 route 全部可用,fixture 收到的 argv 恰為 [op, unit]", async () => {
    let expected = 0;
    for (const [path, { unit, op }] of SERVICE_ROUTES) {
      const r = await fetch(`${BRIDGE_URL}${path}`, { method: "POST" });
      const body = await r.json();
      assert.equal(r.status, 200, `${path} 應為 200`);
      assert.equal(body.ok, true);
      assert.equal(body.unit, unit);
      assert.equal(body.op, op);
      assert.equal(body.exitCode, 0);
      expected += 1;
      const spawns = readSpawnLog(spawnLogPath);
      assert.equal(spawns.length, expected);
      assert.deepEqual(spawns[expected - 1].args, [op, unit], "模板附加參數只能是枚舉的 op/unit");
    }
  });

  await t.test("request body 完全被忽略:惡意 body 進不了指令", async () => {
    const before = readSpawnLog(spawnLogPath).length;
    const r = await fetch(`${BRIDGE_URL}/api/service/hermes-worker.service/restart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unit: "evil.service", op: "mask; rm -rf /", args: ["--evil"] }),
    });
    assert.equal(r.status, 200);
    const spawns = readSpawnLog(spawnLogPath);
    assert.equal(spawns.length, before + 1);
    assert.deepEqual(spawns[spawns.length - 1].args, ["restart", "hermes-worker.service"]);
  });

  await t.test("非白名單一律 400 + 不 spawn:單元外/動詞外/多段路徑/尾斜線", async () => {
    const before = readSpawnLog(spawnLogPath).length;
    const badPaths = [
      "/api/service/hermes-rss.service/restart", // 白名單外單元(timer 驅動)
      "/api/service/hermes-worker.service/enable", // 白名單外動詞
      "/api/service/hermes-worker.service/stop/extra", // 多段
      "/api/service/hermes-worker.service/stop/", // 尾斜線(非全字串命中)
      "/api/service/hermes-cron-daily-memory-check.timer/stop", // timer 不在白名單(拍板項 2)
      "/api/service/hermes-worker.service%2Fstop", // 編碼繞過形狀(不做解碼,全字串不命中)
    ];
    for (const path of badPaths) {
      const r = await fetch(`${BRIDGE_URL}${path}`, { method: "POST" });
      assert.equal(r.status, 400, `${path} 應為 400`);
    }
    assert.equal(readSpawnLog(spawnLogPath).length, before, "400 情境不得 spawn 任何 process");
  });

  await t.test("方法錯誤(GET/DELETE)→ 404:route 是「方法+路徑」整組寫死", async () => {
    const get = await fetch(`${BRIDGE_URL}/api/service/hermes-worker.service/stop`, { method: "GET" });
    assert.equal(get.status, 404);
    const del = await fetch(`${BRIDGE_URL}/api/service/hermes-worker.service/stop`, { method: "DELETE" });
    assert.equal(del.status, 404);
  });

  await t.test("CORS:非本機 origin 一律 403(沿用 bridge 既有檢查)", async () => {
    const evil = await fetch(`${BRIDGE_URL}/api/service/hermes-worker.service/restart`, {
      method: "POST",
      headers: { Origin: "http://evil.example:5173" },
    });
    assert.equal(evil.status, 403);
  });

  await t.test("重複操作防護:同一單元併發 → 一個 200 一個 409;不同單元不互擋", async () => {
    process.env.FAKE_WSL_DELAY_MS = "1200";
    const same = await Promise.all([
      fetch(`${BRIDGE_URL}/api/service/hermes-worker.service/restart`, { method: "POST" }),
      fetch(`${BRIDGE_URL}/api/service/hermes-worker.service/restart`, { method: "POST" }),
    ]);
    assert.deepEqual(same.map((r) => r.status).sort(), [200, 409]);
    const different = await Promise.all([
      fetch(`${BRIDGE_URL}/api/service/hermes-worker.service/stop`, { method: "POST" }),
      fetch(`${BRIDGE_URL}/api/service/hermes-telegram.service/stop`, { method: "POST" }),
    ]);
    assert.deepEqual(different.map((r) => r.status), [200, 200]);
    delete process.env.FAKE_WSL_DELAY_MS;
  });

  await t.test("非零 exit code → 500,回應與 audit 均帶 exit code", async () => {
    process.env.FAKE_WSL_EXIT_CODE = "5";
    const r = await fetch(`${BRIDGE_URL}/api/service/hermes-telegram.service/restart`, { method: "POST" });
    const body = await r.json();
    assert.equal(r.status, 500);
    assert.equal(body.ok, false);
    assert.equal(body.exitCode, 5);
    delete process.env.FAKE_WSL_EXIT_CODE;
  });

  await t.test("audit:每次操作(含拒絕)一筆——時間、動詞、單元、結果/exit code", async () => {
    const auditPath = join(dir, AUDIT_LOG_NAME);
    assert.ok(existsSync(auditPath));
    const text = readFileSync(auditPath, "utf8");
    const serviceLines = text.trim().split("\n").filter((l) => l.includes("| service:"));
    assert.match(text, /\| service:restart \| unit=hermes-worker\.service \| 成功 exit=0/);
    assert.match(text, /\| service:stop \| unit=hermes-telegram\.service \| 成功 exit=0/);
    assert.match(text, /\| service:restart \| unit=hermes-telegram\.service \| 失敗 exit=5/);
    assert.match(text, /\| service:restart \| unit=hermes-worker\.service \| 拒絕\(該單元已有操作進行中\)/);
    assert.match(text, /\| service:- \| unit=- \| 拒絕\(白名單外路徑:\/api\/service\/hermes-rss\.service\/restart\)/);
    assert.match(text, /拒絕\(白名單外路徑:\/api\/service\/hermes-worker\.service\/enable\)/);
    for (const line of serviceLines) {
      assert.match(
        line,
        /^\d{4}-\d{2}-\d{2}T[\d:.]+Z \| service:(start|stop|restart|-) \| unit=\S+ \| /,
        `audit 格式: ${line}`,
      );
    }
    // 白名單外拒絕至少 6 筆(上面 badPaths 每條一筆)
    assert.ok(serviceLines.filter((l) => l.includes("白名單外路徑")).length >= 6);
  });
});

// ---------------------------------------------------------------------------
// UI 端靜態鎖定(ServiceControl.tsx 與 App.tsx)
// ---------------------------------------------------------------------------

test("UI 枚舉與 bridge 白名單一致:單元/動詞字面集合相等,無白名單外單元", async () => {
  const source = await readFile(join(root, "src", "ServiceControl.tsx"), "utf8");
  const unitLiterals = new Set(source.match(/hermes-[a-z-]+\.service/g));
  assert.deepEqual([...unitLiterals].sort(), [...SERVICE_UNIT_WHITELIST].sort(), "UI 不得出現白名單外的單元名");
  for (const op of SERVICE_OP_WHITELIST) {
    assert.ok(source.includes(`"${op}"`), `UI 枚舉須含動詞 ${op}`);
  }
  // 禁區動詞字面不得出現(disabled= 屬性不含引號,不受此掃描影響)
  for (const forbidden of ['"enable"', '"disable"', '"mask"', "daemon-reload", "--terminate"]) {
    assert.ok(!source.includes(forbidden), `UI 不得含禁區動詞: ${forbidden}`);
  }
});

test("UI 請求面:只打 bridge 8787 的 /api/service/ 枚舉模板與 /health", async () => {
  const source = await readFile(join(root, "src", "ServiceControl.tsx"), "utf8");
  assert.match(source, /BRIDGE_URL = "http:\/\/127\.0\.0\.1:8787"/);
  assert.match(source, /SERVICE_ROUTE_PREFIX = "\/api\/service\/"/);
  const fetches = source.match(/fetch\([^)]*\)/gs) ?? [];
  assert.equal(fetches.length, 2, "fetch 位點恰為 health 探測與服務操作兩處");
  assert.ok(source.includes("`${BRIDGE_URL}/health`"));
  assert.ok(
    source.includes("`${BRIDGE_URL}${SERVICE_ROUTE_PREFIX}${action.unit}/${action.op}`"),
    "操作 URL 必須由凍結枚舉值組出",
  );
  assert.ok(!source.includes("/api/hermes/"), "UI 服務控制元件不得碰第一群組端點(防群組滲透)");
});

test("UI 行為邊界:二次確認、stop 語意明示、不樂觀更新、bridge 離線停用", async () => {
  const source = await readFile(join(root, "src", "ServiceControl.tsx"), "utf8");
  // 二次確認:按鈕先 setPending,execute 只由確認鈕觸發
  assert.ok(source.includes("setPending({ unit, op })"), "第一次點擊只能進入待確認狀態");
  assert.ok(source.includes("onClick={() => execute(pending)}"), "execute 只能由確認鈕觸發");
  assert.match(source, />\s*取消\s*</, "須有取消鈕");
  // 圖示化(2026-07-27):操作鈕改 play/reload/stop 圖示——斷言改認
  // aria-label/title(「動詞 + 完整單元名」全文描述),不再認可見文字;
  // 無障礙不得倒退,零外部依賴(inline SVG,不引入 icon 字型/CDN)
  assert.ok(source.includes("aria-label={`${OP_LABELS[op]} ${unit}`}"), "圖示按鈕須帶完整 aria-label(動詞+單元名)");
  assert.ok(source.includes("title={`${OP_LABELS[op]} ${unit}`}"), "圖示按鈕須帶 title tooltip(動詞+單元名)");
  assert.ok(source.includes("<svg"), "圖示須為 inline SVG(CSP 自足,零外部依賴)");
  // 二次確認對話維持全文字(破壞性操作的確認必須一眼可讀,不圖示化)
  assert.match(
    source,
    /確定要對 <b>\{pending\.unit\}<\/b> 執行「\{OP_LABELS\[pending\.op\]\}」/,
    "確認對話文字須為全文字(單元名+動詞全文)",
  );
  assert.match(source, />\s*確認\{OP_LABELS\[pending\.op\]\}\s*</, "確認鈕須為全文字,不圖示化");
  // stop 語意(v1.1 §2.4 定案文字)必須完整出現
  assert.ok(
    source.includes("停止後不自動恢復,直到下次 Windows 登入/WSL distro 重啟時由 systemd(依 enable 狀態)重新拉起。"),
    "stop 真實語意文字必須完整明示於按鈕旁",
  );
  // 不樂觀更新:成功後只顯示收斂等待,不寫死成功狀態
  assert.ok(source.includes("等待狀態收斂"), "操作後須顯示收斂等待,不樂觀更新");
  // bridge 離線:按鈕停用+說明,不做假按鈕
  assert.ok(source.includes('bridgeState !== "online"'));
  assert.ok(source.includes("disabled={disabled}"), "bridge 離線時按鈕必須是明確的停用狀態");
  assert.ok(source.includes("未運行,按鈕已停用"), "停用時須說明原因與啟動方式");
});

test("掛載位置(2026-08-03 遷移):ServiceControl 移出 sidebar,掛在總覽頁 Worker/Adapter 狀態上方的兩欄 grid", async () => {
  const app = await readFile(join(root, "src", "App.tsx"), "utf8");
  // sidebar 不再掛服務控制;聚合燈(ResidentStatus)留在原位常駐
  assert.ok(!app.includes("<ServiceControl />"), "App.tsx(sidebar)不得再掛服務控制鍵");
  const sidebarIdx = app.indexOf('className="sidebar"');
  const residentIdx = app.indexOf("<ResidentStatus />");
  const workspaceIdx = app.indexOf('className="workspace"');
  assert.ok(sidebarIdx >= 0 && residentIdx > sidebarIdx && residentIdx < workspaceIdx,
    "聚合燈必須留在 sidebar 原位(常駐訂閱 resident store,輪詢不因切 view 停止)");
  const overview = await readFile(join(root, "src", "views", "Overview.tsx"), "utf8");
  const gridIdx = overview.indexOf('className="service-overview-grid"');
  const serviceIdx = overview.indexOf("<ServiceControl />");
  const workerPanelIdx = overview.indexOf("Worker / Adapter 狀態");
  assert.ok(gridIdx >= 0 && serviceIdx > gridIdx && workerPanelIdx > serviceIdx,
    "服務控制鍵必須在總覽頁的兩欄 grid 內、Worker/Adapter 狀態區塊上方");
});

test("讀寫分離不回退:ResidentStatus.tsx 仍零操作入口(button/onClick/fetch 皆無)", async () => {
  const source = await readFile(join(root, "src", "ResidentStatus.tsx"), "utf8");
  assert.ok(!source.includes("<button"));
  assert.ok(!source.includes("onClick"));
  assert.ok(!source.includes("fetch("));
});
