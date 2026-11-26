---
name: buddy-checkin
description: WorkBuddy 桌面端「Buddy加油站」每日签到领积分的 UI 自动化技能。当用户要求签到、领积分、加油站签到、领取每日积分（如"帮我签到""领积分""今天积分领了吗""加油站签到"）时使用本技能。通过纯 ctypes Win32 自动化完成：激活窗口 → 轮播卡片确认 → 点击领取 → 菜单路径复检。仅限 Windows + WorkBuddy 桌面端已登录场景。
agent_created: true
---

# Buddy 加油站自动签到

## Overview

本技能通过已验证的 `scripts/checkin_auto.py`（纯 ctypes，无第三方依赖）自动完成 WorkBuddy 桌面端左下角「Buddy加油站」卡片的每日积分领取。脚本内置轮播防护、按钮状态判定、领取后复检三道防线。

## 使用流程

### 第 1 步：确认前提

- Windows 系统，WorkBuddy 桌面端已登录且进程存活（`WorkBuddy.exe`）
- 屏幕处于可交互状态（锁屏下 SendInput 无效；仅显示器关闭不影响）

### 第 2 步：先 dry-run 确认状态（不点击）

```bash
python "<skill_dir>/scripts/checkin_auto.py"
```

解读输出：
- `卡片标题 SAD = N (阈值 15)` — SAD≤15 表示轮播已停在「Buddy加油站」卡片；>15 时脚本自动点轮播圆点切回，失败则等待轮播自动转回（兜底 150s）
- `按钮区域平均亮度 = N (阈值 100)` — >100 为「立即领取」可领；≤100 为「今日已领取」直接结束
- dry-run 结束于 `dry-run 模式, 未点击`

### 第 3 步：真实领取

dry-run 显示「立即领取 -> 可领取」后执行：

```bash
python "<skill_dir>/scripts/checkin_auto.py" --run
```

成功标志：`SUCCESS: 领取完成, 卡片显示 今日已领取`。脚本会自动走 头像→菜单→Buddy加油站 路径重新唤出卡片并复检。

可选参数 `--menu-path`：在领取前额外走一遍 用户原始四步流程（点头像弹菜单→点 Buddy加油站 菜单项），一般无需使用。

## 关键机制（勿绕过）

1. **卡片必须先确认再点按钮**：左下角是 3 张轮播（加油站/去邀约/做任务赢积分），各卡按钮位置相同。「立即前往」按钮也是亮色，只测亮度会误点其他卡片。脚本用标题区域灰度模板 SAD（阈值 15，加油站卡=0、其他卡≈43，跨天稳定）确认卡片。
2. **轮播圆点可点击但热区极小（约 5px）**：未激活圆点 1 中心约 (31,1319)（窗口 rect=(-8,-8) 时）。脚本用 5 个候选坐标依次尝试。
3. **复检必须走菜单路径**：领取成功后轮播池会变化（实测 3 圆点变 2、卡片自动切走），直接截左下角卡片不可靠。脚本已内置：点头像(30,1363) → 菜单项 Buddy加油站(97,1004) → 重新判定。
4. **坐标全部相对窗口原点换算**：窗口移动/缩放不影响。若 WorkBuddy 更新导致界面改版坐标漂移，见下方排错。

## 排错

- **窗口激活失败**：检查 WorkBuddy 是否运行（`Get-Process WorkBuddy`）；最小化会被自动还原。
- **SAD 一直 >15**：模板过期（界面改版）。删除 `scripts/_checkin_log/title_template.bin` 后 dry-run 一次——脚本会在当前截图上重建模板，但**必须人工核对该次截图左下角确实是「Buddy加油站」卡片**再继续。
- **亮度判定异常/点击无效**：界面改版坐标漂移。截图在 `scripts/_checkin_log/<时间戳>_*.bmp`，用图像查看定位新坐标，更新脚本顶部 `AVATAR_REL / GAS_MENU_REL / CLAIM_BTN_REL / BTN_BOX_REL / TITLE_BOX_REL / DOT_CANDIDATES`（均为相对窗口偏移）。
- **安全策略拦截**：本技能脚本只用标准库 ctypes；勿用 PowerShell Add-Type（编译被拦）、reg.exe（被拦）、pyautogui/PIL（未安装）。
- **日志**：每次运行的全部截图存于 `scripts/_checkin_log/`，文件名含时间戳与步骤（01_activated/03_card/05_verify）。

## 风险提示

本技能模拟真实 UI 点击领取用户自己账户的每日积分。客户端更新可能使坐标失效；频繁异常点击可能触发风控。仅限本人账户日常签到使用。
