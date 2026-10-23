"""Windows-owned shutdown task shared by both editions for the current user."""
import base64
import datetime as dt
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


def deadline(mode, value, now):
    if mode == '延时（分钟）':
        if not re.fullmatch(r'[0-9]+', value.strip()) or not 1 <= int(value) <= 10080:
            raise ValueError('分钟数须为 1～10080 的整数')
        return now + int(value) * 60
    if mode != '指定时间（HH:MM）' or not re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]', value.strip()):
        raise ValueError('时间须为 24 小时制 HH:MM，例如 23:30')
    hour, minute = map(int, value.split(':'))
    target = dt.datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= now:
        target += dt.timedelta(days=1)
    return target.timestamp()


# Parameters arrive as JSON on stdin, never interpolated into PowerShell code.
TASK_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($request.owner_sid) {
        if ($request.owner_sid -notmatch '^S-1-5-[0-9-]+$') { throw 'Invalid task owner.' }
        $sid = $request.owner_sid
    }
    $name = 'BlackfireKeeper-Shutdown-' + $sid
    $marker = 'BlackfireKeeper scheduled shutdown v1'
    $exe = Join-Path $env:SystemRoot 'System32\shutdown.exe'
    $task = $null
    try { $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop }
    catch { if ($_.FullyQualifiedErrorId -notlike 'CmdletizationQuery_NotFound*') { throw } }
    if ($task -and ($task.Description -ne $marker -or $task.Actions.Count -ne 1 -or
        $task.Actions[0].Execute -ne $exe -or $task.Actions[0].Arguments -ne '/s /f /t 0')) {
        throw 'Task name is occupied by an unrelated task; refusing to modify it.'
    }
    switch ($request.operation) {
        'arm' {
            $at = [datetime]::ParseExact($request.at, 'yyyy-MM-ddTHH:mm:ss', [cultureinfo]::InvariantCulture)
            if ($at -le (Get-Date)) { throw 'The requested time has already passed.' }
            $action = New-ScheduledTaskAction -Execute $exe -Argument '/s /f /t 0'
            $trigger = New-ScheduledTaskTrigger -Once -At $at
            $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
            $principal = New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
            $definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $marker
            $task = Register-ScheduledTask -TaskName $name -InputObject $definition -Force
        }
        'cancel' {
            if ($task) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
            $task = $null
        }
        'query' { }
        default { throw 'Unsupported operation.' }
    }
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $name
        $at = [datetime]$task.Triggers[0].StartBoundary
        @{ok=$true; registered=$true; at=$at.ToString('yyyy-MM-ddTHH:mm:ss'); state=[string]$task.State;
          last_result=$info.LastTaskResult; name=$name} | ConvertTo-Json -Compress
    } else { @{ok=$true; registered=$false; name=$name} | ConvertTo-Json -Compress }
} catch {
    @{ok=$false; error=$_.Exception.Message; owner_sid=$sid; code=$_.Exception.HResult} | ConvertTo-Json -Compress
    exit 1
}
'''


class WindowsShutdownSchedule:
    def __init__(self, runner=None):
        self.runner = runner or subprocess.run

    def execute(self, operation, target=None):
        if os.name != 'nt':
            raise RuntimeError('定时关机仅支持 Windows')
        request = {'operation': operation}
        if operation == 'arm':
            request['at'] = dt.datetime.fromtimestamp(target).strftime('%Y-%m-%dT%H:%M:%S')
        exe = str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
        encoded = base64.b64encode(TASK_SCRIPT.encode('utf-16le')).decode('ascii')
        result = self.runner([exe, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            input=json.dumps(request).encode('utf8'), capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        try:
            data = json.loads(result.stdout.decode('utf-8-sig'))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError('Windows 定时任务未返回有效结果，请检查任务计划程序服务') from exc
        denied = (data.get('code') == -2147024891 or
                  any(word in str(data.get('error','')).lower() for word in ('denied','拒绝')))
        if data.get('ok') is not True and denied and data.get('owner_sid') and operation in ('arm','cancel'):
            request['owner_sid'] = data['owner_sid']
            data = self.elevated(exe, request)
            result.returncode = 0 if data.get('ok') else 1
        if result.returncode or data.get('ok') is not True:
            raise RuntimeError('Windows 定时任务失败：' + data.get('error', '未知错误'))
        if operation == 'arm' and not data.get('registered'):
            raise RuntimeError('Windows 未确认关机任务注册成功')
        return data

    def elevated(self, exe, request):
        payload = base64.b64encode(json.dumps(request).encode('utf8')).decode('ascii')
        source = TASK_SCRIPT.replace('[Console]::In.ReadToEnd()',
            "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+payload+"'))")
        source = source.replace('    exit 1', '    return')
        quote = lambda s: "'" + str(s).replace("'", "''") + "'"
        with tempfile.TemporaryDirectory(prefix='BKK-shutdown-') as folder:
            result_path = Path(folder)/'result.json'
            elevated = '$report = & {\n'+source+'\n}\n[IO.File]::WriteAllText('+quote(result_path)+', ($report -join "`n"), (New-Object Text.UTF8Encoding($false)))'
            code = base64.b64encode(elevated.encode('utf-16le')).decode('ascii')
            launcher = '$ErrorActionPreference="Stop"; Start-Process -FilePath '+quote(exe)+" -ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand',"+quote(code)+' -Verb RunAs -WindowStyle Hidden -Wait'
            result = self.runner([exe,'-NoProfile','-NonInteractive','-EncodedCommand',
                base64.b64encode(launcher.encode('utf-16le')).decode('ascii')],
                capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=120)
            if result.returncode or not result_path.is_file():
                raise RuntimeError('管理员授权未完成，系统关机任务未确认；请重试并允许 Windows 授权提示')
            return json.loads(result_path.read_text(encoding='utf-8-sig'))

    def arm(self, mode, value):
        return self.execute('arm', deadline(mode, value, time.time()))

    def cancel(self):
        return self.execute('cancel')

    def query(self):
        return self.execute('query')
