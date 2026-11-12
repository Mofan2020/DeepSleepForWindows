"""Win32 调用签名的守卫，以及「托盘跟着窗口进程走」的几条契约。

这一整个文件存在的理由是一个反复出现的 bug：

    argument 1: OverflowError: int too long to convert

它每次的形状都一样——某个函数返回的是**句柄**（64 位的普通 Python
int），这个句柄又被当成第一个参数传给另一个没声明 `argtypes` 的函数，
ctypes 按 C 的 `int` 处理，于是溢出。修了一个地方，下一个地方还会犯。

所以这里不测某一个调用点，测的是「所有拿句柄当参数的调用都必须声明
argtypes」这条规矩本身。
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "winsleep"

#: 这些 Win32 函数的第一个参数（或前几个之一）是句柄：HANDLE /
#: SC_HANDLE / HWND。不声明 argtypes 就是 64 位机器上的溢出。
HANDLE_TAKING = {
    "OpenSCManagerW",
    "OpenServiceW",
    "QueryServiceStatus",
    "QueryServiceConfigW",
    "CloseServiceHandle",
    "OpenProcessToken",
    "AdjustTokenPrivileges",
    "GetTokenInformation",
    "CloseHandle",
    "WaitForSingleObject",
    "ShellExecuteExW",
}


def _modules_calling(name: str) -> list[Path]:
    found = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(rf"\b{name}\s*\(", text):
            found.append(path)
    return found


def test_every_handle_taking_call_declares_its_signature() -> None:
    """拿句柄当参数的调用，必须写 argtypes。

    这条断言抓的是**一整类** bug，不是一个调用点：只要有一处漏了，
    那个平台（Windows）上就是一句 OverflowError，而且往往发生在
    finally 的关句柄里——把前面正常的返回值整个盖掉。
    """
    problems: list[str] = []
    for name in sorted(HANDLE_TAKING):
        for path in _modules_calling(name):
            text = path.read_text(encoding="utf-8", errors="replace")
            if not re.search(rf"\b{name}\.argtypes\s*=", text):
                problems.append(f"{path.relative_to(SRC)}: {name} 没声明 argtypes")
    assert not problems, "这些调用在 64 位 Windows 上会 OverflowError：\n" + "\n".join(
        problems
    )


def test_reading_the_telemetry_state_never_raises(monkeypatch) -> None:
    """读遥测状态不能抛异常——它就是界面上的几张卡片。

    真实的失败长这样：`OpenSCManagerW` 返回一个 64 位句柄，`CloseServiceHandle`
    在 `finally` 里拿它当参数时溢出。`finally` 里抛出的异常会盖掉正常返回值，
    于是整个查询变成一条错误。
    """

    class _Call:
        """可调用的假函数；argtypes / restype 能被赋值。"""

        restype = None
        argtypes = None

        def __init__(self, fn) -> None:
            self._fn = fn

        def __call__(self, *args):
            return self._fn(*args)

    class _Advapi32:
        def __init__(self) -> None:
            self.OpenSCManagerW = _Call(lambda *a: 1 << 40)
            self.OpenServiceW = _Call(lambda *a: 1 << 41)
            self.QueryServiceStatus = _Call(lambda *a: 1)
            self.QueryServiceConfigW = _Call(lambda *a: 0)
            self.CloseServiceHandle = _Call(self._close)

        def _close(self, *_args):
            raise OverflowError("argument 1: int too long to convert")

    from winsleep.core import telemetry

    monkeypatch.setattr(telemetry.sys, "platform", "win32")
    # WinDLL 只在 Windows 上存在，这里造一个出来给被测代码用。
    monkeypatch.setattr(
        telemetry.ctypes, "WinDLL", lambda *a, **k: _Advapi32(), raising=False
    )

    answer = telemetry._service_state("DiagTrack")

    assert answer["state"] in {"unknown", "missing"}, answer
    assert answer["start"] in {"unknown", "missing"}, answer


def test_the_icon_lives_in_the_window_process(monkeypatch) -> None:
    """托盘图标由窗口进程自己创建，不是另一个进程。

    **这是这一版改的根本。** 以前托盘是个独立的计划任务进程，只在登录
    时启动一次，装完不注销就永远没有图标；而且它跟窗口是两个进程，
    「打开主界面」只能再拉一个。
    """
    calls: list[str] = []

    class _FakeIcon:
        title = ""
        menu = None

        def run_detached(self) -> None:
            calls.append("run_detached")

        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr("winsleep.core.tray.sys.platform", "win32")
    monkeypatch.setattr("winsleep.core.tray.make_icon", lambda: _FakeIcon())
    monkeypatch.setattr("winsleep.core.tray._has_pystray", lambda: True)
    monkeypatch.setattr(
        "winsleep.core.tray.wait_for_notification_area", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "winsleep.core.tray.build_menu", lambda **kwargs: "menu-object"
    )

    from winsleep.core import tray

    handle = tray.start_icon(
        on_show=lambda: None, on_lock=lambda: None, on_quit=lambda: None
    )

    assert handle is not None, "没有拿到句柄，图标没法关掉"
    assert calls == ["run_detached"], calls
    handle.stop()
    assert calls[-1] == "stop"


def test_closing_the_window_hides_it_instead_of_ending_the_program() -> None:
    """关窗口只是收起来。返回 False 才会取消关闭（None 不算）。"""

    class _Window:
        def __init__(self) -> None:
            self.hidden = 0

        def hide(self) -> None:
            self.hidden += 1

    from winsleep.gui.app import make_close_handler

    window = _Window()
    handler = make_close_handler(window)

    assert handler() is False, "返回 False 才会取消关闭"
    assert window.hidden == 1


def test_closing_still_closes_when_the_window_cannot_be_hidden() -> None:
    """藏不起来就让它关掉——别留一个既没窗口又没图标的进程。"""

    class _Window:
        def hide(self) -> None:
            raise RuntimeError("no window")

    from winsleep.gui.app import make_close_handler

    assert make_close_handler(_Window())() is True


def test_the_tray_command_opens_the_window(monkeypatch) -> None:
    """`tray` 命令现在就是开窗口：图标由窗口进程带着。

    留一个独立的托盘进程，登录时它会自己起来，跟窗口抢通知区域。
    """
    import argparse

    from winsleep import cli

    calls: list[str] = []
    monkeypatch.setattr(cli, "_open_window", lambda: (calls.append("open"), 0)[1])

    assert cli._cmd_tray(argparse.Namespace()) == 0
    assert calls == ["open"]


def test_the_old_tray_task_is_removed_and_its_process_killed(monkeypatch) -> None:
    """老版本留下的托盘任务要清掉，不然登录时它会再挂一个图标。"""
    from winsleep.core import install

    killed: list[str] = []
    deleted: list[str] = []

    monkeypatch.setattr(install, "task_exists", lambda name: True)
    monkeypatch.setattr(install, "kill_processes", lambda sub: killed.append(sub) or 0)
    monkeypatch.setattr(
        install,
        "_run",
        lambda argv: (
            deleted.append(" ".join(argv))
            or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        ),
    )

    ok, _detail = install.retire_tray_task()
    assert ok is True
    assert killed == ["tray"], killed
    assert deleted and "/Delete" in deleted[0]


def test_no_tray_task_means_nothing_to_retire(monkeypatch) -> None:
    from winsleep.core import install

    monkeypatch.setattr(install, "task_exists", lambda name: False)
    ok, detail = install.retire_tray_task()
    assert ok is True
    assert detail == "no tray task"


# --------------------------------------------------------- 托盘菜单的动作签名

class _StrictMenuItem:
    """照 pystray 的规矩审查动作：参数超过两个就 raise ValueError(动作本身)。"""

    def __init__(self, text, action=None, **kwargs) -> None:
        self.text = text
        self.action = action
        if action is not None:
            code = getattr(action, "__code__", None)
            count = getattr(code, "co_argcount", 0)
            if count > 2:
                # pystray 抛的就是动作对象本身，所以日志里只有一串 lambda repr。
                raise ValueError(action)


class _StrictMenu:
    SEPARATOR = object()

    def __init__(self, *items) -> None:
        self.items = [item for item in items if item is not _StrictMenu.SEPARATOR]


def _fake_pystray(monkeypatch) -> None:
    import sys
    import types

    module = types.ModuleType("pystray")
    module.Menu = _StrictMenu
    module.MenuItem = _StrictMenuItem
    module.Menu.SEPARATOR = _StrictMenu.SEPARATOR
    monkeypatch.setitem(sys.modules, "pystray", module)


def test_every_menu_action_survives_pystray_signature_check(monkeypatch) -> None:
    """菜单里每个动作都要能被 pystray 接受。

    真实报错长这样（0.5.2 的真机日志）：

        the notification area icon is not available:
        <function build_menu.<locals>.<listcomp>.<lambda> at 0x...>

    原因是 pystray 的 ``MenuItem._assert_action`` 数 ``co_argcount``，
    而「把要传的参数写成默认值」的 lambda 数出来是三个参数——它不收，
    抛出的还是函数对象本身，所以连一句能读懂的话都没有。
    """
    _fake_pystray(monkeypatch)

    from winsleep.core import tray

    menu = tray.build_menu(
        on_show=lambda: None,
        on_lock=lambda: None,
        on_quit=lambda: None,
        plan_choices=lambda: [("平衡", "guid-1")],
        on_switch=lambda guid: None,
        on_override=lambda minutes: None,
    )

    assert menu.items, "菜单是空的"


def test_the_hold_entries_hand_over_the_right_number_of_minutes(monkeypatch) -> None:
    """动作签名修好之后，点「2 小时」还得真的传 120。"""
    _fake_pystray(monkeypatch)

    from winsleep.core import tray

    seen: list[int] = []
    menu = tray.build_menu(
        on_show=lambda: None,
        on_lock=lambda: None,
        on_quit=lambda: None,
        on_override=lambda minutes: seen.append(minutes),
    )

    hold_group = [item for item in menu.items if isinstance(item.action, _StrictMenu)]
    assert hold_group, "没有「保持唤醒」子菜单"
    for item in hold_group[0].action.items:
        item.action()
    assert seen == [30, 60, 120, 480, 0], seen
