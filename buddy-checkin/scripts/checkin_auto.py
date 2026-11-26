# -*- coding: utf-8 -*-
"""
WorkBuddy Buddy加油站 自动签到脚本 (纯 ctypes, 无第三方依赖)

实测确认的流程 (2026-11-25, 窗口 rect=(-8,-8,2568,1400) 全屏, 物理像素):
  1. 激活 WorkBuddy 窗口 (Win32 API 置顶获焦, 等效于任务栏切焦点且更稳)
  2. [menu-path 可选] 点击左下角头像(30,1363) 弹出账户菜单 -> 点击 Buddy加油站(97,1004)
     实测: 卡片常驻于侧边栏左下角, 默认直达按钮
  3. 轮播防护: 左下角卡片是三张轮播(加油站/去邀约/成长计划), 用标题区域模板
     匹配(SAD<=15)确认停在 "Buddy加油站", 否则点击第一个轮播圆点(30,1322)切回
  4. 按钮状态判定 (按钮区域平均亮度): >100 "立即领取"可点; <=100 "今日已领取"跳过
     注意: 其他卡片按钮"立即前往"也是亮色, 所以必须先过第3步卡片确认
  5. 点击领取 -> 复检按钮变灰确认成功

用法:
  python checkin_auto.py                  # dry-run: 只检测, 不点击
  python checkin_auto.py --run            # 真实点击领取
  python checkin_auto.py --menu-path --run  # 走头像->菜单完整路径(用户原始4步)
"""
import ctypes, time, struct, os, sys, datetime
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, '_checkin_log')
TPL_PATH = os.path.join(OUT_DIR, 'title_template.bin')

# ---- 相对窗口的锚点坐标 (窗口原点 + 相对偏移 = 绝对坐标, 实测窗口 (-8,-8)) ----
AVATAR_REL = (38, 1371)               # 左下角头像 Jinzer_Y
GAS_MENU_REL = (105, 1012)            # 弹出账户菜单中的 "Buddy加油站" 项
CLAIM_BTN_REL = (291, 1316)           # 卡片 "立即领取/今日已领取/立即前往" 按钮中心
BTN_BOX_REL = (263, 1298, 320, 1324)  # 按钮亮度采样区域 (x0,y0,x1,y1 相对)
TITLE_BOX_REL = (33, 1241, 123, 1263) # 卡片标题 "Buddy加油站" 模板区域 (相对)
DOT1_REL = (38, 1330)                 # 轮播第1个圆点中心 (切回加油站卡片)
BRIGHT_THRESHOLD = 100                # 按钮亮度阈值
SAD_THRESHOLD = 15                    # 标题模板匹配阈值 (实测: 加油站=0, 其他=43)

# ---------------- 窗口激活 ----------------
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
        user32.ShowWindow(hwnd, 9)
        time.sleep(0.4)
    user32.keybd_event(0x12, 0, 0, 0)          # Alt down (绕过前台锁定)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)          # Alt up
    time.sleep(0.5)
    return user32.GetForegroundWindow() == hwnd

