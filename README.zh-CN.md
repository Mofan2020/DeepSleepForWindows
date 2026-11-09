# DeepSleep For Windows(Beta)

> **Windows 上的电源与睡眠管理工具，用 Python 写。** 按空闲时间切换电源计划、保持唤醒、服务器模式，以及基于规则的自动化。

![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2B%2011-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Release](https://img.shields.io/badge/release/Mofan2020/DeepSleepForWindows)

- **版本**：0.3.1
- **语言**：Python 3.11+，pywebview
- **协议**：MIT

[English](./README.md) · [Releases](https://github.com/Mofan2020/DeepSleepForWindows/releases)

## 它做什么

Windows 自带的电源设置面板和 `powercfg` 命令行都回答不了大家真正关心的问题：*我走开的时候把机器切到安静的计划，回来的时候切回快的那个。*

DeepSleep For Windows 替你做这件事，还允许你在这基础上写自己的规则。

- **按空闲切换电源计划** —— 超过 N 分钟没有键盘或鼠标动作、并且 CPU 占用低于阈值时，切换到安静的电源计划；回来后立刻切回。
- **游戏检测** —— 游戏进程在跑时保持高性能计划，并让机器不休眠。
- **服务器模式** —— 持续防睡眠，空闲一段时间后自动锁屏。
- **自动化规则** —— 把空闲时长、CPU 占用、进程是否运行、时间段、星期几组合起来，全部成立时执行动作。
- **后台常驻** —— 两个计划任务，一个跑守护进程，一个跑在用户会话里，随时关窗口都不影响。装、启、停都能在窗口里点。
- **自己的电源计划** —— 复制一个计划，改它里面的内容：多久关显示、多久睡觉、多久休眠、处理器最低降到多少。内置计划还能一键恢复到 Windows 出厂时的设置。
- **两套现成的计划** —— 「DSForWin 最节能」和「DSForWin 最高性能」，从「平衡」复制一份再改出来，需要的时候一键创建。
- **显卡专项省电** —— 把显卡偏好设成省电或最高性能，顺便看驱动报上来的负载。
- **要窗口的话也有** —— 一个 pywebview 小界面，看状态、改设置、管规则。
- **实时看日志** —— 日志边写边看，按行数或按天数裁剪，也能直接清空。
- **不碍事** —— 通知区域常驻一个小图标，程序同时只跑一个。
- **会自己更新** —— 从 GitHub release 下载新版本，校验后替换自己，任务跟着指向新文件，然后重新启动。程序换了位置也不会被落在后面。

## 工作原理

Windows 的后台服务运行在没有键盘也没有鼠标的会话里。那里的进程无法判断有没有人在电脑前 —— 它看起来永远是空闲的。所以 DeepSleep For Windows 分成两半：

```
DeepSleepForWindows daemon    session 0    电源计划、规则、保持唤醒
      ▲                                          （回环 TCP，逐行 JSON）
      ▼
DeepSleepForWindows agent     session 1    读真实空闲时间、锁屏
```

agent 是唯一和 Win32 API 打交道的地方。规则引擎、配置存储、电源计划处理全是普通 Python。系统数据来自 Windows API、注册表或 `psutil`，**从不解析命令行的输出** —— 那是本地化的，在德国机器上和在中国机器上读出来的东西不一样。没有 pywin32，没有 .NET，没有 PowerShell。

## 安装

从 [Releases](https://github.com/Mofan2020/DeepSleepForWindows/releases) 下载 zip，解压出来就是**一个 `DeepSleepForWindows.exe`**，别的什么都没有。放在哪儿都行，双击就能用。

注册后台任务：在窗口里点「安装并启动」就行。也可以在 cmd 或 PowerShell 里跑：

```bat
DeepSleepForWindows.exe install
```

这会注册两个计划任务。守护进程在登录时启动，agent 跑在你自己的会话里，所以它能读到真实的空闲时间。通知区域的托盘图标是第三个任务，机器允许才注册 —— 就算它被拒绝也不影响安装，守护进程和 agent 才是要紧的。

打开窗口：双击那个 exe，或者：

```bat
DeepSleepForWindows.exe gui
```

全部卸载：

```bat
DeepSleepForWindows.exe uninstall
```

程序没有控制台窗口，双击运行不会弹黑框。出问题时会弹一个对话框，写着失败原因和日志文件的位置。

双击会向 Windows 要管理员权限 —— 改电源计划、恢复默认设置都需要。如果你在 UAC 上点「否」，程序照常运行，只是那部分做不了，原因写进日志。

## 命令行

```bat
DeepSleepForWindows gui                  打开窗口
DeepSleepForWindows tray                 只在通知区域常驻，不开窗口
DeepSleepForWindows status               以 JSON 打印当前状态
DeepSleepForWindows plans                列出所有电源计划
DeepSleepForWindows switch "平衡"        立刻切换电源计划
DeepSleepForWindows lock                 立刻锁屏
DeepSleepForWindows sleep [--hibernate]  立刻让机器睡觉
DeepSleepForWindows update          检查新版本并安装
DeepSleepForWindows doctor               检查装得对不对
DeepSleepForWindows selftest             检查打包文件完整
DeepSleepForWindows install              注册计划任务
DeepSleepForWindows uninstall            移除计划任务
```

## 配置

设置保存在 `%ProgramData%\DeepSleepForWindows\config.json`（从源码运行时是 `%LOCALAPPDATA%\DeepSleepForWindows\config.json`）。窗口会改这个文件；文件本身就是普通 JSON，也可以手改。

```json
{
  "idle_plan": "节能",
  "active_plan": "平衡",
  "idle_seconds": 600,
  "idle_cpu_percent": 15.0,
  "game_processes": ["steam.exe", "yysls.exe"],
  "game_keeps_awake": true,
  "server_mode": false,
  "server_lock_seconds": 300,
  "keep_awake": false,
  "rules": [],
  "log_max_lines": 0,
  "log_max_age_days": 0.0,
  "last_plan_guid": ""
}
```

`log_max_lines` 和 `log_max_age_days` 用来自动裁剪日志。两个都是 `0` 表示不管。

### 规则条件

| 条件 | 取值 | 含义 |
| --- | --- | --- |
| `idle_at_least` | 秒 | 空闲时间达到这个值。也能带单位：`10m`、`2h`、`1d`。 |
| `idle_below` | 秒 | 空闲时间小于这个值。也能带单位。 |
| `cpu_below` | 百分比 | CPU 占用低于这个值。 |
| `cpu_above` | 百分比 | CPU 占用达到或高于这个值。 |
| `process_running` | 进程名 | 某个进程在运行。`.exe` 可写可不写。 |
| `time_window` | `HH:MM-HH:MM` | 当前时间在这个区间内。跨零点也支持。 |
| `day_of_week` | `mon,tue,…` | 今天是其中某一天。 |

### 规则动作

| 动作 | 取值 | 含义 |
| --- | --- | --- |
| `set_power_plan` | 计划名 | 切换电源计划。 |
| `keep_awake` | `on` / `off` | 保持唤醒。 |
| `lock_screen` | – | 锁屏。 |
| `run_command` | 命令行 | 执行一条命令。 |

## 源码构建

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Windows 构建用 PyInstaller，配置见 `main.spec`。

## 一点说明

- 空闲时间只能在用户自己的会话里读到。这就是 agent 存在的原因，也是为什么只当服务跑的工具永远判断不了机器是不是空闲的。
- 会注入模拟输入的远控软件（AnyDesk 之类）会让系统看起来一直很忙。DeepSleep For Windows 的规则可以绕开这点 —— 用 `process_running` 条件代替空闲条件。
- 守护进程每次检查都会重新读实际的电源计划，跟期望值对不上就立刻改回来。别的程序（比如 Process Lasso）抢电源计划时，日志里会写出来。

## 签名

发布的 `.exe` 是用代码签名证书签过的，带 DigiCert 的 RFC 3161 时间戳，所以证书到期后签名依然有效。

签名用的是私有 CA（ServBay User CA），Windows 默认不信任它，所以第一次运行时可能提示「未知发布者」。把仓库里的 `certificates/ServBay-User-CA-ECC-Root.crt` 装进「受信任的根证书颁发机构」就不会提示了：

```bat
certutil -addstore Root certificates\ServBay-User-CA-ECC-Root.crt
```

装之前可以先看一眼证书的指纹，确认是自己那份：

```bat
certutil -dump certificates\ServBay-User-CA-ECC-Root.crt
```

## 协议

MIT，见 [LICENSE](./LICENSE)。
