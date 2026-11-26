# -*- coding: utf-8 -*-
"""
WorkBuddy Buddy加油站 自动签到脚本 (纯 ctypes, 无第三方依赖)
v2 (2026-11-26 review 修订)

实测确认的流程 (窗口 2576x1408 全屏, 物理像素):
  1. 激活 WorkBuddy 窗口 (Win32 API 置顶获焦)
  2. [menu-path 可选] 点头像 -> 点 Buddy加油站 菜单项
  3. 轮播防护: 左下角是 3 张轮播卡(加油站/去邀约/做任务), 标题区域模板 SAD<=15
     确认为 "Buddy加油站" 卡片; 否则点圆点1候选网格, 兜底等轮播自动转回
  4. 按钮状态: 按钮区域平均亮度 >100 "立即领取"; <=100 "今日已领取"
     (其他卡片"立即前往"也是亮色, 必须先过第3步卡片确认)
  5. 点击领取 -> 头像->菜单->加油站 路径复检按钮变灰

v2 修订 (review 发现的问题):
  - 截图改为抓整个虚拟桌面, 支持窗口在副屏 (原: 只抓主屏, 副屏失效)
  - 坐标 Y 改为"距窗口底部"换算, 窗口大小/化化状态自适应 (原: 绝对偏移, 窗口化失效)
  - 模板移出日志目录 -> scripts/title_template.bin, 并自动迁移旧位置
  - 首次建模板加特征防护 (标题区亮像素占比须 10%~45%, 否则拒绝)
  - 日志只存左下角关键区域小图 (~400KB/张) + 自动保留最近 24 个
  - 全流程异常捕获, 退出码: 0成功/已领取 1环境问题 2复检异常 3模板异常
  - 删除死代码 DOT1_REL / gray_pixel

用法:
  python checkin_auto.py                     # dry-run: 只检测, 不点击
  python checkin_auto.py --run               # 真实点击领取
  python checkin_auto.py --menu-path --run   # 领取前先走头像->菜单路径
  python checkin_auto.py --force             # 跳过窗口尺寸校验(小窗口调试用)
"""
import ctypes, time, struct, os, sys, glob, datetime
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, '_checkin_log')
TPL_PATH = os.path.join(HERE, 'title_template.bin')          # 模板是资产, 不放日志目录
TPL_LEGACY = os.path.join(OUT_DIR, 'title_template.bin')     # 旧位置(自动迁移)
KEEP_LOGS = 24                                               # 日志保留个数

# ---- 坐标: X 相对窗口左边缘; Y 用"距窗口底部"距离 (窗口高度变化自适应) ----
# 基准窗口 2576x1408, origin(-8,-8); 实测绝对坐标 -> 窗口内 -> 距底
AVATAR = (38, 37)            # 头像中心        abs(30,1363) -> 内(38,1371) -> 底距 37
GAS_MENU = (105, 396)        # 菜单加油站项    abs(97,1004) -> 内(105,1012) -> 底距 396
CLAIM_BTN = (291, 92)        # 领取按钮中心    abs(283,1308) -> 内(291,1316) -> 底距 92
BTN_BOX_XB = (263, 320)      # 按钮采样区 x0,x1  (窗口内)
BTN_BOX_YB = (84, 110)       # 按钮采样区 距底 y0,y1
TITLE_BOX_XB = (33, 123)     # 标题采样区 x0,x1
TITLE_BOX_YB = (145, 167)    # 标题采样区 距底 y0,y1
DOT_CANDS = [(39, 81), (37, 82), (41, 80), (38, 79), (40, 82)]  # 圆点1候选(热区~5px)
BRIGHT_THRESHOLD = 100       # 按钮亮度阈值 (实测: 立即领取=162, 今日已领取=67)
SAD_THRESHOLD = 15           # 标题模板 SAD 阈值 (实测: 加油站=0, 其他卡=43)
MIN_WIN_W, MIN_WIN_H = 1000, 900   # 窗口尺寸下限(低于此坐标可能不可靠)

# ---------------- 窗口 ----------------
def find_workbuddy_hwnd():
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []
    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == 'WorkBuddy':
            found.append(hwnd)
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else None