def get_window_origin(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top

# ---------------- 截图 ----------------
def screenshot_bmp(path):
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
    class BIH(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]
    bi = BIH(); bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w; bi.biHeight = -h; bi.biPlanes = 1; bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    row = w * 4; img = row * h
    with open(path, 'wb') as f:
        f.write(b'BM' + struct.pack('<IHHI', 54 + img, 0, 0, 54))
        f.write(struct.pack('<IiiHHIIiiII', 40, w, h, 1, 32, 0, img, 0, 0, 0, 0))
        f.write(buf.raw)
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(memdc); user32.ReleaseDC(0, hdc)
    return path

def _load_bmp_raw(path):
    data = open(path, 'rb').read()
    w = struct.unpack('<i', data[18:22])[0]
    off = struct.unpack('<I', data[10:14])[0]
    return data[off:], w * 4

def gray_pixel(bmp_path, x, y):
    raw, stride = _load_bmp_raw(bmp_path)
    p = raw[y*stride + x*4 : y*stride + x*4 + 4]
    return (p[0] + p[1] + p[2]) // 3

def avg_brightness(bmp_path, box_abs):
    raw, stride = _load_bmp_raw(bmp_path)
    x0, y0, x1, y1 = box_abs
    tot = n = 0
    for yy in range(y0, y1, 2):
        for xx in range(x0, x1, 2):
            p = raw[yy*stride + xx*4 : yy*stride + xx*4 + 4]
            tot += (p[0] + p[1] + p[2]) // 3
            n += 1
    return tot // max(n, 1)

def extract_region_gray(bmp_path, box_abs):
    raw, stride = _load_bmp_raw(bmp_path)
    px = []
    for y in range(box_abs[1], box_abs[3]):
        for x in range(box_abs[0], box_abs[2]):
            p = raw[y*stride + x*4 : y*stride + x*4 + 4]
            px.append((p[0] + p[1] + p[2]) // 3)
    return px

def sad_match(bmp_path, box_abs, template):
    cur = extract_region_gray(bmp_path, box_abs)
    if len(cur) != len(template):
        return 9999
    return sum(abs(a - b) for a, b in zip(cur, template)) // len(cur)

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
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log = lambda msg: print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] {msg}')

    # 1. 激活窗口
    hwnd = find_workbuddy_hwnd()
    if not hwnd:
        log('FAIL: 未找到 WorkBuddy 窗口'); sys.exit(1)
    if not activate_window(hwnd):
        log('FAIL: WorkBuddy 无法置顶获焦'); sys.exit(1)
    wx, wy = get_window_origin(hwnd)
    log(f'窗口已激活 hwnd={hwnd} origin=({wx},{wy})')

    # 2a. 菜单路径 (用户原始 4 步流程)
    if menu_path:
        click(wx+AVATAR_REL[0], wy+AVATAR_REL[1]); log('点击头像, 弹出账户菜单')
        time.sleep(1.5)
        screenshot_bmp(os.path.join(OUT_DIR, f'{ts}_02_menu.bmp'))
        click(wx+GAS_MENU_REL[0], wy+GAS_MENU_REL[1]); log('点击 Buddy加油站 菜单项')
        time.sleep(1.8)

    # 3. 轮播防护: 确认卡片停在 "Buddy加油站"
    shot = os.path.join(OUT_DIR, f'{ts}_03_card.bmp')
    screenshot_bmp(shot)
    tbox = (wx+TITLE_BOX_REL[0], wy+TITLE_BOX_REL[1], wx+TITLE_BOX_REL[2], wy+TITLE_BOX_REL[3])
    tpl = list(open(TPL_PATH, 'rb').read()) if os.path.exists(TPL_PATH) else None
    if tpl is None:
        # 首次运行: 用当前标题建立模板 (需人工确认当前是加油站卡片)
        open(TPL_PATH, 'wb').write(bytes(bytearray(extract_region_gray(shot, tbox))))
        log('首次运行: 已建立标题模板, 请人工核对本次截图确为加油站卡片')
    else:
        # 圆点可点击但热区很小(直径约5px), 用候选坐标网格依次尝试
        # 未激活圆点1 实测中心约 (31,1319) 绝对坐标 -> REL (39,1327)
        DOT_CANDIDATES = [(39,1327), (37,1326), (41,1328), (38,1329), (40,1326)]
        ok = False
        for attempt, drel in enumerate(DOT_CANDIDATES):
            click(wx+drel[0], wy+drel[1]); log(f'点击轮播圆点1 候选{attempt+1} ({wx+drel[0]},{wy+drel[1]})')
            time.sleep(1.5)
            screenshot_bmp(shot)
            sad = sad_match(shot, tbox, tpl)
            log(f'  SAD = {sad}')
            if sad <= SAD_THRESHOLD:
                ok = True
                break
        if not ok:
            # 兜底: 等待轮播自动转回 (周期约 15-30s/张)
            for i in range(24):
                time.sleep(5)
                screenshot_bmp(shot)
                sad = sad_match(shot, tbox, tpl)
                if sad <= SAD_THRESHOLD:
                    ok = True
                    log(f'  等待{(i+1)*5}s 后轮播转回加油站卡片 (SAD={sad})')
                    break
        if not ok:
            log('FAIL: 未能切回 Buddy加油站 卡片, 人工检查截图'); sys.exit(1)

    # 4. 按钮状态判定
    bright = avg_brightness(shot, (wx+BTN_BOX_REL[0], wy+BTN_BOX_REL[1], wx+BTN_BOX_REL[2], wy+BTN_BOX_REL[3]))
    log(f'按钮区域平均亮度 = {bright} (阈值 {BRIGHT_THRESHOLD})')
    if bright <= BRIGHT_THRESHOLD:
        log('状态: 今日已领取 -> 无需操作, 结束')
        return
    log('状态: 立即领取 -> 可领取')
    if not run_click:
        log('dry-run 模式, 未点击。加 --run 执行真实领取')
        return

    # 5. 真实点击领取 + 复检
    # 注意: 领取成功后轮播池会变化(实测3圆点变2, 左下角卡片可能自动切走),
    # 复检必须走 头像->菜单->Buddy加油站 路径重新唤出卡片再判定
    click(wx+CLAIM_BTN_REL[0], wy+CLAIM_BTN_REL[1]); log('点击领取按钮')
    time.sleep(2.5)
    click(wx+AVATAR_REL[0], wy+AVATAR_REL[1]); log('复检: 点击头像')
    time.sleep(1.5)
    click(wx+GAS_MENU_REL[0], wy+GAS_MENU_REL[1]); log('复检: 点击 Buddy加油站 菜单项唤出卡片')
    time.sleep(2.0)
    shot2 = os.path.join(OUT_DIR, f'{ts}_05_verify.bmp')
    screenshot_bmp(shot2)
    sad2 = sad_match(shot2, tbox, tpl)
    bright2 = avg_brightness(shot2, (wx+BTN_BOX_REL[0], wy+BTN_BOX_REL[1], wx+BTN_BOX_REL[2], wy+BTN_BOX_REL[3]))
    log(f'复检: 标题SAD={sad2}, 按钮亮度={bright2}')
    if sad2 <= SAD_THRESHOLD and bright2 <= BRIGHT_THRESHOLD:
        log('SUCCESS: 领取完成, 卡片显示 今日已领取')
    else:
        log('WARN: 复检状态异常, 请人工检查截图')

if __name__ == '__main__':
    main()
