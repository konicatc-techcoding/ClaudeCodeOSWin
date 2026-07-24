' hermes-wsl-keepalive.vbs — keep the WSL Ubuntu distro alive so hermes systemd
' --user services stay up. Launched by Windows Task Scheduler task
' `HermesWslKeepAlive` (At log on). See hermes/systemd/README.md.
'
' Why a .vbs wrapper: the task runs with LogonType=Interactive (same as
' HermesBridgeDaily); launching wsl.exe directly would leave a permanently
' visible console window on the desktop. wscript.exe has no console, and
' Run(..., 0, ...) starts wsl.exe with a hidden window.
'
' Why wait=True (3rd argument): wscript stays as the task process, so Task
' Scheduler shows the task as Running, MultipleInstances=IgnoreNew prevents
' duplicates, and if wsl.exe dies (e.g. `wsl --shutdown`) the task exits
' non-zero and the task's restart-on-failure setting revives it.
Dim rc
rc = CreateObject("WScript.Shell").Run( _
    """C:\Windows\System32\wsl.exe"" -d Ubuntu --exec sleep infinity", 0, True)
WScript.Quit rc
