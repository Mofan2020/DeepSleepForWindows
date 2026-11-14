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


---

## 0.4.0：窗口不再要管理员权限，程序保留一份自己的副本

### 双击没窗口的真正原因

`cli._open_window()` 提权一次，`gui.app.run()` 里 `_ensure_admin()` 又提权一次。两次都失败（或者用户点了「否」）时，两条路径都返回 0，于是一个窗口都没有、一句提示也没有。

更要命的是 `bridge._elevate_and_restart()`：装服务时如果当前不是管理员，它用 `ShellExecuteW` 重启整个程序然后 `sys.exit(0)`——**窗口消失了，但 install 命令从没被执行过**。这就是「点安装并启动没反应，横幅还在」。

**改法**：窗口本来就不需要管理员权限。电源计划的改动和装任务都是守护进程（SYSTEM 身份）或一次性提权子进程做的事，窗口只管跟它们说话。

- `gui/app.py` 删掉 `_ensure_admin()`，`run()` 直接开窗口。
- `cli._open_window()` 删掉提权分支。
- `bridge._run_elevated_command(command)` 取代 `_elevate_and_restart()`：用 `ShellExecuteExW`（`SEE_MASK_NOCLOSEPROCESS`）起一个只跑 `install` / `uninstall` / `start` / `stop` / `telemetry-off` / `telemetry-on` 的子进程，`WaitForSingleObject` 等它结束，窗口留在原地。失败时把 `GetLastError` 的码和「提权被拒绝」写进结果，界面上能看见。

### 程序有一份自己的副本

计划任务里写死的路径必须是**不会变**的。以前 `_module_args()` 用的是 `sys.executable`——用户下载到 `Downloads` 就在 `Downloads`，删了下载，任务全废，而且没有任何提示（`0x80070002` 只写在任务结果里，没人会去看）。

新增 `core/bootstrap.py`：

- `installed_dir()` = `%LOCALAPPDATA%\DeepSleepForWindows\app`（`WINSLEEP_PROGRAM_DIR` 可覆盖，测试用）
- `fingerprint(path)` = 文件的 SHA-256
- `ensure_installed()`：算运行中程序的指纹 → 和副本比 → 不同就复制 → **读回来再比一次**（复制到一半、磁盘满了都会得到「文件在但不是这个程序」，只有读回来才知道）→ 写 `manifest.json`（version + sha256）
- `is_installed_copy()` / `installed_exe()`

`install._module_args()` 改成走 `bootstrap.installed_exe()`；`cli.main()` 每次启动先 `_keep_a_copy()`（失败只记日志，绝不阻止启动）。

复制是普通用户权限就能做的（`%LOCALAPPDATA%` 是自己的），所以**只有注册计划任务那一步需要管理员**。

### 任务用 XML 注册，不是命令行开关

`schtasks /Create` 的参数做不了几件事：**不能设失败重启**，不能设运行时长上限，不能设电池策略。而这些恰好就是「后台莫名消失」的直接原因——默认 `ExecutionTimeLimit` 是**三天**，到点调度器直接杀掉；崩一次就再也不回来。

`core/tasks.py` 生成完整 XML 再 `schtasks /Create /XML`：

```xml
<RestartOnFailure><Interval>PT1M</Interval><Count>10</Count></RestartOnFailure>
<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
<StartWhenAvailable>true</StartWhenAvailable>
```

身份分开：

- 守护进程：`<UserId>S-1-5-18</UserId>`（SYSTEM）+ `<RunLevel>HighestAvailable</RunLevel>` + `BootTrigger`。锁屏和未登录时也要工作。
- 代理 / 托盘：`<GroupId>S-1-5-32-545</GroupId>`（Users）+ `<RunLevel>LeastPrivilege</RunLevel>` + `InteractiveToken`。**不存密码、创建时也不要密码**——这正是以前 `/IT` 从命令行建任务会失败的原因。

XML 文件必须 UTF-16（调度器只认这个）。所以 `task-xml --write PATH` 让程序自己写文件，不让 shell 重定向——重定向会用控制台代码页。

### 装完立刻启动

`ONLOGON` 触发器等的是「登录」这个事件，而装的时候用户**早就登录了**。所以任务注册完什么都不跑，要等下次开机托盘才出现——用户看到的「菜单栏常驻还是没有」就是这么来的。

