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

## 那台 Windows 机器上的开发环境

源码：`D:\skyc8266\dev\MyProjects\DeepSleepForWindows`

签名相关的文件在 `D:\skyc8266\dev\`：`signing.pfx`、`signing-password.txt`、`signtool.exe`、`ServBay-User-CA-ECC-Root.crt`。**都不在仓库里。**

一条命令构建 + 签名：

```bat
powershell -ExecutionPolicy Bypass -File D:\skyc8266\dev\build.ps1
```

脚本会依次做：装依赖、跑测试、PyInstaller 打包、跑 `selftest` 验包完整、签名并验证。

**PyInstaller 不能在 `C:\Windows\Temp` 下运行** —— 报 `Do not run pyinstaller from C:\Windows\Temp`，源码要放在正常的目录里。

**用 conda 的 Python 打包时，`Library\bin` 必须在 PATH 上。**

那台机器的 Python 是 Anaconda 的 3.13.9。它的 `_ctypes.pyd` 和 `_ssl.pyd` 动态依赖 `Library\bin` 里的 `ffi-8.dll`、`libcrypto-3-x64.dll`、`libssl-3-x64.dll`——conda 只在自己运行时用 `os.add_dll_directory` 把那个目录加进搜索路径，PyInstaller 分析依赖时是从 PATH 上找 DLL 的，所以它找不到，打出来的包会在 `import ctypes` 时报 `DLL load failed while importing _ctypes`。

`build.ps1` 里加了：

```powershell
$env:PATH = "$anaconda\Library\bin;$anaconda;$anaconda\Scripts;" + $env:PATH
```

PyInstaller 只会收**真正被 import 的**那些 DLL，不会把那个目录里 454 个 DLL（1.3 GB）都塞进去。

另一条路是把 `Library\bin` 下的 DLL 显式列进 spec 的 `binaries`，但那样每个 Anaconda 版本的名字都不一样（`ffi-8.dll`、`libffi-7.dll`……），不如改 PATH。

那台机器的 Python 是 Anaconda：`D:\skyc8266\dev\env\anaconda3\python.exe`。`where python` 的第一顺位就是它，但 `C:\Program Files\python` 也装了一个，脚本里用 `python` 时要留意是哪个。


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


## 电源计划的内容也能改

用户要「自定义电源计划」，指的是改一个计划**里面**的东西——多久关显示、多久睡觉、处理器最低降到多少——而不只是换一个计划。

这些设置存在注册表里，但**不能直接改注册表**：方案树里只存了跟默认值不同的项，而且改了不生效。正确做法是用 `powrprof.dll` 的正式 API：

| 干什么 | 调用 |
| --- | --- |
| 读设置的值 | `PowerReadACValueIndex` |
| 读设置的默认值 | `PowerReadACDefaultIndex` |
| 写设置的值 | `PowerWriteACValueIndex` |
| 复制一个计划 | `PowerDuplicateScheme` |
| 给计划起名字 | `PowerWriteFriendlyName` |
| 删掉一个计划 | `PowerDeleteScheme` |

**写需要管理员权限**，因为设置在 `HKEY_LOCAL_MACHINE` 下面。所以写入走 daemon（跑在 SYSTEM 下），GUI 通过 socket 让它做。这跟锁屏必须由 agent 做是同一个道理：**没有权限的那一半，交给有的那一半**。

### 三个坑

**`RootPowerKey` 必须传 NULL。** 文档说它是「保留给以后用，必须是 NULL」。传 `HKEY_LOCAL_MACHINE` 居然也能工作——这是最容易让人放松警惕的一种「能用」，因为它不报错，只是恰好没出问题。

**`PowerDuplicateScheme` 的第三个参数是 `GUID**`，不是 `GUID*`。** 传 `GUID*` 会把源 GUID 当成目标 GUID 的地址，函数把源的内容当成指针去解引用，拿到一堆乱码当新 GUID。正确写法是传一个初始为 NULL 的 `POINTER(GUID)`，函数自己分配内存，返回时用 `LocalFree` 释放——**这段内存属于调用方，不释放就是泄漏**。

**`PowerWriteFriendlyName` 有六个参数，不是四个。** 少了 `SubGroupOfPowerSettingsGuid` 和 `PowerSettingGuid`，再少一个 `BufferSize`。而且 `Buffer` 要的是 `UCHAR*`，得用 `ctypes.cast` 把 unicode buffer 转过去，直接 `byref` 传的是缓冲区本身的地址，不是内容。

### 设置项的 GUID 是查出来的，不是记出来的

我一开始凭印象写了几个 GUID，**全是错的**：

| 我写的 | 实际 |
| --- | --- |
| `501a4d13-...` 当视频组 | 那是 PCI Express 组，视频组是 `7516b95f-...` |
| `3a0dfd23-...` 当「关闭显示」 | 那是别的系统的 GUID，真正的关闭显示是 `3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e` |
| `0012a47e-...` 当磁盘组 | 磁盘组是 `0012ee47-...` |

错的 GUID 不会报错——`PowerReadACValueIndex` 返回一个非零的错误码，`read_setting` 于是退回去读默认值，默认值也读不到，最后这一项就**悄悄消失**了。界面上少一行，程序不报错。

正确的做法是在目标机器上用 `powercfg /query SCHEME_CURRENT <子组>` 读一次，把「电源设置 GUID → 名字 → 当前值」抄下来，写进代码里的固定表。**`powercfg` 只在开发时用一次，产品代码里一行都不碰。**

一个计划里读不到的设置项会被跳过（`read_plan_settings` 只返回读得到的），所以「少了一项」比「多了一项」更隐蔽。

### 复制出来的计划默认继承源计划的名字

`PowerDuplicateScheme` 复制完，副本的 `FriendlyName` 是源计划的名字。必须紧接着调 `PowerWriteFriendlyName` 改名，否则界面上会出现两个一模一样的名字。

## 时间单位：配置里是秒，界面上是分钟

内部统一用秒（`GetLastInputInfo` 出来的就是秒，规则引擎也是秒），界面上用分钟，因为「600 秒」没人愿意填，「10 分钟」才有人填。

规则里的值另外放宽了一点：`10m`、`2h`、`1d`、`10分钟`、`2小时` 都认。解析在 `rules.parse_duration()`，**CPU 阈值不走这个函数**——百分比不是时长，`15m` 应该是无效的而不是 900。这条有专门的测试和变异守着。

## 液态玻璃

界面换成液态玻璃，按 apple-design 的材质规范做的：

- 半透明层 + `backdrop-filter: blur()` + `saturate(180%)`
- 每层上面一条亮边（`--glass-edge`），模拟光打在材料边缘
- 大面板用更强的模糊和更重的阴影，小控件用轻的
- **浅色半透明不叠在浅色半透明上**——叠了就没法读了
- 背景是三个模糊的色块（`.aurora`），玻璃背后有东西，才看得出是玻璃
- `prefers-reduced-transparency` 时全部退回不透明
- `prefers-reduced-motion` 时动画换成淡入

## 没做的部分

- **没有做工具箱。** 用户明确说不要。
- **没有做 macOS 版的全部功能。** 只做了空闲切换、游戏检测、服务器模式、规则引擎这几样基础能力。
- **计划任务安装仍用 `schtasks` / `taskkill`。** 数据获取全部改成了 API，但任务本身的创建和删除还在用命令行——改成 Task Scheduler COM 是更彻底的做法，留待后续。


## v0.3.0 升级批次（2026-11-09）

这一轮做了六件事。记下来的是实现时的取舍和踩到的坑。

### 1. 日志：实时查看 + 自动清理

- **实时**：新增 `core/logs.py` 的 `LogTailer`，按字节偏移记位置，窗口每秒问一次有没有新行。偏移量存在窗口侧不在 Python 侧——窗口关掉再开就是从头读，逻辑更简单。
- **自动清理**：配置里加 `log_max_lines` 和 `log_max_age_days`，两个都是 0 表示不限。daemon 每分钟检查一次并裁剪，窗口里的按钮也能立刻裁。
- **手动清除是截断不是删除**：daemon、agent、gui 三个进程都通过 `RotatingFileHandler` 持有这个文件，Windows 上文件一旦被 rename，句柄就指向了旧名字，继续写会写到别处。截断让所有句柄保持有效。
- **按天清理靠行首时间戳**：日志格式是 `%(asctime)s`，行首就是 `2026-11-09 08:12:33`。解析不出时间的行保留——丢行比留行更糟。
- **窗口只在日志页激活时轮询**，切走就停。没人看的时候没必要一直问。

### 2. 双击自动提权

- 提权逻辑收到 `core/elevate.py` 一处，CLI 和 GUI 都走它。
- **修掉一个真 bug**：旧代码把整个 `sys.argv` 拼成参数传给 `ShellExecuteW`，等于把可执行文件路径又当成了第一个参数传进去。现在只传 `argv[1:]`，可执行文件本身由 `ShellExecuteW` 的第一个参数负责。
- **防重入**：命令行里带 `--winsleep-elevated` 标记。提权后的副本看到这个标记就不再弹 UAC，否则用户点一次「否」就会看到提示框反复出现。
- **UAC 被拒时不闪退**：提权失败就带着普通权限继续跑，并在日志里记一笔。
- 每个参数都加引号，不只在有空格时加——统一规则比判断什么时候要加更不容易漏。

### 3. 电源计划恢复默认

- 新增 `core/planrestore.py`。
- **只有 Windows 内置计划能恢复**。用户自建的计划没有「默认版本」，接口层直接拒绝并给出理由。
- 恢复的值来自注册表里同一个计划下的 `Default*` 子键，那是 Windows 自己用的默认值。
- 恢复 AC 和 DC 两半，笔记本插电和用电池是两套设置。
- 需要管理员权限（写 `HKEY_LOCAL_MACHINE`），所以走 daemon 代办。
- **参数校验排在平台检查前面**：用户自建的计划在任何机器上都得到同一个理由，不会因为平台不同给出不同解释。

### 4. 更完整的计划控制 + 两个自有计划

- `core/ownplans.py` 里定义两套：**DSForWin 最节能** 和 **DSForWin 最高性能**。
- 做法是复制系统「平衡」计划再改写设置，这样新计划和系统里的计划结构一样，用户之后还能继续改。
- 设置涵盖显示、睡眠、休眠、唤醒定时器、混合睡眠、硬盘、处理器最小最大、冷却策略、PCIe 链路。
- 每个设置的 GUID 都对着它所属的 subgroup 核过，值用的是 Windows 自己的索引（秒 / 百分比）。

### 5. GPU 专项省电

- 新增 `core/gpu.py`。
- 做法：找 NVIDIA 显卡的引擎 GUID，往 `HKCU\Software\Microsoft\DirectX\UserGpuPreferences` 写 `2`（省电）或 `3`（最高性能）。Windows 就是从这里读驱动的偏好的。
- 负载用 `pdh.dll` 的「GPU Engine」性能计数器读。读不到就返回 -1，界面显示「未知」，不假装知道。
- **能承诺的和不能承诺的**：程序做的是告诉驱动该倾向省电，并把读回来的状态给用户看。显卡是否真进入最深睡眠由驱动决定，Windows 没有公开接口能强制这件事。所以界面上写的是「已设置」，不是「已进入睡眠」。
- 没有 NVIDIA 显卡时明确显示这一项不适用，而不是给一个假的成功。

### 6. 菜单栏常驻（通知区域）

- 新增 `core/tray.py`，用 `pystray`。菜单：打开主界面 / 立即锁屏 / 切换电源计划 / 退出。
- 新增 `core/single.py` 做**单实例**。锁是一个文件，进程号写在里面。第二个副本发现自己不是第一个，就直接退出，不会开第二个窗口。
- 锁的过期判断：文件超过 60 秒没动，**且**里面写的进程号已经不在了，才算过期并接管。被 kill 掉的进程不会把程序永久锁死。
- 登录自启用新的计划任务 `DeepSleepForWindows-Tray`（ONLOGON + /IT）。**装托盘任务失败不会让整个安装失败**——托盘要的是交互式任务，有些机器会拒绝，而 daemon 和 agent 才是真正需要的。
- 新增 CLI 子命令 `tray`。
- 依赖变化：`pystray`、`Pillow`、`pythonnet` 进 `pyproject.toml` 的运行时依赖，`main.spec` 里补 hiddenimports（`pystray._win32` 按名字加载，PyInstaller 看不见）和图标。

### 踩到的坑

- **bridge 里两个 `kind` 键**：`{"kind": "create_plan", "kind": kind}`。Python 字典字面量里重复的键后者覆盖前者，daemon 收到的是 `kind` 的值而不是 `"create_plan"`，整个功能静默失效。ruff 的 F601 抓到了它。
- **等价变异**：三个变异测试一开始抓不住，因为「变异」改出来的代码行为和原来一样。变异测试本身也要检查变异是不是真的改了行为，不然 27/27 就是假的。
- **检查脚本的 mock 要跟着加**：GUI 检查脚本只抽出被测的函数，`refresh` 调了新函数，mock 的 api 没有对应方法就会抛异常。

### 没做的部分

- **智能性能 / 智能节能**：用户明确说先不做。位置留着——计划设置可以继续扩展，进程优先级降级是另一件事。
- **显卡强制休眠**：Windows 没有这个接口，见上面第 5 条。
- **托盘图标的自定义绘制**：现在用程序自己的 `icon.png`，找不到时才画一个蓝色圆点兜底。
- **关窗口缩到托盘**：目前关窗口就是关程序，托盘是独立的 `tray` 模式。两者的整合留待后续。

## v0.3.1 修的问题（2026-11-09，在真机上查出来的）

v0.3.0 发出去之后，用户报告了四件事：托盘没有图标、后台服务说启动成功但没在运行、NVIDIA 显卡检测不到、日志一个字都没有。这一节记的是在 192.168.1.32 上查出来的原因。

### 日志为空 —— 提权标记让程序自己死了

`core/elevate.py` 的 `with_marker()` 会往命令行里塞一个 `--winsleep-elevated`。**这个标记没有注册进 argparse**。提权后的那个副本一启动就报：

```
DeepSleepForWindows: error: unrecognized arguments: --winsleep-elevated
```

然后立刻退出。于是：双击 → 弹 UAC → 提权的副本秒退 → 原进程也退 → **什么都没跑起来，日志一个字没写**。用户看到的「日志是空的」就是这个。

修法：`build_parser()` 里把 `ELEVATION_MARKER` 注册成一个 `store_true` 参数。

**这一条还顺带解释了「说启动成功但没在运行」**：程序根本没起来，所以后台当然没在运行。

### 计划任务指向一个不存在的文件

任务里写死了 exe 的路径：

```
Task To Run: "C:\Users\philia093\Desktop\DeepSleep For Windows.exe" daemon
LastTaskResult: 2147942402      ← 0x80070002 = 找不到文件
```

用户把程序换了位置，任务还指着旧地方。`schtasks /Run` 只会说「任务被请求启动」，Windows 随后因为找不到文件直接失败 —— 而旧代码把「请求被接受」当成了「启动成功」，于是界面上写着「the background service was started」，实际上什么都没发生。

修法，三处：

1. `install.task_command()` —— 任务的命令行永远从**正在运行的这个程序**取路径，不记在设置里。用户把文件挪了，重装一次任务就跟上。
2. `install.tasks_are_current()` —— 任务指向的地方跟当前程序对不上时，界面直接说「任务还指着程序原来的位置」，并给一个「重新安装任务」按钮。
3. `install.start_daemon()` 之后会**等**守护进程真的应答（最多 5 秒）；没起来就去读任务的返回码，`0x80070002` 单独给一句「找不到程序文件，多半是程序被移动过了」。

### NVIDIA 显卡检测不到 —— 读错了注册表位置

旧代码去读 `<adapter>\<entry>\Settings\GUID`。这台机器上那个 `Settings` 子键**不存在**（WinError 2），所以永远返回空，界面就一直说「这一项不适用」—— 而机器里明明有 RTX 3060。

实测这台机的注册表：`Control\Video\{B55DB269-...}\0000` 下 `ProviderName = NVIDIA`、`DriverDesc = NVIDIA GeForce RTX 3060`。按 `ProviderName` 找就对了。

**偏好值写在哪**：Windows 有两个地方。机器级是

```
HKCU\Software\Microsoft\DirectX\UserGpuPreferences\DirectXUserGlobalSettings
```

里面是分号隔开的一串设置，例如 `GpuPreference=2;SwapEffectUpgradeEnable=0;`。程序写这一项，**别的设置原样保留**（不是我们的东西，不该动）。按适配器 LUID 的那一个是给单个适配器用的。

**还有一个 bug**：判断设置名时用了 `part.lower().startswith(f"{_GPU_PREFERENCE_SETTING}=")`，左边小写了右边没小写，永远不匹配。所以偏好既读不出来、也替换不掉（只会追加一条重复的）。两个常量现在都用小写比较。

### 显卡负载读不出来 —— 两个原因

1. `ctypes.wintypes` 里**根本没有** `HQUERY` 和 `HCOUNTER`（在任何平台都没有）。`wintypes.HQUERY()` 直接 AttributeError。
2. 修好类型之后，`PdhAddEnglishCounterW` 在这台机器上**直接崩**（access violation），而且跟计数器路径无关 —— 连 `\Processor(_Total)\% Processor Time` 都崩。

所以改用 `nvidia-smi`（驱动自带，实测 5 次调用 0.24 秒，返回 `40, 16, NVIDIA GeForce RTX 3060`）。性能计数器留作第二条路。

**教训**：Windows API 的 ctypes 绑定，参数类型必须显式声明。不声明的话 ctypes 会猜，猜错就是野指针。同一个函数在别的机器上能跑，不代表这台能跑。

### 托盘 —— 任务根本没装上

`install_all()` 里托盘任务是可失败的那个（交互式任务要密码，有些机器拒绝）。这台机器上查任务，只有 Daemon 和 Agent，**没有 Tray**。所以托盘图标从来没出现过。

托盘代码本身还有两个问题：

- **「打开主界面」会把托盘自己杀掉**：它调的是 `_elevate_and_exit()`，那个函数最后是 `sys.exit(0)` —— 托盘进程直接没了。改成用 `subprocess.Popen` 起一个独立的窗口进程。
- **「退出」不退出**：菜单项只调了 `on_quit()`（那只是写一行日志），图标没停，程序留在那里。

### 界面上的缩写

电源计划的名字里写着 `DSForWin`，这是仓库名的缩写，界面上不该出现。改成 `DeepSleep 最节能` / `DeepSleep 最高性能`，界面上的标题改成「程序自带的电源计划」。

### 自动更新

v0.3.0 完全没有更新流程，而程序是单文件、还被计划任务指着 —— 用户换了位置就永远起不来。按 macOS 原版 DeepSleep 的做法补上：

- 检查 GitHub release（列出来自己挑，不用 `/releases/latest` —— 它不返回预发布）
- 下载 → 解压 → 校验压缩包里只有一个 exe 且路径不越界
- **更新器脚本写在临时目录**（要替换的文件正是会被替换掉的那个），等程序退出 → 停任务 → 旧文件**改名**而不是删除 → 放入新文件 → 任务指向新文件 → 重新启动
- 新文件放不进去就把旧的放回去 —— 宁可停在旧版本，也不能留下一个装不上的程序
- 结果写进文件，程序下次启动时读它 —— 用户重启过电脑也知道这次更新成没成

实测在真机上跑了一遍：目标文件被替换、旧文件清掉、结果文件写出 `{"status":"success","version":"v9.9.9",...}`。

**踩到的坑**：

- `echo {"status":"success"}` 里的花括号在 cmd 里会被当成语法，写出来是 `{{"status":"success"}}`。改成用 `call :label` 子程序，把参数传进去。
- `> "%RESULT%"` 里的 `>` 前面的 `1` 会被 cmd 当成重定向 stdin（`1>` 是重定向标准输入！），文件写不出来。去掉那个 `1`。
- 结果文件所在的目录可能已经没了（程序启动时才建，脚本是在程序退出后才跑的）。脚本里先 `mkdir`。
- 脚本自己的工作目录最后会被删掉，而 Windows 不允许删掉当前所在的目录。脚本开头 `cd /d "%TEMP%"`。

### v0.3.2：日志文件写不进去（真正的根因）

v0.3.1 修好了提权崩溃，但日志还是空的。继续在真机上查，这次查到了底。

**日志文件是谁建的，权限就跟谁走。** 窗口启动时是提权的（改电源计划要管理员权限），所以它建的日志文件归管理员所有：

```
C:\ProgramData\DeepSleepForWindows\winsleep.log
    NT AUTHORITY\SYSTEM      FullControl
    BUILTIN\Administrators   FullControl
    BUILTIN\Users            ReadAndExecute, Synchronize   ← 只能读
