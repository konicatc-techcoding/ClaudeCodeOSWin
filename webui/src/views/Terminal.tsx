// ClaudeCode CLI view(P3,設計正本 docs/webui-pty-terminal-proposal.md)。
//
// 這是整個 Web UI 中唯一的「寫入型」view:xterm.js 連上獨立 PTY server
// (ws://127.0.0.1:8801),spawn 一個完整的前台 claude session(與本機終端機
// 同權、可寫 memory 正本)。安全邊界:
// - 連線需要 per-boot token(launcher 經 VITE_AGENTOS_PTY_TOKEN 注入,
//   不落磁碟);沒有 token 或 PTY server 未啟動時,顯示明確狀態與啟動
//   指引——不做假介面、不做 disabled 假終端(P0 mock 清零原則)。
// - 進入 view 不自動 spawn:使用者主動按「啟動 session」才連線+spawn
//   (提案 §7,寫入例外一律要明確的使用者動作)。
// - client 只送兩種訊息:stdin 與 resize;沒有其他協定面。
// - 頁面頂部警語為不可移除的固定文字(TERMINAL_PAGE_WARNING)。
import { useCallback, useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

// localhost-only:PTY server 位址寫死 127.0.0.1(與 8787/8799 物理區隔)
const PTY_HEALTH_URL = "http://127.0.0.1:8801/health";
const PTY_WS_URL = "ws://127.0.0.1:8801";
const PTY_START_COMMAND = "cd webui && npm run local";

// 提案 §7:view 頂部固定一行不可移除的說明(視覺區隔,與唯讀 view 根本不同)
export const TERMINAL_PAGE_WARNING =
  "這是完整的前台 Claude Code session——與本機終端機同權,可經你核准執行指令與修改檔案;" +
  "它是整個 Web UI 唯一能寫入 memory 正本的入口,且終端輸出未經 redact 掃描,可能直接顯示明文憑證等敏感內容。";

// per-boot token:只存在於 launcher → Vite dev server 記憶體與本 bundle
// 執行期;非經 npm run local 啟動(例如單獨 npm run dev)時為空字串。
const PTY_TOKEN: string = import.meta.env.VITE_AGENTOS_PTY_TOKEN ?? "";

type ServerState = "checking" | "online" | "offline";
type SessionState = "idle" | "connecting" | "active" | "disconnected" | "ended";

type ServerMessage =
  | { type: "ready"; pid: number; reconnected: boolean }
  | { type: "output"; data: string }
  | { type: "exit"; reason: string; code?: number };

export default function TerminalView() {
  const [serverState, setServerState] = useState<ServerState>("checking");
  const [serverSessionActive, setServerSessionActive] = useState(false);
  const [sessionState, setSessionState] = useState<SessionState>("idle");
  const [notice, setNotice] = useState("尚未啟動 session。");

  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const sessionStateRef = useRef<SessionState>("idle");
  sessionStateRef.current = sessionState;

  const probeServer = useCallback(async () => {
    setServerState("checking");
    try {
      const response = await fetch(PTY_HEALTH_URL, { signal: AbortSignal.timeout(2500) });
      const body = (await response.json()) as { ok?: boolean; sessionActive?: boolean };
      if (response.ok && body.ok) {
        setServerState("online");
        setServerSessionActive(Boolean(body.sessionActive));
        return true;
      }
      setServerState("offline");
      return false;
    } catch {
      setServerState("offline");
      return false;
    }
  }, []);

  useEffect(() => {
    // 進入 view 只做狀態探測(GET /health,不 spawn);不自動啟動 session
    void probeServer();
  }, [probeServer]);

  const sendResize = useCallback(() => {
    const term = termRef.current;
    const ws = wsRef.current;
    if (term && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    }
  }, []);

  const ensureTerminal = useCallback(() => {
    if (termRef.current || !hostRef.current) return termRef.current;
    const term = new Terminal({
      convertEol: false,
      cursorBlink: true,
      fontSize: 15,
      fontFamily: '"Cascadia Mono", Consolas, monospace',
      theme: { background: "#0d1017" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    fit.fit();
    term.onData((data) => {
      // client→server 訊息面之一:stdin(原樣位元組;不做任何指令包裝)
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "stdin", data }));
      }
    });
    term.onResize(() => sendResize());
    termRef.current = term;
    fitRef.current = fit;
    return term;
  }, [sendResize]);

  useEffect(() => {
    const onWindowResize = () => {
      fitRef.current?.fit();
    };
    window.addEventListener("resize", onWindowResize);
    // 本 view 在 App 內常駐掛載、以 display:none 隱藏——切回本 view 時容器
    // 尺寸從 0 恢復,用 ResizeObserver 觸發 refit(否則 TUI 排版會壞)
    const observer = new ResizeObserver(() => {
      if (hostRef.current && hostRef.current.clientWidth > 0) fitRef.current?.fit();
    });
    if (hostRef.current) observer.observe(hostRef.current);
    return () => {
      window.removeEventListener("resize", onWindowResize);
      observer.disconnect();
      wsRef.current?.close();
    };
  }, []);

  const connect = useCallback(
    (isReconnect: boolean) => {
      if (!PTY_TOKEN) return;
      setSessionState("connecting");
      setNotice(isReconnect ? "正在重新連線(60 秒 grace 內可接回既有 session)…" : "正在連線 PTY server 並啟動 claude session…");
      const ws = new WebSocket(`${PTY_WS_URL}/?token=${encodeURIComponent(PTY_TOKEN)}`);
      wsRef.current = ws;

      ws.onopen = () => {
        const term = ensureTerminal();
        if (!isReconnect) term?.reset();
        fitRef.current?.fit();
        sendResize();
        term?.focus();
        setSessionState("active");
        setServerSessionActive(true);
        setNotice("session 進行中——claude 結束(/exit)即終止,不會掉回 shell。");
      };

      ws.onmessage = (event) => {
        let message: ServerMessage | null = null;
        try {
          message = JSON.parse(String(event.data)) as ServerMessage;
        } catch {
          return;
        }
        if (message.type === "output") {
          termRef.current?.write(message.data);
        } else if (message.type === "ready") {
          if (message.reconnected) setNotice("已接回既有 session(斷線期間的輸出不重放)。");
        } else if (message.type === "exit") {
          setSessionState("ended");
          setServerSessionActive(false);
          setNotice(
            message.reason === "claude-exit"
              ? `claude session 已結束(exit code ${message.code ?? "?"})。可啟動新 session;續接上一段對話請在新 session 內自行使用 claude --resume。`
              : `session 已被終止(${message.reason})。`,
          );
          termRef.current?.write("\r\n\x1b[90m[AgentOS] session 已結束。\x1b[0m\r\n");
        }
      };

      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null;
        // exit 訊息已處理過的正常結束不覆蓋狀態
        if (sessionStateRef.current === "active") {
          setSessionState("disconnected");
          setNotice("連線中斷。60 秒 grace 內按「重新連線」可接回既有 session,逾時 server 會終止 claude process。");
        } else if (sessionStateRef.current === "connecting") {
          setSessionState("idle");
          setNotice("連線被拒或失敗(token/Origin 驗證未過、已有其他 session,或服務剛停止)。");
          void probeServer();
        }
      };
    },
    [ensureTerminal, probeServer, sendResize],
  );

  const canStart = serverState === "online" && PTY_TOKEN !== "" && (sessionState === "idle" || sessionState === "ended");

  return (
    <>
      <header className="topbar">
        <div className="page-identity">
          <span>CLAUDECODE CLI</span>
          <div>
            <h1>ClaudeCode CLI</h1>
            <p>完整前台 claude 終端機(cwd=repo 根,session 即 CoS)——唯一的寫入型 view。</p>
          </div>
        </div>
        <div className="top-actions">
          <div className="hermes-top-controls">
            <span className="hermes-top-address">
              <b>PTY server</b>
              <small>ws://127.0.0.1:8801(獨立 process)</small>
            </span>
            <span className={sessionState === "active" ? "online-pill" : "offline-pill"}>
              <i />
              {sessionState === "active"
                ? "Session Active"
                : sessionState === "connecting"
                  ? "Connecting"
                  : sessionState === "disconnected"
                    ? "Disconnected"
                    : serverState === "online"
                      ? "Server Ready"
                      : serverState === "checking"
                        ? "Checking"
                        : "Server Offline"}
            </span>
          </div>
        </div>
      </header>

      <div className="main-content">
        {/* 提案 §7:不可移除的警語(無任何關閉/摺疊控制) */}
        <p className="terminal-warning">{TERMINAL_PAGE_WARNING}</p>

        {serverState === "offline" && (
          <div className="panel">
            <div className="panel-body">
              <p className="error-notice">
                PTY server(127.0.0.1:8801)未啟動或無法連線——本 view 不提供假終端。啟動方式:在 repo 根執行
                <code> {PTY_START_COMMAND} </code>
                (launcher 會產生 per-boot token 並同時啟動 PTY server 與 UI;單獨 `npm run dev` 不含 PTY 服務)。
              </p>
              <button className="refresh-button" type="button" onClick={() => void probeServer()}>
                ↻ 重新檢查服務狀態
              </button>
            </div>
          </div>
        )}

        {serverState === "online" && PTY_TOKEN === "" && (
          <div className="panel">
            <div className="panel-body">
              <p className="error-notice">
                PTY server 在線,但本頁面缺少連線 token——UI 不是由 launcher 啟動(token 只在 `npm run local`
                的同一次啟動內注入,不落磁碟)。請關閉單獨啟動的 dev server,改用:<code>{PTY_START_COMMAND}</code>
              </p>
            </div>
          </div>
        )}

        {serverState === "checking" && sessionState === "idle" && <p className="info-notice">正在確認 PTY server 狀態…</p>}

        {serverState === "online" && PTY_TOKEN !== "" && (
          <>
            <div className="terminal-toolbar">
              {canStart && (
                <button className="terminal-start" type="button" onClick={() => connect(false)}>
                  <span>▶</span>
                  {sessionState === "ended" ? "啟動新 session" : "啟動 session"}
                </button>
              )}
              {sessionState === "disconnected" && (
                <button className="terminal-start" type="button" onClick={() => connect(true)}>
                  <span>↻</span>重新連線(接回 session)
                </button>
              )}
              <p className="terminal-notice">{notice}</p>
              {canStart && serverSessionActive && (
                <p className="warn-notice">PTY server 回報已有一個 session 進行中(上限 1)——新連線會被拒絕,除非該 session 已結束或處於斷線 grace。</p>
              )}
            </div>
          </>
        )}

        {/* 終端容器:只在曾建立過 terminal 時有內容;隱藏而不卸載,session 不因切 view 中斷 */}
        <div
          ref={hostRef}
          className="terminal-host"
          style={sessionState === "idle" && !termRef.current ? { display: "none" } : undefined}
        />
      </div>
    </>
  );
}
