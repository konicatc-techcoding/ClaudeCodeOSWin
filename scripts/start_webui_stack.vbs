' start_webui_stack.vbs — 零黑窗 wrapper(2026-08-03,任務 1)。
' 比照 hermes/windows/hermes-wsl-keepalive.vbs 的既有慣例:wscript.exe 本身
' 沒有主控台,Run(..., 0, ...) 再把 powershell 視窗隱藏——桌面捷徑點下去
' 零黑窗閃爍。實際邏輯全部在同目錄的 start_webui_stack.ps1(冪等啟動
' webui stack + 唯讀 API,就緒後開瀏覽器;失敗會彈出訊息框誠實回報)。
'
' 桌面捷徑目標字串(捷徑檔由主 session 建立,不在本任務範圍):
'   wscript.exe "C:\Users\razer\dev\ClaudeCodeOSWin\scripts\start_webui_stack.vbs"
'
' wait=True(第三參數):失敗時 ps1 的訊息框關掉前 wscript 不退出,
' exit code 原樣透傳(成功 0 / 失敗 1)。
Dim fso, psPath, rc
Set fso = CreateObject("Scripting.FileSystemObject")
psPath = fso.GetParentFolderName(WScript.ScriptFullName) & "\start_webui_stack.ps1"
rc = CreateObject("WScript.Shell").Run( _
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & psPath & """", 0, True)
WScript.Quit rc
