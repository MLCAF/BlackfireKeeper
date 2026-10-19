# v0.4.1 便携版构建与验证边界

发布目标为 Windows x64，Python 3.12 / PyInstaller 6.22.3。双入口共享 `_internal` 依赖目录：`BlackfireKeeper.exe` 打开界面，`BlackfireKeeper-cli.exe` 提供命令行。

资源、设置和日志定位到 EXE 所在目录，不依赖启动时工作目录或开发者路径。首次选择游戏后保存在本机 `config/runtime/installation.json`。启动器不自动连接或操作游戏。

构建白名单只取 Git 已追踪文件，挂机计划读取 HEAD 中的公共默认值。运行记录、日志、玩家完整截图、虚拟环境不进入发布包。包内含对应源码、依赖许可与 SHA-256 清单；本机原有设置不被改写。

验证流程：

1. 运行完整公开离线回归测试，私有截图测试明确跳过。
2. 构建真实 EXE，完整解压 ZIP 到另一个带中文和空格的目录。
3. 清理该进程的 Python 路径变量，从无关工作目录调用 EXE 输出版本并执行 `--self-test`。
4. 自检实际加载 MaaFramework 和 MaaToolkit DLL，执行局部模板匹配、合成计时文字的 OCR，并创建 Tk 挂机面板。自检控制器禁止截图游戏及点击，面板没有连接游戏会话。
5. 检查发布 ZIP 未包含本机配置、运行状态或诊断；核对本机受保护文件及 Git 差异。

源测试日志和打包验证记录保存在维护机 `evidence/release_v0.4.1` 与 `logs`，不公开上传玩家信息。每次发布完成情况以维护者给出的实际测试结果为准。用户可以运行 `离线自检.cmd` 生成本机 `logs/portable-self-test.json`。

验证边界：同机换目录、限制 PATH 后运行并不等于全新 Windows 虚拟机验证；仍需收集其他电脑反馈。自检不测试游戏内存连接、真实窗口截图、游戏输入或连续刷本。本次新增的是分发与启动能力，游戏流程沿用 v0.3.3 并包含 v0.4.1 完成冒险识别修复，只有体力领取启用通用动作保护，识别阈值不变。

实现依据：[PyInstaller 运行目录文档](https://pyinstaller.org/en/stable/runtime-information.html)及[目录式分发说明](https://pyinstaller.org/en/stable/operating-mode.html)。