`install.start_all()` 在注册完之后对三个任务各发一次 `schtasks /Run`。

### 托盘状态怎么判断

托盘进程启动时写 `tray.alive` 心跳（跟 agent 一样），`tray_running()` 看心跳是否在 20 秒内。窗口直接显示「托盘图标已显示 / 没有起来 / 没有装上」三种状态——Windows 11 默认把托盘图标收进小箭头里，不写清楚用户根本不知道是没起来还是被收起来了。

### 遥测状态不读命令输出

`telemetry_status()` 原来解析 `sc query` 的 `RUNNING` 和 `schtasks` 的 `Disabled`——**本地化文本**，中文系统上永远匹配不到，会把运行中的服务读成没运行。

改成：

- 服务 → `advapi32` 的 `OpenSCManagerW` / `OpenServiceW` / `QueryServiceStatus` / `QueryServiceConfigW`，读 `dwCurrentState` 和 `dwStartType` 的数字。
- 任务 → `schtasks /Query /XML`，解析 XML。
- 注册表 → 本来就是 `winreg`。

**任务开关在 `<Settings><Enabled>`，不是 `<Triggers><Enabled>`**。两个都有 `Enabled`，意思完全不同：前者说这个任务能不能跑，后者说是哪个事件触发它。读错会把「运行中」读成「已禁用」。这个坑是变异测试逼出来的——我先写成 `all(所有 Enabled 都是 false)`，变异成 `any` 时测试没红，才发现逻辑本身就不对。

### 变异测试的字节码缓存坑（会导致结果骗人）

变异把 `== "false"` 换成 `!= "false"`：**长度完全一样，同一秒内写回**。Python 判断 `.pyc` 是否有效只看「mtime（秒）+ 文件大小」，两个都没变 → 它继续用变异版的字节码 → 后面的测试跑的是**磁盘上已经不存在的代码**。

表现出来就是：变异测试报「39/39 全抓到」，紧接着跑 pytest 却有三个测试红，而那三个测试单跑又是绿的。

修法：变异写入和还原之后都调 `forget_bytecode(path)` 删掉对应的 `.pyc`。

**教训**：等长的字符串变异必须清字节码缓存，否则整个变异流程的结论不可信。

### 其它

- `paths.app_dir()` 先试 `%ProgramData%` 并**实际写一个探针文件**验证可写，不可写才退回 `%LOCALAPPDATA%`。窗口不再提权之后，被管理员创建的机器级目录它写不进去，会直接崩在保存设置上。
- `selftest` 增加托盘图标能否画出来的检查——模块能 import 不等于能用，图标文件不在包里时图标就是没有。
- CI 新增两步：把三种任务 XML 真的交给调度器注册、读回、删掉；以及用临时 `WINSLEEP_PROGRAM_DIR` 跑一次，验证副本和 manifest 真的生成、第二次跑不会重写。


### 变异测试不能并行跑

两次变异同时跑会互相改同一批源码：A 记下「原始内容」→ B 也开始 → A 写入变异 → B 读到的「原始内容」是 A 的变异 → A 还原 → B 又把它改成变异。最后报出来的东西全是假的：`the snippet was not found`（其实是另一个脚本改掉了）、`NOT CAUGHT`（其实是变异的不是同一个位置）。

实际踩到的表现：后台挂了一次变异测试没管，后来又跑了一次，第二次报「38/39，任务没启动没被抓」，而单独顺序跑是「39/39」。两个结论的前提都是错的。

现在脚本用 `.mutation-lock` 互斥：拿不到锁就打印对方 pid 并 exit 3。锁里写 pid，进程还活着就等；进程没了（上次被 kill）就接管。


## 托盘图标不出现的真正原因（v0.4.1）

用户第二次报「菜单栏还是不显示」。前面 v0.3.x 修过两轮：任务没装上、ONLOGON 要等下次登录。这次不是那两个。

### 根因：pystray 把返回值丢了

`pystray/_win32.py` 的 `_message()`：

```python
win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(...))
```

返回值没接。`Shell_NotifyIcon(NIM_ADD, ...)` 返回 0 表示失败——托盘区还没建好、或者 Explorer 重启过把图标全清了。pystray 不抛、不重试、不打日志。**进程活着、心跳在写、图标就是没有。**

两个触发场景：

