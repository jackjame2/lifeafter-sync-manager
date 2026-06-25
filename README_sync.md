# Game Window Synchronizer — 游戏窗口同步器

基于 [WowOpenBox](WowOpenBox/) 的窗口管理思路，专为**《明日之后》PC 版**多窗口同步而设计。搭配 KeymouseGo 录制与回放脚本，实现多沙盒窗口的按键同步管理。

## 核心原理：前台置顶轮询发送（Foreground Polling）

《明日之后》PC 版对底层 RawInput 和窗口激活状态有要求——后台窗口通常不响应 SendInput。本工具采用**前台轮询**策略：

`
用户按键
  → 当前活动窗口自然接收到按键
  → 同步器依次将每个非活动窗口置顶（SetForegroundWindow）
  → 向该窗口发送相同按键（SendInput）
  → 短暂间隔后切换到下一个窗口
  → 最后将焦点归还给原始窗口
`

**两种发送模式**（可在 config.json 中切换）：

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| foreground_polling | 置顶窗口 → SendInput，焦点轮询，按键发完后归还焦点 | 默认模式，兼容性最好 |
| post_message | 直接向窗口句柄 PostMessage（WM_KEYDOWN/WM_KEYUP） | 部分游戏后台也接受消息时使用，无焦点闪烁 |

## 文件说明

`
New project 2/
├── game_sync.py              # 同步器主程序
├── license_client.py         # 卡密验证客户端
├── config.json               # 配置文件
├── run_sync.bat              # 启动脚本（管理员权限推荐）
├── download_keymousego.ps1   # KeymouseGo 下载脚本
├── KeymouseGo_v5_2_1-win.exe # 键盘鼠标录制工具（需下载，不打包在 Git 中）
├── license-server/           # Cloudflare 卡密管理后台
│   ├── src/index.js          # Worker API + 管理面板
│   ├── migrations/           # D1 数据库迁移
│   └── README_DEPLOY.md      # 部署指南
├── WowOpenBox/               # 开源多窗口管理器（窗口布局/焦点切换参考）
└── README_sync.md            # 本文件
`

## 快速开始

### 1. 首次使用：下载 KeymouseGo

如果目录下没有 KeymouseGo_v5_2_1-win.exe，双击 run_sync.bat 会自动下载。

或手动执行：
`powershell
powershell -ExecutionPolicy Bypass -File download_keymousego.ps1
`

也可以从官方 GitHub 手动下载：
https://github.com/taojy123/KeymouseGo/releases

### 2. 确认游戏进程名

打开《明日之后》后，在 config.json 中确认进程名：

`json
{
  "process_name": "LifeAfter.exe"
}
`

如果进程名不同（例如通过模拟器运行），修改为实际进程名。

### 3. 启动同步器

双击 run_sync.bat（右键 → 以管理员身份运行，以获得更好的窗口置顶权限）。

或命令行：
`powershell
python game_sync.py
`

### 4. 操作热键

| 热键 | 功能 |
|------|------|
| Ctrl+Shift+S | 开启/关闭同步 |
| Ctrl+Shift+R | 重新扫描游戏窗口 |
| Ctrl+Shift+L | 平铺排列窗口（自动网格布局） |
| Ctrl+Shift+Q | 退出程序 |

### 5. 搭配 KeymouseGo 使用

典型工作流：

1. 启动《明日之后》的所有沙盒窗口
2. 启动本同步器 → 按 Ctrl+Shift+S 开启同步
3. 启动 KeymouseGo → 加载已录制的脚本
4. 将鼠标放在主窗口内 → 在 KeymouseGo 中点击播放
5. KeymouseGo 的按键回放会被同步器捕获并转发到所有窗口
6. 操作完成后按 Ctrl+Shift+S 关闭同步

> 建议：在 KeymouseGo 中录制脚本时，先在主窗口完成所有动作录制，回放时同步器会自动转发到其他窗口。

## 卡密管理

本工具集成了 Cloudflare 卡密验证系统。首次运行需要输入有效的卡密。

管理员部署和生成卡密请参考 license-server/README_DEPLOY.md。

## 配置说明

`jsonc
{
  // ── 窗口检测 ──
  "process_name": "LifeAfter.exe",          // 游戏的进程名
  "window_title_pattern": "",               // 窗口标题过滤（留空=不过滤）

  // ── 同步模式 ──
  "sync_mode": "foreground_polling",        // "foreground_polling" 或 "post_message"

  // ── 时间参数（毫秒）──
  "poll_interval_ms": 12,                   // 切换窗口之间的间隔
  "activate_delay_ms": 8,                   // 置顶窗口后等待激活的时间
  "key_hold_ms": 20,                        // 按键按下的持续时间
  "return_focus": true,                     // 发送完是否归还焦点
  "return_focus_delay_ms": 30,              // 归还焦点前的等待时间

  // ── 热键 ──
  "sync_hotkey": "ctrl+shift+s",
  "quit_hotkey": "ctrl+shift+q",
  "layout_hotkey": "ctrl+shift+l",
  "refresh_windows_hotkey": "ctrl+shift+r",

  // ── 按键过滤 ──
  "sync_enabled_keys": "all",               // "all" 表示同步所有按键
                                            // 也可指定列表：["e", "w", "a", "s", "d"]

  // ── 鼠标同步（默认关闭）──
  "sync_mouse": false,
  "sync_mouse_clicks": false,
  "sync_mouse_movement": false
}
`

## 常见问题

**Q: 按键没有同步到其他窗口？**
- 确认游戏窗口标题包含在 window_title_pattern 中（或留空）
- 尝试增大 poll_interval_ms 和 key_hold_ms
- 尝试切换到 post_message 模式

**Q: 窗口焦点来回跳动？**
- 这是前台轮询模式的正常现象。焦点会在所有窗口间快速切换后归还主窗口
- 如果跳动影响操作，可尝试 post_message 模式（如果游戏支持后台消息）

**Q: 找不到游戏窗口？**
- 确认游戏已启动
- 确认 process_name 与实际进程名一致（通过任务管理器查看）
- 按 Ctrl+Shift+R 重新扫描

**Q: 需要管理员权限吗？**
- 不是必须，但建议以管理员身份运行，以获得更可靠的窗口置顶行为

**Q: KeymouseGo 为什么不打包在项目中？**
- KeymouseGo 的 exe 文件较大（~57MB），不适合直接上传到 Git
- run_sync.bat 启动时会自动检测并提示下载
