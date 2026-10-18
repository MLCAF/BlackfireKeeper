# 黑火守望（BlackfireKeeper）

首次公开测试版：**黑火守望 v0.1.0**。

Blackfire Crusade 的 Windows 自动化助手，基于 BK2：用只读内存监视角色状态、地下城冷却与体力，用 MaaFramework 识别游戏界面并执行操作。

## 功能

- **地下城轮换**：从左向右识别一本至六本，只刷勾选的副本，每个副本独立选择经验或伤害 Buff；冷却期间等待。
- **体力管理**：实时显示体力、制作进度及可领取数量。UI 可设置等待阈值（5–320）；低于阈值不进入新副本，补充后自动继续。
- **免费体力**：每 30 分钟检查一次，按“面包 → 收集食物 → 奖励确认 → 关闭工坊”处理；以页面和内存变化确认结果。
- **免费钻石**：按游戏冷却安排领取，优先于体力和新副本；正在刷本时等当前副本结束，再回城镇领取并关闭商店。
- **战败处理**：每场战斗最多重新开始 3 次；仍失败则放弃冒险，确认退出后跳过该副本，继续其他勾选副本及钻石、体力领取。全部跳过后仍执行领取任务；重新开始挂机清空跳过集合。
- **桌面 UI**：连接、选本、Buff、体力阈值、日志、统计和诊断入口。F8 停止输入，内存监视保留。
- **错误诊断**：状态超时、有限重试、后继页面确认、输入互斥锁，以及自动保存的 JSON 报告和故障截图。

代码不写游戏内存，不修改冷却时间。七本暂缺入口模板，保持不可选。

## 安装

需要 **Windows x64、Python 3.12 x64（含 Tkinter 和 `py` 启动器）**，以及自行安装的 Blackfire Crusade。项目不包含游戏程序。

推荐把仓库放到游戏的 `edit` 文件夹下：

```text
Blackfire Crusade/
├── Blackfire Crusade.exe
└── edit/
    └── BlackfireKeeper/
        ├── setup.ps1
        ├── start.cmd
        └── bk2/
```

在 PowerShell 中进入 `edit`，执行：

```powershell
git clone https://github.com/MLCAF/BlackfireKeeper.git
cd BlackfireKeeper
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

安装器只创建项目自己的 `.venv` 并安装 `requirements.txt`，不依赖旧 BKCrusade 的环境。也可以手动安装：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

放在其他位置时，在启动前指定游戏路径：

```powershell
$env:BLACKFIRE_EXE = 'D:\Games\Blackfire Crusade\Blackfire Crusade.exe'
.\start.cmd
```

该环境变量只影响当前终端及其启动的程序；以后从该终端启动，或自行在 Windows 用户环境变量中保存路径。

## 使用

1. 打开游戏，登录后停在主界面或地下城列表；先完成游戏里尚未结束的副本。
2. 双击 `start.cmd`，或 `打开挂机界面.vbs`；点击 **连接游戏 · 只读**。首次连接通常需要十几秒。
3. 勾选副本、选择 Buff、设置体力阈值和自动领取选项，再保存设置。
4. 点击 **开始挂机**。低体力和冷却等待期间仍处理到期的免费奖励。
5. 点击 **停止 / F8** 或按 F8 停止；修改设置后可以重新启动。关闭窗口会停止监视和挂机。

随首版发布的预设为：三至六本、体力阈值 150、自动领取钻石和体力开启。它们只是可编辑的初始设置，请按自己的进度选择。保存位置为 `config/dungeon_plan.json`。

更新代码后需要重启应用。保持游戏窗口可截图且尺寸稳定；不要让多个挂机同时控制同一游戏。领取任务只在当前挂机运行期间执行，关闭应用后不会继续。

## 版本与限制

程序名称和版本统一定义在 [`bk2/version.py`](bk2/version.py)，UI 标题、界面顶部、命令行和两类错误诊断均读取这一处定义。诊断中的 `application.version` 是程序版本，`application.tag` 对应 Git 标签；原有顶层 `version` 仍表示报告格式版本。旧诊断保持原样，不追补版本。

```powershell
# 只显示程序版本，不连接游戏
.\.venv\Scripts\python.exe -X utf8 -m bk2 --version
```

版本递增约定：`v0.1.1` 一类补丁号用于修复识别失败、卡住等问题；`v0.2.0` 一类次版本用于新增功能；核心功能和安装流程达到稳定发布标准后再使用 `v1.0.0`。每次发布更新版本定义，Git 标签与代码版本一致，已经发布的标签不覆盖。CI 会检查标签与代码是否一致。当前仍为公开测试版，版本号不代表全部游戏场景都已验证。

当前内存布局仅核验过 SHA-256 为下列值的游戏 EXE：

```text
06efee8f6cfb4953cc842404b2518b01b3536353c03115687475e0423ea7e7a9
```

游戏更新导致哈希不符时，程序拒绝使用旧布局。内存读取的是客户端缓存；读取失效或超过 3 秒未更新会停止自动操作。界面语言和缩放需要与中文模板匹配。默认截图为 FramePool、输入为 PostMessage。

已包含祭坛 Buff、钻石奖励确认与商店退出、短暂黑帧、战败重试和小字双排战利品“继续”等修复。相关逻辑在 `bk2/` 中，`vendor_core/` 是从 BKCrusade 移植的基础执行层。

2026-10-18 补充修复六本选本超时：改用底部已选中的地下城导航校验列表，并统一列表 OCR 和返回确认。详见 [修复说明](docs/LIST_GUARD_FIX_20261018.md)。

## 检查与测试

```powershell
# 核验游戏版本、依赖和模板，不点击游戏
.\.venv\Scripts\python.exe -X utf8 -m bk2 check

# 只读观察 10 秒
.\.venv\Scripts\python.exe -X utf8 -m bk2 observe --seconds 10

# 离线测试：模拟内存、调度和输入，不操作游戏
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -t . -v
```

公开仓库不附带玩家完整截图，因此相应截图回放用例明确显示为 `skipped`。它们不计为通过；详见 [测试说明](tests/README.md) 和 [发布验证记录](docs/VALIDATION.md)。GitHub Actions 使用 Windows 与 Python 3.12 运行公开测试集。

## 目录

| 目录 | 内容 |
|---|---|
| `bk2/` | 内存读取、调度、恢复流程和 GUI |
| `vendor_core/` | 识别、输入、状态机和基础调度 |
| `assets/` | 界面局部识别模板及 OCR 模型 |
| `config/` | 可编辑计划和流程配置 |
| `tests/` | 单元测试及可选的本地截图回放代码 |
| `logs/` | 运行时自动创建的日志与错误诊断，不提交 |
| `config/runtime/` | 本机冷却与领取计时，不提交 |

诊断保存到 `logs/diagnostics/`，包含错误阶段、状态历史、匹配信息、最后有效快照和可用的故障截图。排错时先检查报告及截图中的个人信息，再选择需要分享的部分。

仓库保留原有 [GPL-3.0 许可证](LICENSE)。随附第三方资源来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