- 任务是 `ONLOGON` 触发的，登录过程中 Explorer 画托盘区比任务启动晚。每次登录都输这个竞态，输一次整个会话就没图标。
- Explorer 重启（崩溃或手动重启）会销毁全部托盘图标。不响应 `TaskbarCreated` 广播的程序再也回不来，pystray 不响应。

### 修法

- 加图标**之前**等托盘区存在：`FindWindowW("Shell_TrayWnd")` + `FindWindowExW(..., "TrayNotifyWnd", ...)`，最多等 60 秒，超时照常加（任务栏坏了值得看一眼，但不是不给程序的理由）。
- 起来之后用 `watch_notification_area()` 盯着：区域消失再出现时，用 `icon.visible = False` / `True` 一对赋值强制 `NIM_DELETE` + `NIM_ADD`。这是 pystray 的公开属性，没碰私有成员。只设 `True` 没用——pystray 自己认为图标还在，会短路掉。
- `FindWindow` 的 `restype` 必须声明成 `wintypes.HWND`。不声明 ctypes 按 C `int` 截断 64 位句柄，「非零」判断只是碰巧对，句柄本身不能用。

### 顺带修的：心跳只写一次

`_cmd_tray` 启动时写一次 `tray.alive`，然后 `icon.run()` 一直阻塞。`HEARTBEAT_SECONDS = 20`，所以**图标明明在，20 秒后所有问「托盘在不在」的地方都答不在**。现在由监视线程每 5 秒写一次，退出时删掉文件让状态立刻翻转。

### 没验证的

改的是 Windows 专用路径，没有真机。逻辑依据是 pystray 源码里那句丢掉返回值的 `Shell_NotifyIcon`，以及 `visible` setter 的短路行为——都能在源码里读到，但没在 Windows 上跑过。


## 变异测试的三个新坑（v0.4.1）

### 1. pytest 参数不对 → exit=4 被当成「抓到了」

第一轮变异跑完报「4/4 全抓到」，其实每一条都是 `exit=4`——pytest 的**用法错误**，因为 `--timeout` 这个参数没装插件，测试根本没跑起来。非零退出码被当成了「测试红了」。

修法：变异之前先跑一次基线，**基线必须 exit 0**，否则后面的结论全无意义。退出码要和「跑起来了但失败」区分开。

### 2. 循环终止条件依赖被变异的东西 → 挂死

几个监视线程的用例靠 `beat` 回调来 `stop.set()`。去掉心跳的变异等于同时去掉了唯一的终止条件，`watch_notification_area` 无限循环，pytest 挂到超时被杀。

教训：**循环的终止条件不能来自被测试对象内部**。现在全部改成由 `ready()` 计数置停，去掉心跳时是干净地断言失败。

### 3. 被 kill 的变异跑会留源码残留

第一次变异跑在 300 秒处被强杀，`finally` 没执行，源码里留下两条变异（`return True` 提前返回、`if False` 替掉心跳）。之后所有测试结论都不可信。

处置：kill 之后立刻 `grep` 变异特征 + `git status`。这次是手动找到两处残留逐个还原的——其中一处还原时又把 `if ready():` 套成了两层导致 IndentationError，说明**还原操作本身也要验证**（改完立刻跑测试，别只看 diff）。

脚本层面的正确做法是把「原始内容」写进 journal 文件，下次启动先对账；这个项目已有的 `.mutation-journal.json` 就是干这个的，但这次是临时写的内联脚本，绕过了它。

## 0.5.0 界面重构与临时覆盖

### 概览页：先说"为什么"

以前打开窗口第一眼是「后台服务」面板——一个大标题、两段说明和一个按钮，占掉窗口三分之一，而它几乎永远不需要被看。真正该在第一眼的信息（现在用哪个计划、为什么、空闲多久了）分散在顶栏和日志里。

现在第一页是概览：左边大字是当前计划，下面一行是**决策原因**，右边是空闲计时和一个进度条（离阈值还有多远）。后台服务收成一条，不出问题时只有一行字。

### 决策原因：`Intent.reason` 一直存在，只是没人送到界面

`decide.py` 里 `reason` 的注释写着 "A short human readable explanation, shown in the UI"，但从 daemon 到窗口这条路上，它从来没被送出去过。窗口只能显示结果，不能显示原因——而这个程序的全部工作就是做这个决定。