```

而**守护进程是普通用户跑的计划任务**（LogonType 是 InteractiveToken，没有管理员权限），它写不进去。同一台机器上 `winsleep.port` 是任务自己建的，权限里就有用户本人——差别只在于谁先建的。

实测证据：任务启动的守护进程写 `winsleep.port`（00:58:52）但日志一个字节都没长（`grew=0`）；用批处理手工启动的同一个 exe，日志就正常长。SSH 里 `echo >>` 追加成功，是因为**那个会话是提权的**，普通用户追加则失败。

**修法**：程序在设置日志时，先看**用户本人**（不是当前进程）有没有写权限，没有就补上。用 `icacls` 做，不用 API：

```python
icacls <log> /grant "<DOMAIN\user>:(F)"
```

**为什么不用 API**：控制访问列表的 API 是一堆结构和偏移量，写错一个不会报错，只是**什么都没发生**。这次为了修它试了 ctypes 版本，连踩四个坑：ctypes 不声明 argtypes 会猜错参数（`GetTokenInformation` 返回成功但 sid 读出来是 `None`）；`GetNamedSecurityInfoW` 的第 6 个参数是**指针到指针**不是指针到结构；`TOKEN_USER` 里的 sid 指针指向缓冲区，函数返回后缓冲区被回收，sid 就成了野指针；Windows 给回的 DACL **正好是它需要的大小**，没有空间加条目，`AddAccessAllowedAce` 直接失败（返回 0），得自己复制一份更大的出来。

四个坑每一个都是「不报错但没效果」。icacls 一条命令就做对了。

**判断依据不能用 AccessCheck**：那个问的是**当前进程**的权限。窗口是提权的（属于 Administrators），守护进程不是。要问的是「用户本人能不能写」，所以走的是 DACL 里**用户自己**的那一条，不看组成员——否则提权进程会以为自己能写，就什么也不改。

新增 `core/acl.py` 和 `tests/test_acl.py`。