def activate_window(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        time.sleep(0.4)
    user32.keybd_event(0x12, 0, 0, 0)          # Alt down (绕过前台锁定)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)          # Alt up
    time.sleep(0.5)
    return user32.GetForegroundWindow() == hwnd

def window_geometry(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top

def abs_pt(g, rel):
    """(X, 距底) -> 屏幕绝对坐标"""
    wx, wy, ww, wh = g
    return wx + rel[0], wy + wh - rel[1]

def abs_box(g, xb, yb):
    """(x0,x1 窗口内, y0,y1 距底) -> 屏幕绝对 (x0,y0,x1,y1)"""
    wx, wy, ww, wh = g
    return (wx + xb[0], wy + wh - yb[1], wx + xb[1], wy + wh - yb[0])

# ---------------- 抓屏 (整个虚拟桌面, 支持副屏) ----------------
def grab_desktop():
    vx = user32.GetSystemMetrics(76); vy = user32.GetSystemMetrics(77)
    vw = user32.GetSystemMetrics(78); vh = user32.GetSystemMetrics(79)
    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, vw, vh)
    gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, vw, vh, hdc, vx, vy, 0x00CC0020)   # SRCCOPY
    class BIH(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]
    bi = BIH(); bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = vw; bi.biHeight = -vh; bi.biPlanes = 1; bi.biBitCount = 32
    buf = ctypes.create_string_buffer(vw * vh * 4)
    gdi32.GetDIBits(memdc, bmp, 0, vh, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(memdc); user32.ReleaseDC(0, hdc)
    return {'raw': buf.raw, 'w': vw, 'h': vh, 'stride': vw * 4, 'vx': vx, 'vy': vy}

def gray_at(scr, x, y):
    """屏幕绝对坐标处的灰度 (自动减虚拟桌面原点)"""
    sx, sy = x - scr['vx'], y - scr['vy']
    if not (0 <= sx < scr['w'] and 0 <= sy < scr['h']):
        return 0   # 窗口超出虚拟桌面(异常), 返回黑
    o = sy * scr['stride'] + sx * 4
    p = scr['raw'][o:o + 4]
    return (p[0] + p[1] + p[2]) // 3

def avg_brightness(scr, box_abs):
    x0, y0, x1, y1 = box_abs
    tot = n = 0
    for yy in range(y0, y1, 2):
        for xx in range(x0, x1, 2):
            tot += gray_at(scr, xx, yy); n += 1
    return tot // max(n, 1)

def extract_region(scr, box_abs):
    x0, y0, x1, y1 = box_abs
    return [gray_at(scr, xx, yy) for yy in range(y0, y1) for xx in range(x0, x1)]

def sad_match(scr, box_abs, template):
    cur = extract_region(scr, box_abs)
    if len(cur) != len(template):
        return 9999
    return sum(abs(a - b) for a, b in zip(cur, template)) // len(cur)

# ---------------- 日志 (只存关键区域小图) ----------------
def save_crop(path, scr, box_abs):
    """把绝对坐标区域存成小 BMP (约 400KB), 供人工核查"""
    x0, y0, x1, y1 = box_abs
    cw, ch = x1 - x0, y1 - y0
    rows = []
    for yy in range(y0, y1):
        row = bytearray()
        for xx in range(x0, x1):
            sx, sy = xx - scr['vx'], yy - scr['vy']
            o = sy * scr['stride'] + sx * 4
            row.extend(scr['raw'][o:o + 4])
        rows.append(bytes(row))
    img = cw * ch * 4
    with open(path, 'wb') as f:
        f.write(b'BM' + struct.pack('<IHHI', 54 + img, 0, 0, 54))
        f.write(struct.pack('<IiiHHIIiiII', 40, cw, ch, 1, 32, 0, img, 0, 0, 0, 0))
        for r in reversed(rows):   # BMP 自底向上
            f.write(r)
    return path

def prune_logs():
    files = sorted(glob.glob(os.path.join(OUT_DIR, '*.bmp')), key=os.path.getmtime, reverse=True)
    for f in files[KEEP_LOGS:]:
        try: os.remove(f)
        except OSError: pass

# ---------------- 点击 ----------------
def click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.4)