现在 daemon 每 tick 把当前 intent 存进 `_current_intent`（`_last_intent` 只在决定变化时更新，拿它回答会报半小时前的原因），加一个 `status` 消息把结构化的 intent 递出来，桥接层塞进 `get_state`。

reason 是英文（日志用英文），界面是中文。翻译放在界面层的 `reasonText()`，**认不出的原因原样显示**——加一条泛化文案会把"还没人教过窗口的新原因"藏起来，而那种情况恰恰最该看见。

### 临时不睡觉：存截止时间，不存开关

「保持唤醒 30 分钟」如果实现成一个布尔值 + 一个定时器，就有三个坑：窗口关了定时器没了、程序重启状态没了、定时器没触发时机器永远不睡。

所以存的是 `keep_awake_until`（绝对时间戳）。daemon 每 tick 只做一件事：跟当前时间比一下。过期自然失效，没有任何需要清理的状态。关窗口、杀进程、重启机器都不影响这个承诺。

daemon 每 tick 重读配置，所以窗口只写配置，不需要走 IPC。

计划仍然由空闲逻辑决定——覆盖只管"别睡"，不管"用哪个计划"。否则一次覆盖会让机器整晚停在高性能。

### 规则：一直支持多条件，只是界面只给了一个

`Rule.conditions` 是元组，引擎是"全部成立才触发"，但界面只构建一个条件、提交 `[{kind, value}]`。所以多条件是纯界面改动，没有配置迁移。

现在条件行可以加任意多条，提交时读所有填了的行（空行跳过，不提交空字符串让 daemon 去猜）。优先级也放到了界面上——两条规则同时命中时靠它排序，藏起来会让顺序看起来是随机的。

### 顺带修掉的

**`.sheet-backdrop` 被截断。** 上一版为了修「弹窗一进来就显示」加了 `.sheet-backdrop[hidden] { display: none !important; }`，但插在了 `.sheet-backdrop` 的**声明块内部**：

```css
.sheet-backdrop {
  position: fixed; inset: 0; z-index: 40; display: flex;
.sheet-backdrop[hidden] { display: none !important; }
  align-items: center;   /* 到这里已经是新的顶层规则，解析错误被丢弃 */
  ...
```

后半段（居中、遮罩、背景、动画）全部变成解析错误被丢掉，进程选择弹窗没有遮罩也不居中。现在拆成两条独立规则。

**动作下拉一开始是空的。** `buildActionValue()` 在 `wire()` 里调用，那时 `state.plans` 还没到，所以"切换电源计划"的计划下拉在冷启动时是空的，直到用户切换一次动作类型。加 `syncActionPlanOptions()`，每次 refresh 补一次，并保留用户已经选中的值。

**单位重复。** 计划的设置项里下拉标签已经写了「10 分钟」，右边又跟一个「分钟」。有选项时不再显示单位，只有数字输入框才显示。

### 没做的与原因

- **规则不能在界面上编辑**，只能删了重加。编辑要处理"改到一半"的状态和取消，比加一条复杂得多，这一版先不动。
- **托盘菜单没加"暂停守护"**。想了一下它和"保持唤醒"语义重叠——用户说的通常是"别切我的计划一会儿"，那是第三个概念（临时锁住计划），不是保持唤醒。硬塞进这个菜单会让人分不清。留到下一版单独设计。
- **日志不能导出**。加了搜索和级别过滤，导出要决定格式和落盘位置，不急。
- **没有真实的 Windows 机器验证**。托盘菜单、电源计划读写、遥测开关都只能在 Windows 上验。自动化测试跑在 macOS 上（假 pystray、假 DOM），验证的是逻辑不是系统调用。


## 0.5.0 之后修的四条（真机反馈）

### OverflowError: int too long to convert

提权安装走 `ShellExecuteExW` + `SEE_MASK_NOCLOSEPROCESS`，拿到 `hProcess` 之后
`WaitForSingleObject(hProcess, ...)`。听起来天经地义，错在两处：

1. `info.hProcess` 是 `c_void_p` 字段，从 ctypes 结构体读出来是**普通 Python
   int**——64 位句柄能到 1e18；
2. `WaitForSingleObject` / `CloseHandle` 没声明 `argtypes`，ctypes 默认按 C 的
   `int` 处理，于是第一个参数直接抛 `OverflowError: int too long to convert`。

