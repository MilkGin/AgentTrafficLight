# AgentTrafficLight

**一个常驻屏幕最上层的红绿灯悬浮窗，用来一眼看出 AI Agent 现在处于什么状态。**

红 = 需要你操作，黄 = 正在干活，绿 = 空闲。窗口可开启**鼠标穿透**，所以它悬浮在最上层却不会挡住你点任何东西。

> **纯 Win32 实现**：`pywin32` + `ctypes` 自绘 GDI 窗口，**没有 Tkinter 依赖**。

---

## 为什么不用 Tkinter

这是整个项目里最关键的一个技术决策。

最初用 Tkinter 实现，但 **Tk 窗口在显示 / 打包后会被自身重置扩展样式**——`WS_EX_TRANSPARENT` 和 `WM_NCHITTEST` 子类化钩子都无法稳定实现"鼠标穿透到背后程序"。

改用纯 Win32 自绘窗口（GDI），由 `WM_NCHITTEST` 返回 `HTTRANSPARENT`，穿透稳定生效，**并用探针脚本验证通过**。

另外一个互斥关系值得注意：**DWM 亚克力磨砂会接管命中测试，导致穿透失效**，所以磨砂只在「可交互（非穿透）」模式下启用，两者互斥。

---

## 灯光语义

| 颜色 | 含义 | 判定条件 |
|---|---|---|
| 🔴 红 | **需要你确认 / 已暂停** | 会话处于 `needs_input` / `paused` / `waiting` / `blocked` 等**阻塞在用户**的状态 |
| 🟡 黄 | 正在干活 | 会话处于 `working` / `streaming` / `running` / `pending` |
| 🟢 绿 | 空闲 / 已完成 | 其余状态 |

**一个踩过的坑**：`cancelled` / `aborted` / `failed` / `error` / `timeout` 这些**任务终止态不该亮红灯**——否则用户主动删掉一个未结束的任务后，红灯会一直亮着（"常红"）。现在用状态白名单把「阻塞在用户」和「任务已终止」严格分开。

---

## 功能

- **自动读取 Agent 状态**：后台每 3 秒读 CodeBuddy 会话库（`%APPDATA%\CodeBuddy CN\codebuddy-sessions.vscdb`）解析 `status` 字段
- **鼠标穿透**：开启后点击直接落到下方窗口（比如浏览器书签），不挡操作
- **DWM 亚克力磨砂**：约 78% 不透明度，与穿透模式互斥
- **仅系统托盘控制**：切色 / 自动模式 / 置顶 / 穿透 / 开机启动 / 退出 全在托盘右键菜单
- **手动模式**：托盘选色即切回手动，关掉自动读取
- **始终置顶** / **竖排三灯**（未激活两灯偏暗，当前灯带呼吸光晕）
- **开机启动**：一键写入当前用户注册表 `Run`
- **状态记忆**：重启后恢复上次的置顶 / 穿透 / 自动设置

---

## 快速开始

```bash
pip install pywin32
pythonw traffic_light.py     # 或直接双击 traffic-light/run.bat
```

**打包成独立 exe（无需 Python 环境）**：

```bash
pip install pyinstaller
pyinstaller traffic-light/AgentTrafficLight.spec
# 产物：dist/AgentTrafficLight.exe（约 6.4 MB 单文件）
```

配置与详细说明见 **[traffic-light/README.md](traffic-light/README.md)**。

---

## 依赖

- Python 3.8+ / Windows 10 或 11（磨砂玻璃依赖 DWM，低版本自动回退普通半透明）
- `pywin32` — 系统托盘、窗口样式、注册表开机启动
- `ctypes`（标准库）— GDI / User32 底层调用

**纯本地运行，无需任何网络访问。**

---

## 许可证

见 [LICENSE](LICENSE)（如未单独提供，默认保留所有权利）。
