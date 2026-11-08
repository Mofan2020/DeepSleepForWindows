# 开发笔记

记录实现过程中的决策、踩过的坑、以及验证方式。面向后续维护者。

## 架构

三个进程，两种会话：

```
daemon   session 0   电源计划、规则引擎、保持唤醒、状态纠偏
   ↕  回环 TCP，逐行 JSON
agent    session 1   读真实空闲时间、执行锁屏
gui      session 1   pywebview 界面（可选）
```

Windows 的后台服务跑在没有键鼠的会话里。那里的进程**永远看起来是空闲的**——`GetLastInputInfo` 在 session 0 返回一个无意义的巨大值，`GetForegroundWindow` 返回 0，`LockWorkStation` 直接返回 `False`。所以空闲判断和锁屏必须由 agent（session 1）来做，daemon 只负责电源。

端口在 daemon 启动时随机选，写进 `winsleep.port`，agent 读这个文件来连接。

## 为什么不用命令行解析

`powercfg /list` 和 `tasklist` 的输出是**本地化的**。中文系统上 `powercfg /list` 输出：

```
平衡的电源计划 GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (平衡)
```

英文系统上格式完全不同。任何基于正则的解析都只在一半机器上成立，在另一半上**静默拿到错值**（不报错，是错的）。

所以：

| 数据 | 来源 |
| --- | --- |
| 电源计划列表 | `winreg` 枚举 `SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes` |
| 当前 / 切换计划 | `powrprof.dll` 的 `PowerGetActiveScheme` / `PowerSetActiveScheme` |
| 进程列表 | `psutil` |
| 用户空闲时间 | `user32.GetLastInputInfo`（session 1） |
| 保持唤醒 | `kernel32.SetThreadExecutionState` |
| 锁屏 | `user32.LockWorkStation`（session 1） |

内置计划（平衡 / 高性能 / 节能 / 卓越性能）的显示名用**固定 GUID → 名字映射**，同时给中文和英文。自定义计划读注册表的 `FriendlyName`。

试过用 MUI 的 `LoadStringExW` 拿本地化名称，`ctypes` 属性查找不稳定，多次尝试拿不到可靠结果，放弃。

## 规则是用户写的，规则说了算

规则引擎（`rules.py`）和 daemon 是分开的。daemon 每个 tick 会：

1. 读配置里的规则
2. 拿当前的 snapshot 去评估（`evaluate()`）
3. 按优先级从高到低应用命中的动作

命中的 `set_power_plan` **覆盖**内置的空闲/游戏逻辑——用户明明白白写下来的规则，比程序猜的准。优先级高的先应用，同一个优先级里排在前面的先应用；低优先级的规则不能覆盖高优先级已经定下的计划。

`keep_awake` / `lock_screen` 同理。`run_command` 不带 shell 执行（`shell=False`），规则文件是用户自己的，但没必要把 shell 交出去。

`process_running` 条件需要真实的进程列表。daemon 把「游戏列表」和「所有规则里出现的进程名」合成一份，一次 `psutil` 查询，5 秒一次，不会因为规则多而变慢。

## 打包时漏掉依赖的坑

0.1.0 发出去，用户双击就报：

```
the window needs pywebview, which is not part of this build
```

根因是 CI 只装了 `requirements-dev.txt`（pytest / ruff / pyinstaller），**没装运行时依赖**。运行时依赖写在 `pyproject.toml` 里，但 CI 从来没执行过 `pip install .`，所以 pywebview 根本不在构建环境里，自然也不在包里。

本地也一样：venv 里是 `pip install -e '.[dev]'` 装的，理论上会带运行时依赖，但那个 venv 是早期建的，pywebview 一直没补上。本地只跑了 `--version` 和 `status`，没开过窗口，所以也没发现。

修法：

1. CI 两个 job 都加 `pip install .`
2. spec 里用 `collect_submodules("webview")` 收全子模块 —— pywebview 的平台后端是运行时按名字 import 的，PyInstaller 看不见。Windows 后端还要 `clr`（pythonnet）。
3. **加 `selftest` 命令**，打包后问一遍 bundle「你齐了吗」，缺什么列出来。CI 里跑这一步，不过就不发。

第 3 条是关键：靠人肉点开界面才能发现的打包问题，不该靠人肉发现。

**教训**：PyInstaller 的 hidden import 只能覆盖「静态可见」的 import。运行时按名字加载的东西，必须显式收集，并且要有机制在发布前验证。


## 单文件和无控制台窗口

发布的是一个 exe，别的什么都没有。

PyInstaller 原来是 one-folder（`dist/名字/名字.exe` + `_internal/`），改成 one-file（`EXE` 里直接带 `a.binaries` 和 `a.datas`，去掉 `COLLECT`）。代价是每次启动都要把包解到临时目录，启动慢一点，而且要有可写的临时目录。

`console=False` 之后程序没有自己的控制台窗口，双击不会闪黑框。但这样一来命令行就没地方输出了。解法是 `AttachConsole(ATTACH_PARENT_PROCESS)`：程序启动时问父进程要控制台。

- 从 cmd / PowerShell 里跑 → 父进程有控制台 → 接上 → 输出正常
- 从资源管理器双击 → 父进程是 explorer，没有控制台 → 接不上 → 什么都不发生，这正是想要的
- 输出被重定向到文件时（`sys.stdout` 不为 None）→ 不接，保留重定向

注意 `sys.stdout is None` 才是「没有控制台」的判据。GUI 子系统的程序在 PowerShell 里用 `&` 启动，PowerShell 既不等它也捕获不到它的输出。CI 里改用 `Start-Process -Wait -RedirectStandardOutput`。

