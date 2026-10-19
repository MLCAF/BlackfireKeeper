# 黑火守望 v0.4.0

新增 Windows x64 便携版：完整解压后双击 EXE，无需另装 Python。首次启动可选择游戏 EXE，路径保存到本机；提供重新选择路径、启动失败日志与无游戏操作的离线自检。

发布附件 `BlackfireKeeper-v0.4.0-windows-x64.zip` 是便携版，GitHub 自动生成的 Source code ZIP 是源码版。不要只复制一个 EXE。详细操作见包内 `使用说明.html` 或仓库 `使用说明.md`。

构建排除本机挂机设置、冷却记录、日志及玩家截图，使用 Git HEAD 的公共默认设置；附对应源码、依赖许可和 SHA-256 清单。已有用户更新前退出旧窗口，将自己的 `config/dungeon_plan.json` 和 `config/runtime/` 复制到新解压目录。

游戏流程沿用 v0.3.3，识别阈值不变。验证方法与边界见 `docs/PORTABLE_VALIDATION.md`；离线自检不代表连续实机挂机或其他电脑已全部验证。