# ---------------- 主流程 ----------------
def main():
    run_click = '--run' in sys.argv
    menu_path = '--menu-path' in sys.argv
    force = '--force' in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    prune_logs()
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log = lambda m: print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] {m}')

    # 1. 激活窗口
    hwnd = find_workbuddy_hwnd()
    if not hwnd:
        log('FAIL: 未找到 WorkBuddy 窗口'); return 1
    if not activate_window(hwnd):
        log('FAIL: WorkBuddy 无法置顶获焦'); return 1
    g = window_geometry(hwnd)
    log(f'窗口已激活 hwnd={hwnd} rect=({g[0]},{g[1]},{g[0]+g[2]},{g[1]+g[3]}) size={g[2]}x{g[3]}')
    if not force and (g[2] < MIN_WIN_W or g[3] < MIN_WIN_H):
        log(f'FAIL: 窗口过小({g[2]}x{g[3]} < {MIN_WIN_W}x{MIN_WIN_H}), 左下角卡片布局可能不可靠。'
            f'请最大化窗口, 或加 --force 强制运行'); return 1

    # 2a. 菜单路径 (可选)
    if menu_path:
        click(*abs_pt(g, AVATAR)); log('点击头像, 弹出账户菜单')
        time.sleep(1.5)
        click(*abs_pt(g, GAS_MENU)); log('点击 Buddy加油站 菜单项')
        time.sleep(1.8)

    # 3. 轮播防护
    tbox = abs_box(g, TITLE_BOX_XB, TITLE_BOX_YB)
    bbox = abs_box(g, BTN_BOX_XB, BTN_BOX_YB)
    crop_box = abs_box(g, (0, 420), (0, 250))   # 左下角关键区域(日志用)

    # 模板: 优先新位置, 自动迁移旧位置
    if not os.path.exists(TPL_PATH) and os.path.exists(TPL_LEGACY):
        try:
            os.replace(TPL_LEGACY, TPL_PATH); log('已迁移模板到 scripts/title_template.bin')
        except OSError:
            pass

    scr = grab_desktop()
    tpl = list(open(TPL_PATH, 'rb').read()) if os.path.exists(TPL_PATH) else None

    # 3a. 卡片存在性检测: 卡片可能被收起(只剩头像) -> 走菜单路径唤出
    def card_present(s):
        px = extract_region(s, tbox)
        return sum(1 for v in px if v > 180) / max(len(px), 1) >= 0.10
    if not card_present(scr):
        log('卡片未显示(左下角只剩头像), 走 头像->菜单->Buddy加油站 路径唤出')
        click(*abs_pt(g, AVATAR)); time.sleep(1.5)
        click(*abs_pt(g, GAS_MENU)); time.sleep(2.0)
        scr = grab_desktop()
        if not card_present(scr):
            save_crop(os.path.join(OUT_DIR, f'{ts}_00_nocard.bmp'), scr, crop_box)
            log('FAIL: 菜单路径未能唤出卡片, 截图见 ' + OUT_DIR); return 1
        log('卡片已唤出')

    if tpl is None:
        # 首次建模板: 特征防护 - 标题区必须有文字(亮像素占比在合理区间)
        px = extract_region(scr, tbox)
        white_ratio = sum(1 for v in px if v > 180) / max(len(px), 1)
        if not (0.10 <= white_ratio <= 0.45):
            save_crop(os.path.join(OUT_DIR, f'{ts}_00_nocard.bmp'), scr, crop_box)
            log(f'FAIL: 标题区域无文字特征(亮像素占比{white_ratio:.0%}), 当前不是加油站卡片, 拒绝建立模板。'
                f'请人工确认后删除日志重试'); return 3
        with open(TPL_PATH, 'wb') as f:
            f.write(bytes(bytearray(px)))
        tpl = px
        log(f'首次运行: 已建立标题模板 (亮像素占比{white_ratio:.0%}, 请人工核对截图)')
        save_crop(os.path.join(OUT_DIR, f'{ts}_00_tpl.bmp'), scr, crop_box)

    ok = False
    for attempt, drel in enumerate(DOT_CANDS):
        click(*abs_pt(g, drel))
        time.sleep(1.5)
        scr = grab_desktop()
        sad = sad_match(scr, tbox, tpl)
        log(f'点击圆点候选{attempt+1} -> SAD={sad}')
        if sad <= SAD_THRESHOLD:
            ok = True; break
    if not ok:
        # 兜底: 等轮播自动转回 (实测周期约 15-30s, 最多等 90s)
        for i in range(18):
            time.sleep(5)
            scr = grab_desktop()
            sad = sad_match(scr, tbox, tpl)
            if sad <= SAD_THRESHOLD:
                ok = True; log(f'  等待{(i+1)*5}s 后轮播转回加油站卡片 (SAD={sad})'); break
    if not ok:
        save_crop(os.path.join(OUT_DIR, f'{ts}_03_fail.bmp'), scr, crop_box)
        log('FAIL: 90s 内未能切回 Buddy加油站 卡片, 截图见 ' + OUT_DIR); return 1

    # 4. 按钮状态判定
    save_crop(os.path.join(OUT_DIR, f'{ts}_03_card.bmp'), scr, crop_box)
    bright = avg_brightness(scr, bbox)
    log(f'按钮区域平均亮度 = {bright} (阈值 {BRIGHT_THRESHOLD})')
    if bright <= BRIGHT_THRESHOLD:
        log('状态: 今日已领取 -> 无需操作, 结束'); return 0
    log('状态: 立即领取 -> 可领取')
    if not run_click:
        log('dry-run 模式, 未点击。加 --run 执行真实领取'); return 0

    # 5. 点击领取 + 菜单路径复检
    click(*abs_pt(g, CLAIM_BTN)); log('点击领取按钮')
    time.sleep(2.5)
    click(*abs_pt(g, AVATAR)); log('复检: 点击头像')
    time.sleep(1.5)
    click(*abs_pt(g, GAS_MENU)); log('复检: 点击 Buddy加油站 菜单项')
    time.sleep(2.0)
    scr = grab_desktop()
    save_crop(os.path.join(OUT_DIR, f'{ts}_05_verify.bmp'), scr, crop_box)
    sad2 = sad_match(scr, tbox, tpl)
    bright2 = avg_brightness(scr, bbox)
    log(f'复检: 标题SAD={sad2}, 按钮亮度={bright2}')
    if sad2 <= SAD_THRESHOLD and bright2 <= BRIGHT_THRESHOLD:
        log('SUCCESS: 领取完成, 卡片显示 今日已领取'); return 0
    log('WARN: 复检状态异常, 请人工检查截图 ' + OUT_DIR); return 2

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('[!] 已中断'); sys.exit(130)
    except Exception as e:
        print(f'[FATAL] 未预期异常: {type(e).__name__}: {e}')
        sys.exit(1)
