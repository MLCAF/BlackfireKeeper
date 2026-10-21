# 第三方资源说明

仓库根目录的 GPL-3.0 许可证适用于本项目代码；以下资源保留各自来源与权利信息。

## 开发参考项目

开发过程中参考了以下挂机脚本，感谢其原作者及贡献者：

- [AALC · AhabAssistantLimbusCompany](https://github.com/KIYI671/AhabAssistantLimbusCompany)：Limbus Company 的 PC 端挂机助手。除作为开发参考外，本项目直接使用了本地 AALC 参考包中的 OCR 模型与字典，详见下方“OCR 模型”的来源说明。
- **BBchannel**：FGO 自动刷本辅助工具，作为开发参考。现有本地参考包没有可核实的上游仓库链接，暂按项目名称署名。

以上参考项目的致谢与具体资源的许可记录分别列示；直接使用的资源仍保留其来源及许可资料。[MaaFramework](https://github.com/MaaXYZ/MaaFramework) 是本项目实际使用的截图、识别和受控输入框架。

## Python 依赖

源码依赖由 `requirements.txt` 安装，Git 源码仓库不包含 Python 环境或依赖 DLL。v0.4.0 起的便携发布附件包含 Python、Tk、MaaFramework、NumPy、Pillow 等运行依赖，许可资料保存在包内 `third_party/bundled/`，对应项目源码和构建脚本保存在 `Source.zip`。PyInstaller 使用允许分发构建产物的 bootloader exception；相关许可资料随包保存。MaaFramework 原生依赖也保留安装包内的许可资料。游戏程序不随包分发。

## OCR 模型

`assets/model/ocr/cooldown/` 中的模型和字典来自原有 AALC 参考包的 RapidOCR 模型目录，文件名对应 PaddleOCR PP-OCRv4 中文模型。具体文件大小、原始文件名及 SHA-256 见该目录的 `SOURCE.md`；来源记录是已有本地包的复制记录，并非声称重新训练了模型。

- RapidOCR 上游：https://github.com/RapidAI/RapidOCR
- PaddleOCR 上游：https://github.com/PaddlePaddle/PaddleOCR
- 两个上游仓库的 Apache-2.0 许可证副本保存在 `third_party/licenses/`。

模型用于本地界面文字识别，运行时不调用外部 OCR 服务。

## 游戏识别模板

`assets/image/` 是从 Blackfire Crusade 游戏界面裁出的局部识别素材，用于按钮、页面和奖励判定。游戏图像及标识属于其原权利人；本项目 GPL 许可证不重新授予游戏素材的版权。仓库不包含游戏 EXE、完整玩家截图或提取的游戏脚本。