**所有拿句柄做参数的 Win32 调用都要显式声明 `argtypes = [c_void_p, ...]`。**
不声明的默认行为在 32 位上刚好能用，64 位上必炸——这也是这类 bug 只在用户
机器上出现的原因。

### 分组标题显示成裸 GUID

`app.js` 的 `GROUP_LABELS` 用 `SUB_VIDEO` 这种符号名当 key，而设置项带回来的
`row.group` 是**组 GUID 字符串**（`7516b95f-f776-...`）。查不到就落到
`|| row.group`，界面上把 GUID 原样显示出来。

改成 GUID 当键，十个组全补上；认不出的组显示「其他设置」——分组名缺失是程序
的事，不该让用户去读 GUID。

**这类错误靠人眼对账靠不住**（两边一个是符号名一个是 GUID，看着都像标识符），
所以加了一条 pytest：读 `powersettings.GROUPS` 和 `app.js`，逐组核对有没有标题。

### 换计划时下面不刷新

下拉框的 option value 原来是**计划名字**：

- Windows 允许重名（复制「平衡」就有了两个「平衡」），两个同名计划在框里分不开；
- 反过来用 `plans.indexOf(name)` 找回 GUID，重名时永远返回第一个。

于是「切到第二个同名计划」这个操作在界面上等于没反应。改成 value 是 GUID、
显示是名字，并且列表没变就不重建（`refresh()` 5 秒一次，重建会把用户正打开着
的下拉框关掉、选中项也会跳）。

### 托盘不出现

`install_tray_task()` 只注册了**登录时触发**的计划任务；`start_tray()` 写好了
却没有一个调用点。所以装完之后，只要不注销重登，图标永远不会出现。

现在窗口启动时调 `ensure_tray_running()`：已经在跑就返回，没跑就先注册任务再
`schtasks /Run`。托盘进程自己加单例锁（`tray.lock`），避免窗口和计划任务同时
拉起来变成两个图标。

**教训**：写了「启动某件事」的函数，一定要有调用点。`start_tray` 这种名字让人
以为它在某处被调用了，实际上 grep 一下才发现是死代码。
## 0.5.2：遥测的溢出，和托盘真正的归属

### 遥测状态一直报 OverflowError

`telemetry._service_state()` 用 `OpenSCManagerW` 拿一个 SC_HANDLE 回来，
接着 `OpenServiceW` / `QueryServiceStatus` / `QueryServiceConfigW` /
`CloseServiceHandle` 全都没声明 `argtypes`。句柄从 ctypes 读出来是普通
Python int，不声明就按 C 的 int 处理，于是 `argument 1: OverflowError:
int too long to convert`。

最阴的一处是 `CloseServiceHandle` 在 `finally` 里。finally 抛出的异常会
盖掉前面所有正常返回值，所以整个「读遥测状态」变成一个错误，界面上就只
剩那行报错，看起来像查询本身坏了。

补了签名，并把关句柄包成 `_close_service()`：关句柄这件事永远不许抛。

### 这一类 bug 不再靠人记

同一个形状已经出现两次（提权那条、这条）。新增
`tests/test_win32_and_tray_ownership.py`：逐个模块检查所有拿句柄当参数的
Win32 调用有没有声明 `argtypes`。这条测试当场又揪出三处漏的
（`OpenProcessToken`、`AdjustTokenPrivileges`、`ShellExecuteExW`）。

### 托盘：从「另一个进程」改成「窗口自己的图标」

以前托盘是独立的计划任务进程，只在登录那一刻启动：

- 装完之后不注销重登，图标永远不出现；
- 就算起来了，它跟窗口是两个进程，「打开主界面」只能再拉一个；
- 登录时它自己起来，跟窗口抢通知区域。

现在跟窗口同一个进程（跟 SDM 一样的做法）：

- `tray.start_icon()` 起完就返回，不阻塞窗口的消息循环；
- 关窗口返回 `False` 取消关闭，把窗口 `hide()` 起来，图标留着；
- 托盘「打开主界面」把窗口 `show()` 回来，不再开新进程；
- 退出走托盘菜单，`webview.start()` 返回后把图标摘掉；
- 启动时 `retire_tray_task()` 把老版本留下的任务删掉、把残留进程杀干净。

`tray_installed()` 现在只回答「有没有老的遗留任务」，不再代表图标在不在。
## 0.5.3：托盘为什么从来没出现过，以及电源设置面板

