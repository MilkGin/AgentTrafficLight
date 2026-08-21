# Agent 工作状态红绿灯（纯 Win32 版）

一个常驻屏幕最上层的磨砂玻璃悬浮窗，用红 / 黄 / 绿三种颜色直观展示 Agent 的工作状态。窗口半透明、可开启鼠标穿透（点击直接落到背后程序，不挡操作），所有控制通过系统托盘右键菜单完成，支持开机自启。

> 纯 Windows 实现：使用 `pywin32` + `ctypes` 自绘 GDI 窗口（**无 Tkinter 依赖**）。
> 之所以不用 Tkinter，是因为 Tk 窗口在打包 / 显示后会被自身重置扩展样式，
> 无论是 `WS_EX_TRANSPARENT` 还是 `WM_NCHITTEST` 子类化都无法稳定实现鼠标穿透；
> 纯 Win32 窗口配合 `WM_NCHITTEST` 返回 `HTTRANSPARENT` 可稳定穿透。

## 灯光语义

| 颜色 | 含义 | 说明 |
| ---- | ---- | ---- |
| 🔴 红 | **需确认 / 暂停，需要用户操作** | 存在 `failed / error / aborted / paused / needs_input` 等会话 |
| 🟡 黄 | 忙碌（工作中） | 存在 `working / streaming / running` 等会话 |
| 🟢 绿 | 空闲 / 正常 | 全部会话空闲或已完成 |

## 功能

- **接入 CodeBuddy 读取状态（自动模式，默认开启）**
  - 程序后台每 3 秒读取 CodeBuddy 会话数据库
    `（%APPDATA%\CodeBuddy CN\codebuddy-sessions.vscdb）`，
    解析各会话的 `status` 字段自动判定红绿灯。
  - 窗口标题与托盘提示实时显示来源，如「CodeBuddy: 1 个会话进行中」。
- **磨砂玻璃材质 + 半透明**：窗口采用 Windows DWM 亚克力（Acrylic）磨砂玻璃效果，整体约 78% 不透明度。
  - 注意：DWM 亚克力会接管命中测试，因此**磨砂仅在「可交互（非穿透）」模式下启用**，穿透模式自动关闭磨砂。
- **鼠标穿透（点击穿透到背后程序）**：开启后点击会直接落到下方窗口（如浏览器书签），红绿灯始终"悬浮"但不挡操作。
- **仅系统托盘控制**：所有操作（切换红黄绿 / 自动读取 / 置顶 / 鼠标穿透 / 开机启动 / 关于 / 退出）均在**系统托盘右键菜单**中进行。
- **手动模式**：在托盘菜单选择状态即切回手动控制（关闭自动）。
- **始终置顶**：窗口始终显示在所有窗口最上层（托盘菜单可关闭）。
- **竖排三灯**：红(上) / 黄(中) / 绿(下)，未激活的两灯偏暗，当前灯带呼吸光晕。
- **开机启动**：一键注册到 Windows 当前用户启动项（注册表 `Run`）。
- **状态记忆**：关闭后重新打开会恢复上次的状态、置顶、穿透与自动模式设置。

## 运行方式

### 直接运行（需 Python 环境）
```bash
pip install pywin32
pythonw traffic_light.py
```

### 打包成独立 exe（推荐，无 Python 也能用）
```bash
pip install pyinstaller
pyinstaller AgentTrafficLight.spec        # 无控制台窗口版
# 或 pyinstaller AgentTrafficLight_console.spec  # 带控制台（调试用）
```
生成的 `dist\AgentTrafficLight.exe` 双击即可运行，可放入开机启动文件夹或直接使用托盘菜单「开机启动」开关。

> 说明：程序使用 `pythonw` 运行（无控制台窗口）。磨砂玻璃需 Windows 10/11 支持；低版本系统会自动回退为普通半透明。

## 配置

配置文件位于：
```
%APPDATA%\AgentTrafficLight\config.json
```
内容示例：
```json
{
  "auto_mode": true,
  "penetrate": true,
  "always_on_top": true,
  "state": "green",
  "startup": false
}
```

| 字段 | 含义 |
| ---- | ---- |
| `auto_mode` | 是否自动读取 CodeBuddy 状态 |
| `penetrate` | 鼠标穿透（true = 点击落到背后程序） |
| `always_on_top` | 始终置顶 |
| `state` | 手动模式下的状态 `green/yellow/red` |
| `startup` | 开机启动 |

## 开机启动的两种开启方式

1. 软件内：右键系统托盘图标 → 勾选「开机启动」
2. 手动：把 `AgentTrafficLight.exe` 快捷方式放进 `shell:startup` 文件夹

## 依赖

- Python 3.8+
- `pywin32`（仅 Windows，用于系统托盘、窗口样式与注册表开机启动）
- `ctypes`（Python 标准库，用于 GDI / User32 底层调用）

无需任何网络访问，纯本地运行。

