"""Bundled release summary shown on every interactive GUI launch."""
from tkinter import messagebox
from .version import DISPLAY_NAME

UPDATE_TEXT = '''v1.0.2 本次更新：

1. 修复八本（巨龙巢穴）、九本（古老墓穴）已出现却识别不到、反复回到列表最左端的问题。
2. 增加实际 1920×991 画面的名称兼容模板，识别阈值仍为 0.95；保留导航遮挡校验、点击位置复核和冷却保护。
3. 两张实际截图回放已通过；完整战斗流程仍待用户验证。现有选本、Buff、挂机设置和冷却记录保留。
4. 请完全退出旧版主界面，再启动本版本；旧进程不会自动加载修复。

错误诊断如何上传：
在主界面切换到“挂机面板”，点击右侧“打开诊断目录”（小窗口可向下滚动）。
找到与报错时间对应的日期时间文件夹，将整个文件夹压缩为 ZIP 后上传；保留 report.json、screen.png、previous.png 等实际生成的全部文件，不要只发单个 JSON、截图或最后一行报错。
若同一次报错生成多个文件夹，请把相关文件夹一起压缩上传。“复制最新报告路径”仅用于定位最新报告，不能代替完整压缩包。

定时关机仍由 Windows 系统任务独立执行；停止 / F8、报错或关闭脚本不取消，仅显式取消才撤销。
现有挂机设置和冷却记录保留。
关闭本提示后继续使用。'''


def show_update_notice(parent):
    messagebox.showinfo(f'{DISPLAY_NAME} · 本次更新', UPDATE_TEXT, parent=parent)