失败提示：没有控制台窗口，错误就没人看得见。所以窗口打不开时弹 `MessageBoxW`，里面写清楚原因和日志文件的路径。agent 跑在用户会话里，有桌面，也弹。daemon 在 session 0，弹不出来，只能写日志。

one-file 下 `__file__` 指向解包目录，`gui/app.py` 里加了 `sys._MEIPASS` 的兜底。


## 状态纠偏

macOS 原版在 `SleepController.swift` 里会检查实际的 `disablesleep`，发现被外部改了立刻写回去。Windows 版对电源计划做同样的事：

daemon 每个 tick（5 秒）都**重新读一次实际的计划**，和期望值比较，不一致就立刻 `PowerSetActiveScheme` 写回。不缓存上次的结果——缓存了就发现不了别人改过。

连续 3 次切换失败才算「有别的程序在抢」，这时给出提示。成功的切换会把失败计数和提示一起清掉。

实测（真机，SSH 到 `DESKTOP-9ARGO8C`）：

| 步骤 | 结果 |
| --- | --- |
| 报告空闲 10 秒 | 计划 = Bitsum Highest Performance |
| 报告空闲 300 秒（阈值 60 秒） | 计划 = 节能 `a1841308-3541-4fab-bc81-f71556f20b4a` |
| 外部手动改成高性能 | daemon 5 秒内改回节能 |
| 回到活跃 | 计划 = Bitsum Highest Performance |

## 测试

```
pytest -q                    # 单元 + 集成（含真机 Windows 测试，跳过）
pytest -q -m windows ...     # 需要真 Windows
python scripts/mutation_test.py
```

**变异测试是强制的。** 每个新模块都要有：把实现改坏，看测试会不会红。`scripts/mutation_test.py` 里放了 10 个变异，覆盖电源计划纠偏、CPU 阈值、游戏优先、idle 阈值边界、配置里的零值陷阱、服务器模式重复锁屏、进程名匹配。

当前：10/10 全部被抓住。

写变异脚本的坑：

- **还原必须在 `finally` 里。** 上一轮变异跑一半被杀掉，变异代码留在仓库里，下次跑测试直接红，没人知道是被变异过的。
- **变异要改核心逻辑，别改测试。**
- **别跟常规测试并行跑。** 变异脚本在改源码，并行跑的 pytest 读的是半改半没改的文件。
- **被杀掉的变异跑会留残留。** kill 之后立刻 `git status` + grep 变异标记，确认源码树干净了再干别的。

## 踩过的坑

**`winreg.EnumKey` 的返回类型在 Python 3.13 变了。** 3.11/3.12 返回字符串，3.13 返回 `(name, class)` 元组。两种都要接。

**Session 0 里 `GetLastInputInfo` 没有意义。** 会返回几万秒的假空闲值。AnyDesk 之类的远控还会注入模拟输入，让系统看起来一直很忙——这跟 session 无关，是输入注入本身。

**`idle_cpu_percent` 用 `or` 兜默认值会吞掉 0。** 用户写 0 的意思是「CPU 必须完全空闲」，`0.0 or 15.0` 会静默变成 15。现在用显式的 `_number()` 读，只有缺 key 或不是数字才回退。

**Windows 没有 `tail`、`base64`、`timeout`。** 远端脚本统一用 `python -c` 跑，长内容走 tar 管道或 scp。

**SSH 并发会撞认证限流。** 走 `ControlMaster` 的 socket 复用一条连接；scp 也要带 `-o ControlPath=...`。

**传大文件不要走 base64 + 命令行。** 命令行长度上限会把文件截断成 0 字节，而且**不报错**——远端 `certutil` 才报 `ERROR_FILE_INVALID`。长内容用 tar 管道。

## 签名

用的证书是 `Skyc8266.crt`（带 Code Signing EKU，ECC P-384，有效期到 2029-01-12），由 ServBay User CA 签发。

几件实测出来的事：

- **ECC 在 Authenticode 上是可行的。** 以前以为 Windows 只认 RSA 的代码签名证书，实测 `signtool sign` + `signtool verify /pa` 都通过。Windows 10 1809 之后支持 ECC。
- **`certutil -user -addstore Root` 装不进去**，报 `ERROR_NOT_SUPPORTED`。装到 `LocalMachine\Root` 可以（需要管理员）。装根证书时优先用 `Import-Certificate -CertStoreLocation Cert:\LocalMachine\Root`。
- **没有根证书时 `verify /pa` 报 `0x800B010A`**（`CERT_E_UNTRUSTEDROOT`）。这就是「未知发布者」的来源。装了根证书就通过。
- **时间戳要用 `/tr http://timestamp.digicert.com /td SHA256`**，DigiCert 的 RFC 3161 时间戳响应器接受 ECC 证书链。

CI 里的做法：证书以 base64 的 PKCS#12 存在 repository secrets 里，签名前把 `certificates/` 里的根证书装进 runner 的信任库，签完 `verify /pa` 验一遍，然后把 PFX 文件删掉。私钥不进仓库、不进产物。


## 没做的部分

- **没有做工具箱。** 用户明确说不要。
- **没有做 macOS 版的全部功能。** 只做了空闲切换、游戏检测、服务器模式、规则引擎这几样基础能力。
- **计划任务安装仍用 `schtasks` / `taskkill`。** 数据获取全部改成了 API，但任务本身的创建和删除还在用命令行——改成 Task Scheduler COM 是更彻底的做法，留待后续。