### 托盘：pystray 数参数，多一个就把函数本身抛出来

真机日志：

```
the notification area icon is not available:
<function build_menu.<locals>.<listcomp>.<lambda> at 0x...>
```

pystray 的 `MenuItem._assert_action` 会读 `action.__code__.co_argcount`：
0 个参数自动包一层、1 个包一层、2 个原样用，**3 个以上直接
`raise ValueError(action)`**——抛的是函数对象本身，所以那句日志里除了
一串 lambda repr 什么都没有。

而我们的写法正好是三个：

```python
lambda _icon=None, _item=None, minutes=minutes: on_override(minutes)
```

把参数写成默认值也算进 `co_argcount`，所以它看着能接两个参数、实际数出来
是三个。改成闭包 `_callback(handler, argument)`，签名固定两个。

**这一条也解释了为什么 0.5.1 和 0.5.2 的托盘都没出现过**：不是进程没起，
是菜单一构造就抛，`start_icon` 直接返回 None。而且异常信息里没有一句
人话，所以两次都被当成「托盘机制没写对」。

测试用了一个模仿 pystray 这条规矩的假 `MenuItem`（`co_argcount > 2` 就
`ValueError(action)`），再犯会当场红。

### 电源设置面板：读失败时一片空白

`renderPlanSettings()` 先把 `box.innerHTML = ''`，然后 `await
api().plan_settings(guid)`。对面抛出时整个函数中断——面板已经清空，
不回填、不提示，用户看到的就是「点哪个计划下面都不变」。

现在两条：出错就把原因写在面板里；成功时第一行写「正在编辑：<计划名>」，
换计划会跟着变，一眼知道刷没刷新。

后端也记一行 `plan settings for <名字> (<guid>): N 行`。读成 0 行和读成
旧值，修法完全不同，光看界面分不出来。

### 双击没反应：第二份程序根本不问第一份

现场日志里 6 次 `there is already a window; this copy gives up`，
没有任何异常。根因是 `gui/app.py` 拿到锁就 `return 0`——`core/single.py`
的文件头写着「asks the other one to come forward」，但代码里没有这条道。

现在：第二份通过回环端口（`core/windowlink.py`，端口写进
`app_dir()/window.port`）请第一份 `window.show()` + `restore()`；
没人应答且锁的主人已经不在了，就接管锁自己开窗口。主人还活着但不应答
时不开第二个窗口——两个窗口会互相覆盖设置，比开不出来更糟。

锁文件从此写两行：pid 和第二行进程启动时间。只有 pid 时，被强杀的
进程留下的锁在 pid 被系统复用之后会永远「活着」，程序就再也起不来。
现在主人已死立刻算陈旧（原来要先等 60 秒才看 pid）。daemon 也不再在
启动时无条件删锁——那是**活着的**窗口的锁。

### 托盘图标：pystray 把 NIM_ADD 的返回值丢掉了

`Shell_NotifyIcon(NIM_ADD)` 失败时返回 0，pystray 丢掉不管，于是「图标
没加上」和「图标好好的」在程序里长得一模一样。日志里那句「the icon is
in the notification area」是在 `run_detached()` 之后打的无条件日志，不算数
（现在改成「handed to」）。

查图标的办法：`Shell_NotifyIconGetRect`（winapi.notify_icon_present）。
**pystray 的图标 uID 永远是 0**——它拼 `NOTIFYICONDATAW` 时传的是
`hID=id(self)`，而字段叫 `uID`，ctypes 把陌生关键字当普通属性收下，
真正的字段保持默认值。所以查的时候必须用 uid=0，按 `id()` 查永远查不到。

看门狗每拍多问一句「图标还在吗」，不在就 `visible=False/True` 补一次。
Explorer 没重启但图标被丢掉，原来那段「区域消失又回来」的判断永远不触发。

真机实测（192.168.1.32，Win11 build 26200）：图标其实一直都在，只是在
Win11 的「隐藏的图标」溢出区里（`HKCU\Control Panel\NotifyIconSettings`
里 UID=0、无 IsPromoted）。所以「图标自己没了」有一半是溢出区的观感问题，
而双击打不开是实打实的代码 bug。跨进程验证过 `Shell_NotifyIconGetRect`
能读到别的进程的图标（自建图标 PRESENT，删掉后 MISSING）。
