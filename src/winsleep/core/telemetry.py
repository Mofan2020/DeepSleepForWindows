"""一键禁止微软遥测。

通过修改注册表、禁用服务、修改 hosts 等方式，彻底阻止 Windows
向微软发送遥测数据。需要管理员权限。

参考：Windows 隐私设置、O&O ShutUp10、W10Privacy 等工具的实现方式。
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

__all__ = [
    "disable_telemetry",
    "enable_telemetry",
    "telemetry_status",
]

log = logging.getLogger("winsleep.telemetry")

# 注册表键值对：键路径 -> [(值名称, 类型, 值), ...]
# 类型: "dword", "string", "qword"
_TELEMETRY_REGISTRY = {
    # 核心遥测设置
    r"SOFTWARE\Policies\Microsoft\Windows\DataCollection": [
        ("AllowTelemetry", "dword", 0),
    ],
    # 应用程序遥测
    r"SOFTWARE\Policies\Microsoft\Windows\AppCompat": [
        ("AITEnable", "dword", 0),
        ("DisableInventory", "dword", 1),
        ("DisablePCA", "dword", 1),
    ],
    # Windows 更新遥测
    r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate": [
        ("DisableWindowsUpdateAccess", "dword", 1),
        ("DisableAutoUpdate", "dword", 1),
    ],
    # 体验改进计划
    r"SOFTWARE\Policies\Microsoft\SQMClient\Windows": [
        ("CEIPEnabled", "dword", 0),
    ],
    # 反馈中心
    r"SOFTWARE\Policies\Microsoft\Windows\Feedback": [
        ("FeedbackFrequency", "dword", 0),
    ],
    # 搜索遥测
    r"SOFTWARE\Policies\Microsoft\Windows\Windows Search": [
        ("DisableSearchBoxSuggestions", "dword", 1),
        ("AllowSearchToUseLocation", "dword", 0),
    ],
    # Edge 遥测
    r"SOFTWARE\Policies\Microsoft\Edge": [
        ("TelemetryEnabled", "dword", 0),
        ("MetricsReportingEnabled", "dword", 0),
    ],
    # Defender 云保护/样本提交
    r"SOFTWARE\Policies\Microsoft\Windows Defender\Spynet": [
        ("SubmitSamplesConsent", "dword", 0),
        ("DisableBlockAtFirstSeen", "dword", 1),
    ],
    r"SOFTWARE\Policies\Microsoft\Windows Defender\Features": [
        ("TamperProtection", "dword", 0),
    ],
    # 手写和语音识别
    r"SOFTWARE\Policies\Microsoft\InputPersonalization": [
        ("AllowInputPersonalization", "dword", 0),
    ],
    # 位置服务
    r"SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors": [
        ("DisableLocation", "dword", 1),
        ("DisableSensors", "dword", 1),
    ],
    # 广告 ID
    r"SOFTWARE\Policies\Microsoft\Windows\AdvertisingInfo": [
        ("DisabledByGroupPolicy", "dword", 1),
    ],
    # 应用商店遥测
    r"SOFTWARE\Policies\Microsoft\WindowsStore": [
        ("DisableStoreApps", "dword", 1),
        ("AutoDownload", "dword", 2),
    ],
    # OneDrive 遥测
    r"SOFTWARE\Policies\Microsoft\Windows\OneDrive": [
        ("DisableFileSync", "dword", 1),
        ("DisableFileSyncNGSC", "dword", 1),
    ],
    # 错误报告
    r"SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting": [
        ("Disabled", "dword", 1),
        ("DontShowUI", "dword", 1),
        ("DoNotConfirm", "dword", 1),
    ],
    # 步骤记录器
    r"SOFTWARE\Policies\Microsoft\Windows\AppCompat\PSR": [
        ("DisablePSR", "dword", 1),
    ],
}

# 需要禁用的服务
_TELEMETRY_SERVICES = [
    "DiagTrack",
    "dmwappushservice",
    "WMPNetworkSvc",
    "RetailDemo",
]

# Hosts 文件要屏蔽的域名
_TELEMETRY_HOSTS = [
    "vortex-win.data.microsoft.com",
    "settings-win.data.microsoft.com",
    "telemetry.microsoft.com",
    "telemetry.urs.microsoft.com",
    "oca.microsoft.com",
    "sqm.microsoft.com",
    "watson.microsoft.com",
    "watson.ppe.microsoft.com",
    "v10.vortex-win.data.microsoft.com",
    "v10.settings-win.data.microsoft.com",
    "settings-win.data.microsoft.com",
    "spynet2.microsoft.com",
    "spynetalt.microsoft.com",
    "sqm.df.telemetry.microsoft.com",
    "telemetry.appex.bing.net",
    "telemetry.urs.microsoft.com",
    "vortex.data.microsoft.com",
    "vortex-win.data.microsoft.com",
    "watson.microsoft.com",
    "watson.ppe.microsoft.com",
    "v10.vortex-win.data.microsoft.com",
    "v10.settings-win.data.microsoft.com",
    "settings-win.data.microsoft.com",
    "spynet2.microsoft.com",
    "spynetalt.microsoft.com",
    "sqm.df.telemetry.microsoft.com",
    "telemetry.appex.bing.net",
    "telemetry.urs.microsoft.com",
    "vortex.data.microsoft.com",
    "vortex-win.data.microsoft.com",
    "watson.microsoft.com",
    "watson.ppe.microsoft.com",
    "v10.vortex-win.data.microsoft.com",
    "v10.settings-win.data.microsoft.com",
    "settings-win.data.microsoft.com",
    "df.telemetry.microsoft.com",
    "reports.wes.df.telemetry.microsoft.com",
    "cs.wpc.v0cdn.net",
    "vortex-sandbox.data.microsoft.com",
    "oca.microsoft.com",
    "oca.telemetry.microsoft.com",
    "oca.telemetry.microsoft.com.nsatc.net",
    "pre.footprintpredict.com",
    "reports.wes.df.telemetry.microsoft.com",
    "vortex.data.microsoft.com",
    "v10.vortex-win.data.microsoft.com",
    "v10.settings-win.data.microsoft.com",
    "settings-win.data.microsoft.com",
]

# 计划任务
_TELEMETRY_TASKS = [
    r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
    r"\Microsoft\Windows\Application Experience\ProgramDataUpdater",
    r"\Microsoft\Windows\Autochk\Proxy",
    r"\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
    r"\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask",
    r"\Microsoft\Windows\Customer Experience Improvement Program\UsbCeipTask",
    r"\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
    r"\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticResolver",
    r"\Microsoft\Windows\PI\Sqm-Tasks",
    r"\Microsoft\Windows\Power Efficiency Diagnostics\AnalyzeSystem",
    r"\Microsoft\Windows\RAC\RACAgent",
    r"\Microsoft\Windows\Shell\FamilySafetyMonitor",
    r"\Microsoft\Windows\Shell\FamilySafetyMonitorToast",
    r"\Microsoft\Windows\Shell\FamilySafetyRefreshTask",
    r"\Microsoft\Windows\Shell\FamilySafetyUpload",
    r"\Microsoft\Windows\SoftwareProtectionPlatform\SvcRestartTask",
    r"\Microsoft\Windows\SystemRestore\SR",
    r"\Microsoft\Windows\WDI\ResolutionHost",
    r"\Microsoft\Windows\Windows Error Reporting\QueueReporting",
]

def _run_reg_add(key: str, name: str, type_: str, value: str) -> tuple[bool, str]:
    try:
        cmd = [
            "reg", "add", key,
            "/v", name,
            "/t", type_.upper(),
            "/d", str(value),
            "/f"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return True, "ok"
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


def _run_reg_delete(key: str, name: str) -> tuple[bool, str]:
    try:
        cmd = ["reg", "delete", key, "/v", name, "/f"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return True, "ok"
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


def _service_action(action: str, name: str) -> tuple[bool, str]:
    try:
        if action == "disable":
            cmd = ["sc", "config", name, "start=", "disabled"]
        elif action == "enable":
            cmd = ["sc", "config", name, "start=", "demand"]
        elif action == "stop":
            cmd = ["sc", "stop", name]
        elif action == "start":
            cmd = ["sc", "start", name]
        else:
            return False, f"unknown action: {action}"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return True, "ok"
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


def _hosts_action(action: str, hosts: list[str]) -> tuple[bool, str]:
    hosts_path = Path(r"C:\Windows\System32\drivers\etc\hosts")
    try:
        content = hosts_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        marker_start = "# >>> DeepSleep Telemetry Block Start >>>"
        marker_end = "# <<< DeepSleep Telemetry Block End <<<"

        if action == "block":
            new_lines = [line for line in lines if marker_start not in line and marker_end not in line]
            filtered = [line for line in new_lines if not any(h in line for h in _TELEMETRY_HOSTS)]
            filtered.append("# >>> DeepSleep Telemetry Block Start >>>")
            for h in hosts:
                filtered.append(f"0.0.0.0 {h}")
            filtered.append("# <<< DeepSleep Telemetry Block End <<<")
            hosts_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
            return True, f"blocked {len(hosts)} domains"
        elif action == "unblock":
            new_lines = []
            in_block = False
            for line in lines:
                if "# >>> DeepSleep Telemetry Block Start >>>" in line:
                    in_block = True
                    continue
                if "# <<< DeepSleep Telemetry Block End <<<" in line:
                    in_block = False
                    continue
                if not in_block:
                    new_lines.append(line)
            hosts_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            return True, "unblocked"
        return False, "unknown action"
    except Exception as exc:
        return False, str(exc)


def _task_action(action: str, tasks: list[str]) -> tuple[bool, str]:
    try:
        success = 0
        for task in tasks:
            if action == "disable":
                cmd = ["schtasks", "/change", "/tn", task, "/disable"]
            elif action == "enable":
                cmd = ["schtasks", "/change", "/tn", task, "/enable"]
            else:
                continue
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                success += 1
        return True, f"{action}d {success}/{len(tasks)} tasks"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


def disable_telemetry() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "only available on Windows"

    messages = []

    log.info("writing telemetry registry keys...")
    reg_ok = 0
    for key, values in _TELEMETRY_REGISTRY.items():
        for name, type_, value in values:
            ok, msg = _run_reg_add(f"HKLM\\{key}", name, type_, value)
            if ok:
                reg_ok += 1
            else:
                log.warning("registry %s\\%s failed: %s", key, name, msg)
    messages.append(f"registry: {reg_ok} keys set")

    log.info("disabling telemetry services...")
    svc_ok = 0
    for svc in _TELEMETRY_SERVICES:
        _service_action("stop", svc)
        ok, _ = _service_action("disable", svc)
        if ok:
            svc_ok += 1
    messages.append(f"services: {svc_ok}/{len(_TELEMETRY_SERVICES)} disabled")

    log.info("blocking telemetry domains in hosts...")
    ok, msg = _hosts_action("block", _TELEMETRY_HOSTS)
    messages.append(f"hosts: {msg}")

    log.info("disabling telemetry tasks...")
    ok, msg = _task_action("disable", _TELEMETRY_TASKS)
    messages.append(f"tasks: {msg}")

    log.info("telemetry disabled: %s", "; ".join(messages))
    return True, "; ".join(messages)


def enable_telemetry() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "only available on Windows"

    messages = []

    log.info("restoring telemetry registry keys...")
    reg_ok = 0
    for key, values in _TELEMETRY_REGISTRY.items():
        for name, _, _ in values:
            ok, _ = _run_reg_delete(f"HKLM\\{key}", name)
            if ok:
                reg_ok += 1
    messages.append(f"registry: {reg_ok} keys removed")

    log.info("enabling telemetry services...")
    svc_ok = 0
    for svc in _TELEMETRY_SERVICES:
        ok, _ = _service_action("enable", svc)
        if ok:
            svc_ok += 1
    messages.append(f"services: {svc_ok}/{len(_TELEMETRY_SERVICES)} enabled")

    ok, msg = _hosts_action("unblock", [])
    messages.append(f"hosts: {msg}")

    ok, msg = _task_action("enable", _TELEMETRY_TASKS)
    messages.append(f"tasks: {msg}")

    log.info("telemetry enabled: %s", "; ".join(messages))
    return True, "; ".join(messages)


#: What the service manager says a service is doing.
_SERVICE_STATE_NAMES = {
    0x00000001: "stopped",
    0x00000002: "starting",
    0x00000003: "stopping",
    0x00000004: "running",
    0x00000005: "continuing",
    0x00000006: "pausing",
    0x00000007: "paused",
}

#: How a service is started.
_START_TYPE_NAMES = {
    0x00000000: "boot",
    0x00000001: "system",
    0x00000002: "automatic",
    0x00000003: "manual",
    0x00000004: "disabled",
}

_SC_MANAGER_CONNECT = 0x0001
_SERVICE_QUERY_STATUS = 0x0004
_SERVICE_QUERY_CONFIG = 0x0001


class _SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


class _QUERY_SERVICE_CONFIG(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwStartType", wintypes.DWORD),
        ("dwErrorControl", wintypes.DWORD),
        ("lpBinaryPathName", wintypes.LPWSTR),
        ("lpLoadOrderGroup", wintypes.LPWSTR),
        ("dwTagId", wintypes.DWORD),
        ("lpDependencies", wintypes.LPWSTR),
        ("lpServiceStartName", wintypes.LPWSTR),
        ("lpDisplayName", wintypes.LPWSTR),
    ]


def _service_state(name: str) -> dict[str, str]:
    """What one service is doing, in words.

    A service that is not on the machine at all comes back as
    ``{"state": "missing", "start": "missing"}``, which is not an
    error: not every machine has every telemetry service.
    """
    missing = {"state": "missing", "start": "missing"}
    if sys.platform != "win32":
        return missing

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenSCManagerW.restype = wintypes.HANDLE
    handle_manager = advapi32.OpenSCManagerW(None, None, _SC_MANAGER_CONNECT)
    if not handle_manager:
        return {"state": "unknown", "start": "unknown"}

    try:
        handle_service = advapi32.OpenServiceW(
            handle_manager, name, _SERVICE_QUERY_STATUS | _SERVICE_QUERY_CONFIG
        )
        if not handle_service:
            return missing

        try:
            status = _SERVICE_STATUS()
            if not advapi32.QueryServiceStatus(
                handle_service, ctypes.byref(status)
            ):
                return {"state": "unknown", "start": "unknown"}

            # Eight kilobytes is enough for the config of any service;
            # the buffer is asked for the size it needs and then filled.
            need = wintypes.DWORD(0)
            advapi32.QueryServiceConfigW(
                handle_service, None, 0, ctypes.byref(need)
            )
            buffer = ctypes.create_string_buffer(max(need.value, 8192))
            config = ctypes.cast(
                buffer, ctypes.POINTER(_QUERY_SERVICE_CONFIG)
            ).contents
            if not advapi32.QueryServiceConfigW(
                handle_service, buffer, len(buffer), ctypes.byref(need)
            ):
                return {
                    "state": _SERVICE_STATE_NAMES.get(
                        status.dwCurrentState, "unknown"
                    ),
                    "start": "unknown",
                }

            return {
                "state": _SERVICE_STATE_NAMES.get(
                    status.dwCurrentState, "unknown"
                ),
                "start": _START_TYPE_NAMES.get(config.dwStartType, "unknown"),
            }
        finally:
            advapi32.CloseServiceHandle(handle_service)
    except Exception as exc:  # noqa: BLE001 - a query must not crash the window
        log.debug("could not read the service %s: %s", name, exc)
        return {"state": "unknown", "start": "unknown"}
    finally:
        advapi32.CloseServiceHandle(handle_manager)


def _task_is_disabled(name: str) -> bool | None:
    """Whether one task is disabled.

    ``None`` means the task is not on the machine.  The answer comes
    from the XML the scheduler publishes, which has a fixed shape and
    no translated words in it.
    """
    import subprocess
    import xml.etree.ElementTree as ElementTree

    creationflags = 0x08000000 if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/XML"],
            capture_output=True,
            check=False,
            creationflags=creationflags,
        )
    except OSError:
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    try:
        text = result.stdout.decode("utf-16", errors="replace")
        if not text.lstrip().startswith("<?xml") and "<Task" not in text:
            text = result.stdout.decode("utf-8", errors="replace")
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError):
        return None

    # There is an ``Enabled`` under ``<Triggers>`` and another one
    # under ``<Settings>``, and they mean different things.  The one
    # that switches the task itself off is the one in the settings -
    # a task whose trigger is on but whose settings say disabled does
    # not run.
    namespace = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
    settings = root.find(f"{namespace}Settings")
    if settings is None:
        return False
    enabled = settings.find(f"{namespace}Enabled")
    if enabled is None or enabled.text is None:
        return False
    return enabled.text.strip().lower() == "false"


def telemetry_status() -> dict[str, Any]:
    """How much of the telemetry is already turned off.

    Every part of the answer is read through an interface without a
    language, so the same code gives the same answer on every Windows.
    """
    if sys.platform != "win32":
        return {"ok": False, "message": "只在 Windows 上可用"}

    status: dict[str, Any] = {
        "registry": {},
        "services": {},
        "hosts_blocked": False,
        "hosts_blocked_count": 0,
        "tasks_disabled": 0,
        "tasks_present": 0,
    }

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AllowTelemetry")
            status["registry"]["AllowTelemetry"] = value
    except OSError:
        status["registry"]["AllowTelemetry"] = "not set"

    for service in _TELEMETRY_SERVICES:
        status["services"][service] = _service_state(service)

    hosts_path = Path(r"C:\\Windows\\System32\\drivers\\etc\\hosts")
    try:
        content = hosts_path.read_text(encoding="utf-8", errors="ignore")
        blocked = sum(1 for host in _TELEMETRY_HOSTS if f"0.0.0.0 {host}" in content)
        status["hosts_blocked_count"] = blocked
        status["hosts_blocked"] = blocked > len(_TELEMETRY_HOSTS) // 2
    except OSError as exc:
        log.debug("the hosts file could not be read: %s", exc)

    for task in _TELEMETRY_TASKS:
        disabled = _task_is_disabled(task)
        if disabled is None:
            continue
        status["tasks_present"] += 1
        if disabled:
            status["tasks_disabled"] += 1

    return {"ok": True, "status": status}
