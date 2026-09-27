"""
main.py - Clash of Clans Auto-Attack Bot (lazy-import, menu-driven)

Run:
    python main.py

Modes:
    1) Full auto                     - attacks forever, auto-deploys
    2) Full auto + storage check     - stops at 24M gold/elixir, auto-deploys
    3) Auto attack (no deploy)       - runs the attack cycle but skips
                                       goblins/heroes/abilities so you can
                                       deploy manually. Waits MANUAL_DEPLOY_WAIT
                                       seconds then proceeds to end battle.
    0) Exit
"""

import os
import csv
import re
import time
import json
import random
import signal
import argparse
import subprocess
from datetime import datetime

# =========================================================================
# RUNTIME FLAGS (set from CLI args in main())
# =========================================================================
USE_GPU   = False   # --gpu       try cv2.cuda template matching
FAST_MODE = False   # --fast      stop scale search at first good hit
GPU_READY = False   # set True once a CUDA device is actually confirmed

# =========================================================================
# LAZY MODULE LOADING
# =========================================================================
pyautogui = None
cv2 = None
np = None
gw = None
RapidOCR = None
mss_mod = None


def _ensure_pyautogui():
    global pyautogui
    if pyautogui is None:
        print("  [deps] importing pyautogui...")
        import pyautogui as _pa
        pyautogui = _pa
        pyautogui.PAUSE = 0.02
        pyautogui.FAILSAFE = True


def _ensure_full_deps(need_ocr=False):
    global cv2, np, gw, RapidOCR
    _ensure_pyautogui()

    if cv2 is None:
        print("  [deps] importing opencv-python...")
        import cv2 as _cv
        cv2 = _cv

    if np is None:
        print("  [deps] importing numpy...")
        import numpy as _np
        np = _np

    if gw is None:
        print("  [deps] importing pygetwindow...")
        import pygetwindow as _gw
        gw = _gw

    if need_ocr and RapidOCR is None:
        try:
            print("  [deps] importing rapidocr-onnxruntime (may take ~2s)...")
            from rapidocr_onnxruntime import RapidOCR as _r
            RapidOCR = _r
        except ImportError:
            print("\n  [!] rapidocr-onnxruntime not installed.")
            print("      pip install rapidocr-onnxruntime")
            print("      Falling back to mode without storage check.\n")
            return False

    _ensure_mss()
    _check_gpu()
    return True


def _ensure_mss():
    """mss grabs frames straight from the OS compositor - typically 3-8x
    faster than pyautogui.screenshot()/PIL ImageGrab, which matters a lot
    when we're polling for an icon every SCAN_INTERVAL seconds."""
    global mss_mod
    if mss_mod is None:
        try:
            import mss as _mss
            mss_mod = _mss
            print("  [deps] mss available -> using fast screen capture")
        except ImportError:
            mss_mod = False
            print("  [deps] mss not installed (pip install mss) -> "
                  "falling back to pyautogui.screenshot (slower)")
    return mss_mod


def _check_gpu():
    """Confirms an OpenCV build with CUDA + an actual CUDA device are both
    present. --gpu only does anything useful if both are true."""
    global GPU_READY
    if not USE_GPU:
        return False
    try:
        count = cv2.cuda.getCudaEnabledDeviceCount()
        if count > 0:
            GPU_READY = True
            dev_name = ""
            try:
                cv2.cuda.printCudaDeviceInfo(0)
            except Exception:
                pass
            print(f"  [gpu] {count} CUDA device(s) detected -> GPU template matching ON")
        else:
            print("  [gpu] --gpu was passed but no CUDA device was found "
                  "(need an NVIDIA GPU + opencv-contrib-python built with CUDA). "
                  "Falling back to CPU.")
    except AttributeError:
        print("  [gpu] --gpu was passed but this OpenCV build has no cv2.cuda module "
              "(pip install opencv-contrib-python built with CUDA support, or use the "
              "CPU build). Falling back to CPU.")
    return GPU_READY


# =========================================================================
# PATHS + CONFIG
# =========================================================================
ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE   = os.path.join(ASSET_DIR, "config.json")
LOG_FILE      = os.path.join(ASSET_DIR, "bot_log.csv")
LOOT_LOG_FILE = os.path.join(ASSET_DIR, "loot_log.csv")
REGIONS_FILE  = os.path.join(ASSET_DIR, "resource_regions.json")

ASSETS = {
    "attack_menu":      "attack_btn_menu_1.png",
    "find_match":       "find_match_btn_2.png",
    "attack_go":        "attack_btn_go_3.png",
    "gollum":           "gollum_click_4.png",
    "end_battle":       "end_battlel_5.png",
    "surrender_dialog": "surrender_okay_6.png",
    "return_home":      "return_home_7.png",
}
ASSET_PATHS = {k: os.path.join(ASSET_DIR, v) for k, v in ASSETS.items()}

DEFAULT_CONFIDENCE = {
    "attack_menu":      0.82,
    "find_match":       0.72,
    "attack_go":        0.82,
    "gollum":           0.85,
    "end_battle":       0.68,
    "surrender_dialog": 0.90,
    "return_home":      0.82,
}

DEFAULT_SCALES = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95,
                  1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.4, 1.5]


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    cfg.setdefault("confidence", dict(DEFAULT_CONFIDENCE))
    cfg.setdefault("scales", list(DEFAULT_SCALES))
    for k, v in DEFAULT_CONFIDENCE.items():
        cfg["confidence"].setdefault(k, v)
    save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# =========================================================================
# CONSTANTS
# =========================================================================
WINDOW_TITLE_KEYWORD = "Clash of Clans"
GAME_EXE_PATH = r"C:\Program Files\Google\Play Games\current\emulator\crosvm.exe"

MATCHMAKING_TIMEOUT = 60
STEP_TIMEOUT        = 12
SCAN_INTERVAL       = 0.05
CLICK_MOVE_DURATION = 0.02
POST_CLICK_PAUSE    = 0.25
BATTLE_END_WAIT     = 6.0
BASE_PREVIEW_WAIT   = 2.0
MANUAL_DEPLOY_WAIT  = 60.0   # option 3: how long to wait for your manual deploy

FALLBACK_MARGIN       = 0.05
SURRENDER_OKAY_OFFSET = (0.74, 0.83)

ZOOM_OUT_TIMES  = 2
ZOOM_STEP_DELAY = 0.3

MAX_GOLD   = 24_000_000
MAX_ELIXIR = 24_000_000
SANITY_MAX = 100_000_000

SAVE_DEBUG = False
DEBUG_DIR  = os.path.join(ASSET_DIR, "debug_crops")

DEFAULT_REGIONS = {
    "gold":        [1053, 242, 190, 42],
    "elixir":      [1054, 294, 193, 39],
    "dark_elixir": [1097, 350, 146, 32],
    "gems":        [1099, 395, 143, 40],
}

GOBLIN_NUM_PASSES       = 8
GOBLIN_JITTER           = 10
GOBLIN_TAP_DELAY        = 0.05
GOBLIN_REVERSE_ALT_PASS = True
GOBLIN_PASS_DELAY_MIN   = 2.0
GOBLIN_PASS_DELAY_MAX   = 4.0

DEPLOY_MIN_X = 640
DEPLOY_MAX_X = 1830
DEPLOY_MIN_Y = 180
DEPLOY_MAX_Y = 830

GOBLIN_LOOP = [
    (1446, 780), (1524, 752), (1623, 671), (1667, 619),
    (1680, 498), (1565, 399), (1467, 357), (1397, 272),
    (1388, 240), (1314, 240), (1191, 241), (1139, 262),
    (1062, 331), (992, 378), (888, 449), (853, 517),
    (847, 614), (907, 669), (963, 709), (1030, 754),
    (1131, 780), (1223, 780), (1386, 780), (1521, 772),
    (1562, 715), (1591, 642),
]

HERO_BUTTONS = [
    (1070, 862), (1140, 857), (1220, 851), (1300, 858),
]
HERO_TAPS_PER_SPOT = 2
HERO_SELECT_DELAY  = 0.20
HERO_TAP_DELAY     = 0.12
HERO_BETWEEN_DELAY = 0.25
HERO_ABILITY_DELAY = 0.20
HERO_ABILITY_WAIT  = 6.0


# =========================================================================
# MENU
# =========================================================================
def print_menu():
    print()
    print("=" * 60)
    print("  Clash of Clans Auto-Attack Bot")
    print("=" * 60)
    print("  1) Full auto")
    print("     attacks forever, auto-deploys goblins + heroes + abilities")
    print()
    print("  2) Full auto + storage check")
    print("     same as 1, but stops when gold or elixir reaches 24M")
    print()
    print("  3) Auto attack only (NO deploy)")
    print("     clicks Attack / Find Match / Attack!, then waits")
    print(f"     {MANUAL_DEPLOY_WAIT:.0f}s for YOU to deploy manually,")
    print("     then clicks End Battle / Surrender / Return Home")
    print()
    print("  4) Test / calibrate asset detection")
    print("     no clicking - repeatedly scans for every icon and prints")
    print("     live score + timing per asset, so you can tune confidence")
    print("     thresholds and check detection speed/accuracy")
    print()
    print("  0) Exit")
    print("=" * 60)
    print(f"  gpu={'ON' if (USE_GPU and GPU_READY) else ('requested, unavailable' if USE_GPU else 'off')}"
          f"   fast={'ON' if FAST_MODE else 'off'}"
          f"   capture={'mss' if mss_mod else 'pyautogui'}")


# =========================================================================
# LOGGING
# =========================================================================
def ensure_log_header():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp", "step", "asset", "found", "score", "threshold",
                "scale", "rel_x", "rel_y", "abs_x", "abs_y",
                "window_left", "window_top", "window_w", "window_h", "note",
            ])


def log_attempt(step, asset_key, match, region, note=""):
    ts = datetime.now().isoformat()
    if "error" in match:
        row = [ts, step, asset_key, False, "", "", "", "", "", "", "",
               region[0], region[1], region[2], region[3], match["error"]]
        print(f"[log] {ts} | {step:<18} | {asset_key:<16} | ERROR: {match['error']}")
    else:
        cx, cy = match["x"] + match["w"] // 2, match["y"] + match["h"] // 2
        abs_x, abs_y = region[0] + cx, region[1] + cy
        row = [ts, step, asset_key, match["found"], f"{match['score']:.4f}",
               f"{match['threshold']:.2f}", f"{match['scale']:.2f}",
               cx, cy, abs_x, abs_y,
               region[0], region[1], region[2], region[3], note]
        status = "FOUND" if match["found"] else "miss "
        print(f"[log] {ts} | {step:<18} | {asset_key:<16} | {status} | "
              f"score={match['score']:.3f} (need {match['threshold']:.2f}) | "
              f"scale={match['scale']:.2f} | rel=({cx},{cy}) abs=({abs_x},{abs_y})")
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow(row)


def log_loot_row(before, after, gained):
    exists = os.path.exists(LOOT_LOG_FILE)

    def v(d, k):
        val = d.get(k)
        return "" if val is None else val

    with open(LOOT_LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow([
                "timestamp",
                "gold_before", "elixir_before", "de_before", "gems_before",
                "gold_after",  "elixir_after",  "de_after",  "gems_after",
                "gold_gained", "elixir_gained", "de_gained", "gems_gained",
            ])
        w.writerow([
            datetime.now().isoformat(),
            v(before, "gold"),        v(before, "elixir"),
            v(before, "dark_elixir"), v(before, "gems"),
            v(after, "gold"),         v(after, "elixir"),
            v(after, "dark_elixir"),  v(after, "gems"),
            v(gained, "gold"),        v(gained, "elixir"),
            v(gained, "dark_elixir"), v(gained, "gems"),
        ])


def print_resources(tag, res):
    if not res:
        print(f"[loot] {tag}  (read failed)")
        return

    def fmt(v):
        return f"{v:>11,}" if v is not None else "        n/a"

    print(f"[loot] {tag}  gold={fmt(res.get('gold'))}  "
          f"elixir={fmt(res.get('elixir'))}  "
          f"de={fmt(res.get('dark_elixir'))}  "
          f"gems={fmt(res.get('gems'))}")


# =========================================================================
# OCR
# =========================================================================
def load_regions():
    if os.path.exists(REGIONS_FILE):
        try:
            with open(REGIONS_FILE) as f:
                data = json.load(f)
            if data:
                return data
        except Exception as e:
            print(f"  ! could not read {REGIONS_FILE}: {e}, using defaults")
    return dict(DEFAULT_REGIONS)


def _preprocess(crop_rgb):
    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    h, w = thresh.shape
    thresh = cv2.resize(thresh, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    return thresh


def _parse_number(text):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def read_resources(regions, verbose=True):
    win = find_game_window()
    if win is None:
        return None

    rect = get_region(win)
    shot = pyautogui.screenshot(region=rect)
    img = np.array(shot)

    if SAVE_DEBUG:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(DEBUG_DIR, "_window.png"),
                    cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

    engine = RapidOCR()
    out = {}

    for name in ("gold", "elixir", "dark_elixir", "gems"):
        crop_rect = regions.get(name)
        if not crop_rect or crop_rect[2] < 5 or crop_rect[3] < 5:
            out[name] = None
            continue

        l, t, w, h = crop_rect
        crop = img[t:t + h, l:l + w]
        if crop.size == 0:
            out[name] = None
            continue

        if SAVE_DEBUG:
            cv2.imwrite(os.path.join(DEBUG_DIR, f"{name}.png"),
                        cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))

        processed = _preprocess(crop)
        try:
            result, _ = engine(processed)
        except Exception:
            out[name] = None
            continue

        if not result:
            out[name] = None
            if verbose:
                print(f"    [{name}] raw='' -> None")
            continue

        text = " ".join(item[1] for item in result)
        value = _parse_number(text)

        if value is not None and value > SANITY_MAX:
            if verbose:
                print(f"    [{name}] raw={text!r} -> {value}  ** ignored **")
            value = None
        elif verbose:
            print(f"    [{name}] raw={text!r} -> {value}")

        out[name] = value

    return out


# =========================================================================
# WINDOW + MATCHING
# =========================================================================
def find_game_window():
    for w in gw.getAllWindows():
        if WINDOW_TITLE_KEYWORD.lower() in w.title.lower() and w.width > 0 and w.height > 0:
            return w
    return None


def ensure_game_running():
    win = find_game_window()
    if win:
        return win
    print(f"No window matching '{WINDOW_TITLE_KEYWORD}' found. Launching...")
    if os.path.exists(GAME_EXE_PATH):
        subprocess.Popen([GAME_EXE_PATH])
    else:
        print(f"WARNING: {GAME_EXE_PATH} not found. Start the game manually.")
    for _ in range(60):
        time.sleep(1)
        win = find_game_window()
        if win:
            print("Game window detected.")
            return win
    raise RuntimeError("Timed out waiting for the game window.")


_ACTIVATE_EVERY = 2.0   # seconds between win.activate() calls
_last_activate_ts = 0.0


def get_region(win, force_activate=False):
    """Returns (left, top, width, height) for the game window.

    win.activate() is a real OS call (window-manager round trip) and the
    old code did it on *every* poll inside the scan loops (every
    SCAN_INTERVAL == 0.05s). That's ~20 activations/sec doing nothing
    useful once the window is already focused, and it's the single
    biggest reason icon lookups felt slow. Now we only re-activate every
    _ACTIVATE_EVERY seconds (or when explicitly asked / window looks
    invalid), and just re-read left/top/width/height (cheap, no IPC)
    every other call.
    """
    global _last_activate_ts
    win = find_game_window() or win
    now = time.time()

    try:
        if win.isMinimized:
            win.restore()
            time.sleep(0.2)
            force_activate = True
        if force_activate or (now - _last_activate_ts) >= _ACTIVATE_EVERY:
            win.activate()
            _last_activate_ts = now
    except Exception:
        pass

    left, top, width, height = win.left, win.top, win.width, win.height

    if left < -5000 or top < -5000 or width <= 0 or height <= 0:
        print("  ! window rect invalid, retrying...")
        time.sleep(0.6)
        win2 = find_game_window()
        if win2:
            try:
                if win2.isMinimized:
                    win2.restore()
                    time.sleep(0.2)
                win2.activate()
            except Exception:
                pass
            left, top, width, height = win2.left, win2.top, win2.width, win2.height

    return (left, top, width, height)


def click_region_point(region, rel_x, rel_y):
    abs_x = region[0] + rel_x
    abs_y = region[1] + rel_y

    screen_w, screen_h = pyautogui.size()
    safe_x = min(max(abs_x, 2), screen_w - 2)
    safe_y = min(max(abs_y, 2), screen_h - 2)

    try:
        pyautogui.moveTo(safe_x, safe_y, duration=CLICK_MOVE_DURATION)
        pyautogui.click()
    except pyautogui.FailSafeException:
        print(f"  ! fail-safe on click ({safe_x},{safe_y})")
    except Exception as e:
        print(f"  ! click error ({safe_x},{safe_y}): {e}")


_mss_thread_local = {}


def capture(region):
    left, top, width, height = region
    if mss_mod:
        # mss instances aren't thread-safe to share, but we're single
        # threaded here; keep one instance alive instead of re-creating
        # it (which is the expensive part) on every single poll.
        sct = _mss_thread_local.get("sct")
        if sct is None:
            sct = mss_mod.mss()
            _mss_thread_local["sct"] = sct
        monitor = {"left": left, "top": top, "width": width, "height": height}
        raw = sct.grab(monitor)
        # mss gives BGRA, already in BGR-first order - just drop alpha.
        return np.array(raw)[:, :, :3]

    shot = pyautogui.screenshot(region=region)
    return cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)


_TEMPLATE_CACHE = {}          # asset_key -> BGR ndarray (avoid re-reading PNGs from disk)
_GPU_TEMPLATE_CACHE = {}      # asset_key -> cv2.cuda_GpuMat of the template
_gpu_screen_mat = None        # reused GpuMat for the screenshot upload


def _load_template(asset_key):
    if asset_key not in _TEMPLATE_CACHE:
        path = ASSET_PATHS[asset_key]
        if not os.path.exists(path):
            _TEMPLATE_CACHE[asset_key] = None
            return None, f"missing asset: {path}"
        img = cv2.imread(path)
        if img is None:
            _TEMPLATE_CACHE[asset_key] = None
            return None, f"could not read: {path}"
        _TEMPLATE_CACHE[asset_key] = img
    img = _TEMPLATE_CACHE[asset_key]
    if img is None:
        return None, f"missing/unreadable asset: {asset_key}"
    return img, None


def match_template(screen_bgr, asset_key, cfg, thorough=None):
    """Finds asset_key inside screen_bgr across cfg['scales'].

    thorough=None uses the global FAST_MODE flag (inverted): by default
    (FAST_MODE off) behaves like the original - scans every scale so the
    reported score is the true best score, which is what you want while
    tuning confidence thresholds. With FAST_MODE on, or thorough=False,
    it stops at the first scale that clears the threshold, which is a
    big speedup once thresholds are already dialed in (the common case
    once you're just running the bot).
    """
    template_orig, err = _load_template(asset_key)
    if err:
        return {"error": err}

    threshold = cfg["confidence"].get(asset_key, 0.82)
    screen_h, screen_w = screen_bgr.shape[:2]

    scales = list(cfg["scales"])
    pref = cfg.get("preferred_scale", {}).get(asset_key)
    if pref is not None and pref in scales:
        scales.remove(pref)
        scales.insert(0, pref)

    stop_early = FAST_MODE if thorough is None else (not thorough)

    if GPU_READY and USE_GPU:
        best = _match_template_gpu(screen_bgr, template_orig, asset_key,
                                    scales, screen_w, screen_h,
                                    threshold, stop_early)
    else:
        best = _match_template_cpu(screen_bgr, template_orig, scales,
                                    screen_w, screen_h, threshold, stop_early)

    if best is None:
        return {"error": "template larger than screen at every scale"}

    best["threshold"] = threshold
    best["found"] = best["score"] >= threshold
    return best


def _match_template_cpu(screen_bgr, template_orig, scales, screen_w, screen_h,
                         threshold, stop_early):
    best = None
    h0, w0 = template_orig.shape[:2]
    for scale in scales:
        w, h = int(w0 * scale), int(h0 * scale)
        if w < 5 or h < 5 or w > screen_w or h > screen_h:
            continue
        template = cv2.resize(template_orig, (w, h), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if best is None or max_val > best["score"]:
            best = {"x": max_loc[0], "y": max_loc[1], "w": w, "h": h,
                    "score": max_val, "scale": scale}
        if stop_early and max_val >= threshold:
            break
    return best


def _match_template_gpu(screen_bgr, template_orig, asset_key, scales,
                         screen_w, screen_h, threshold, stop_early):
    """Same search, run on the GPU via cv2.cuda. Uploading the screenshot
    once per call and reusing GpuMats for the templates is what makes this
    actually faster than CPU - re-uploading per scale would eat the gain."""
    global _gpu_screen_mat
    try:
        if _gpu_screen_mat is None:
            _gpu_screen_mat = cv2.cuda_GpuMat()
        _gpu_screen_mat.upload(screen_bgr)

        h0, w0 = template_orig.shape[:2]
        best = None
        for scale in scales:
            w, h = int(w0 * scale), int(h0 * scale)
            if w < 5 or h < 5 or w > screen_w or h > screen_h:
                continue

            cache_key = (asset_key, scale)
            tmpl_gpu = _GPU_TEMPLATE_CACHE.get(cache_key)
            if tmpl_gpu is None:
                resized = cv2.resize(template_orig, (w, h), interpolation=cv2.INTER_AREA)
                tmpl_gpu = cv2.cuda_GpuMat()
                tmpl_gpu.upload(resized)
                _GPU_TEMPLATE_CACHE[cache_key] = tmpl_gpu

            matcher = cv2.cuda.createTemplateMatching(cv2.CV_8UC3, cv2.TM_CCOEFF_NORMED)
            result_gpu = matcher.match(_gpu_screen_mat, tmpl_gpu)
            result = result_gpu.download()
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if best is None or max_val > best["score"]:
                best = {"x": max_loc[0], "y": max_loc[1], "w": w, "h": h,
                        "score": max_val, "scale": scale}
            if stop_early and max_val >= threshold:
                break
        return best
    except Exception as e:
        print(f"  [gpu] match failed ({e}), falling back to CPU for this frame")
        return _match_template_cpu(screen_bgr, template_orig, scales,
                                    screen_w, screen_h, threshold, stop_early)


def center_of(match):
    return match["x"] + match["w"] // 2, match["y"] + match["h"] // 2


def wait_and_click(win, cfg, asset_key, label, timeout=STEP_TIMEOUT):
    print(f"Looking for '{label}'...")
    start = time.time()
    region = get_region(win)
    attempt = 0
    best = None

    while time.time() - start < timeout:
        attempt += 1
        region = get_region(win)
        screen = capture(region)
        match = match_template(screen, asset_key, cfg)

        if "error" in match:
            print(f"  [{attempt}] {asset_key}: {match['error']}")
        else:
            status = "FOUND" if match["found"] else "miss"
            print(f"  [{attempt}] {asset_key}: {status} score={match['score']:.3f} "
                  f"(need {match['threshold']:.2f}) scale={match['scale']:.2f}")
            if best is None or match["score"] > best["score"]:
                best = match

            if match["found"]:
                log_attempt(label, asset_key, match, region)
                cfg.setdefault("preferred_scale", {})[asset_key] = match["scale"]
                save_config(cfg)
                cx, cy = center_of(match)
                click_region_point(region, cx, cy)
                return True
        time.sleep(SCAN_INTERVAL)

    if best is not None and best["score"] >= best["threshold"] - FALLBACK_MARGIN:
        print(f"  -> fallback click (score {best['score']:.3f})")
        log_attempt(label, asset_key, best, region, note="FALLBACK_CLICK")
        cx, cy = center_of(best)
        click_region_point(region, cx, cy)
        return True

    log_attempt(label, asset_key, best or {"error": "no attempt"}, region, note="TIMEOUT")
    return False


def wait_until_gone(win, cfg, asset_key, label, timeout=MATCHMAKING_TIMEOUT):
    print(f"Waiting for '{label}' to clear...")
    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        region = get_region(win)
        screen = capture(region)
        match = match_template(screen, asset_key, cfg)
        if "error" in match:
            return True
        status = "still showing" if match["found"] else "cleared"
        print(f"  [{attempt}] {asset_key}: {status} score={match['score']:.3f} "
              f"(need {match['threshold']:.2f})")
        if not match["found"]:
            return True
        time.sleep(SCAN_INTERVAL)
    print(f"  ! timeout waiting for '{label}' to clear")
    return False


def click_surrender_okay(win, cfg):
    region = get_region(win)
    screen = capture(region)
    match = match_template(screen, "surrender_dialog", cfg)
    log_attempt("surrender_okay", "surrender_dialog", match, region)
    if "error" in match or not match["found"]:
        print("  ! surrender dialog not found")
        return False
    ox = match["x"] + int(match["w"] * SURRENDER_OKAY_OFFSET[0])
    oy = match["y"] + int(match["h"] * SURRENDER_OKAY_OFFSET[1])
    click_region_point(region, ox, oy)
    return True


# =========================================================================
# DEPLOY
# =========================================================================
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def zoom_out_and_center(win=None, times=ZOOM_OUT_TIMES):
    print(f"Zooming out ({times} steps) before deploy...")
    if win is not None:
        region = get_region(win)
        cx = region[0] + region[2] // 2
        cy = region[1] + region[3] // 2
    else:
        screen_w, screen_h = pyautogui.size()
        cx, cy = screen_w // 2, screen_h // 2

    try:
        pyautogui.moveTo(cx, cy, duration=0.10)
        time.sleep(0.15)
    except pyautogui.FailSafeException:
        print("  ! fail-safe on move to center")
        return

    for i in range(times):
        try:
            pyautogui.keyDown("ctrl")
            pyautogui.press("-")
            pyautogui.keyUp("ctrl")
        except Exception as e:
            print(f"  ! zoom step {i+1} failed: {e}")
            return
        time.sleep(ZOOM_STEP_DELAY)

    print("  zoom-out done.")


def select_gollum_card(win, cfg, timeout=8):
    print("Selecting gollum troop card...")
    match = {"error": "no attempt"}
    best = None
    region = get_region(win)
    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        region = get_region(win)
        screen = capture(region)
        match = match_template(screen, "gollum", cfg)
        if "error" in match:
            print(f"  [{attempt}] gollum: {match['error']}")
        else:
            status = "FOUND" if match["found"] else "miss"
            print(f"  [{attempt}] gollum: {status} score={match['score']:.3f} "
                  f"(need {match['threshold']:.2f}) scale={match['scale']:.2f}")
            if best is None or match["score"] > best["score"]:
                best = match
            if match["found"]:
                break
        time.sleep(SCAN_INTERVAL)

    if "error" not in match and match.get("found"):
        log_attempt("gollum_select", "gollum", match, region)
        cx, cy = center_of(match)
        click_region_point(region, cx, cy)
        time.sleep(0.15)
        return True
    if best is not None and best["score"] >= best["threshold"] - FALLBACK_MARGIN:
        print(f"  -> fallback select gollum (score {best['score']:.3f})")
        log_attempt("gollum_select", "gollum", best, region, note="FALLBACK_CLICK")
        cx, cy = center_of(best)
        click_region_point(region, cx, cy)
        time.sleep(0.15)
        return True
    log_attempt("gollum_select", "gollum", best or match, region, note="TIMEOUT")
    print("  ! could not find gollum card")
    return False


def deploy_goblins():
    print(f"\n=== Deploying goblins ({GOBLIN_NUM_PASSES} passes) ===")
    for p in range(GOBLIN_NUM_PASSES):
        direction = "reverse" if (GOBLIN_REVERSE_ALT_PASS and p % 2 == 1) else "forward"
        points = list(reversed(GOBLIN_LOOP)) if direction == "reverse" else GOBLIN_LOOP
        print(f"  Pass {p + 1}/{GOBLIN_NUM_PASSES} ({direction})")

        for (x, y) in points:
            jx = _clamp(x + random.randint(-GOBLIN_JITTER, GOBLIN_JITTER),
                        DEPLOY_MIN_X, DEPLOY_MAX_X)
            jy = _clamp(y + random.randint(-GOBLIN_JITTER, GOBLIN_JITTER),
                        DEPLOY_MIN_Y, DEPLOY_MAX_Y)
            try:
                pyautogui.moveTo(jx, jy)
                pyautogui.click()
            except pyautogui.FailSafeException:
                print("  ! fail-safe on goblin tap")
                continue
            time.sleep(GOBLIN_TAP_DELAY)

        if p < GOBLIN_NUM_PASSES - 1:
            pause = random.uniform(GOBLIN_PASS_DELAY_MIN, GOBLIN_PASS_DELAY_MAX)
            print(f"    ... pause {pause:.1f}s ...")
            time.sleep(pause)


def deploy_heroes():
    print("\n=== Deploying heroes ===")
    spot_a, spot_b = random.sample(GOBLIN_LOOP, 2)
    print(f"  Spot A: {spot_a}  (heroes 1, 2)")
    print(f"  Spot B: {spot_b}  (heroes 3, 4)")

    plan = [
        (1, HERO_BUTTONS[0], spot_a),
        (2, HERO_BUTTONS[1], spot_a),
        (3, HERO_BUTTONS[2], spot_b),
        (4, HERO_BUTTONS[3], spot_b),
    ]

    for num, hero_btn, spot in plan:
        print(f"  Hero {num} -> {spot}")
        try:
            pyautogui.moveTo(*hero_btn)
            pyautogui.click()
        except pyautogui.FailSafeException:
            print(f"    ! fail-safe on hero {num} select")
            continue
        time.sleep(HERO_SELECT_DELAY)

        cx, cy = spot
        for _ in range(HERO_TAPS_PER_SPOT):
            try:
                pyautogui.moveTo(cx, cy)
                pyautogui.click()
            except pyautogui.FailSafeException:
                print(f"    ! fail-safe on hero {num} tap")
                break
            time.sleep(HERO_TAP_DELAY)
        time.sleep(HERO_BETWEEN_DELAY)


def activate_hero_abilities():
    print(f"\n=== Activating hero abilities (waiting {HERO_ABILITY_WAIT}s first) ===")
    time.sleep(HERO_ABILITY_WAIT)
    for i, hero_btn in enumerate(HERO_BUTTONS, 1):
        print(f"  Ability {i} -> {hero_btn}")
        try:
            pyautogui.moveTo(*hero_btn)
            pyautogui.click()
        except pyautogui.FailSafeException:
            print(f"    ! fail-safe on ability {i}")
            continue
        time.sleep(HERO_ABILITY_DELAY)


def full_deploy(win, cfg):
    print(f"Waiting {BASE_PREVIEW_WAIT}s on base preview before zooming out...")
    time.sleep(BASE_PREVIEW_WAIT)

    zoom_out_and_center(win, times=ZOOM_OUT_TIMES)

    if not select_gollum_card(win, cfg):
        print("  ! gollum card not found, skipping deploy")
        return False

    deploy_goblins()
    deploy_heroes()
    activate_hero_abilities()

    print("=== Deploy complete ===\n")
    return True


# =========================================================================
# STORAGE CHECK
# =========================================================================
def is_storage_full(res):
    if not res:
        return False
    gold   = res.get("gold")   or 0
    elixir = res.get("elixir") or 0
    return gold >= MAX_GOLD or elixir >= MAX_ELIXIR


# =========================================================================
# ATTACK CYCLE
# =========================================================================
def run_one_attack_cycle(win, cfg, resource_regions, storage_check, auto_deploy):
    """auto_deploy=True  -> goblins/heroes/abilities (modes 1 & 2)
       auto_deploy=False -> just wait MANUAL_DEPLOY_WAIT, you play (mode 3)"""
    print("\n" + "#" * 60)
    print("# Starting attack cycle")
    print("#" * 60)

    before = None
    if storage_check:
        print("Scanning resources before attack...")
        before = read_resources(resource_regions, verbose=True)
        print_resources("BEFORE", before)

        if is_storage_full(before):
            gold   = (before.get("gold")   or 0) if before else 0
            elixir = (before.get("elixir") or 0) if before else 0
            print(f"\n*** Storage full: gold={gold:,}  elixir={elixir:,} ***")
            print("*** Stopping. ***\n")
            return False

        if before and before.get("gold") is not None and before.get("elixir") is not None:
            print(f"[loot] remaining to cap: "
                  f"gold={MAX_GOLD - before['gold']:,}  "
                  f"elixir={MAX_ELIXIR - before['elixir']:,}")

    if not wait_and_click(win, cfg, "attack_menu", "Home Attack! icon"):
        return True
    time.sleep(POST_CLICK_PAUSE)

    if not wait_and_click(win, cfg, "find_match", "Find a Match"):
        return True

    wait_until_gone(win, cfg, "find_match", "matchmaking", timeout=MATCHMAKING_TIMEOUT)
    print("Base found, confirming attack...")
    time.sleep(POST_CLICK_PAUSE)

    if not wait_and_click(win, cfg, "attack_go", "Attack! (base preview)"):
        return True

    if auto_deploy:
        full_deploy(win, cfg)
        print(f"Waiting {BATTLE_END_WAIT}s for the battle to end...")
        try:
            time.sleep(BATTLE_END_WAIT)
        except KeyboardInterrupt:
            print("  [Ctrl+C] skipping wait...")
    else:
        # Manual deploy window.
        print(f"\n>>> YOU HAVE THE CONTROLS. Deploy manually now.")
        print(f">>> Bot resumes in {MANUAL_DEPLOY_WAIT:.0f}s "
              f"(Ctrl+C skips the rest of the wait and proceeds).\n")
        try:
            time.sleep(MANUAL_DEPLOY_WAIT)
        except KeyboardInterrupt:
            print("  [Ctrl+C] skipping manual-deploy wait...")

    if not wait_and_click(win, cfg, "end_battle", "End Battle"):
        return True
    time.sleep(POST_CLICK_PAUSE)

    if not click_surrender_okay(win, cfg):
        return True
    time.sleep(POST_CLICK_PAUSE)

    if not wait_and_click(win, cfg, "return_home", "Return Home", timeout=15):
        return True

    if storage_check:
        time.sleep(1.2)
        after = read_resources(resource_regions, verbose=True)
        print_resources("AFTER", after)

        if before and after:
            def delta(k):
                a = after.get(k)
                b = before.get(k)
                if a is None or b is None:
                    return None
                return a - b

            gained = {k: delta(k) for k in
                      ("gold", "elixir", "dark_elixir", "gems")}

            def fmt_g(v):
                return f"{v:+,}" if v is not None else "n/a"

            print(f"[loot] GAINED  gold={fmt_g(gained['gold'])}  "
                  f"elixir={fmt_g(gained['elixir'])}  "
                  f"de={fmt_g(gained['dark_elixir'])}  "
                  f"gems={fmt_g(gained['gems'])}")

            if gained["gold"] is not None or gained["elixir"] is not None:
                log_loot_row(before, after, gained)

    print("=== Cycle complete, back home ===\n")
    return True


def mode_full_auto(cfg, storage_check, auto_deploy=True):
    if storage_check:
        print("\nFULL AUTO + STORAGE CHECK")
        print(f"Stop condition: gold >= {MAX_GOLD:,} or elixir >= {MAX_ELIXIR:,}\n")
    elif auto_deploy:
        print("\nFULL AUTO (no storage check)")
        print("Running forever. Ctrl+C to return to menu.\n")
    else:
        print("\nAUTO ATTACK ONLY (no deploy)")
        print(f"Waits {MANUAL_DEPLOY_WAIT:.0f}s each cycle for your manual deploy.")
        print("Ctrl+C to return to menu.\n")

    resource_regions = None
    if storage_check:
        resource_regions = load_regions()
        print(f"Resource regions: {resource_regions}\n")

    win = ensure_game_running()

    try:
        while True:
            cfg = load_config()
            keep_going = run_one_attack_cycle(win, cfg, resource_regions,
                                              storage_check, auto_deploy)
            if not keep_going:
                print("Storage full - back to menu.")
                break
            time.sleep(1.5)
    except KeyboardInterrupt:
        print("\nCtrl+C - back to menu.")


# =========================================================================
# TEST / CALIBRATION MODE
# =========================================================================
def mode_test(cfg, rounds=15):
    """Scans for every asset repeatedly WITHOUT clicking anything, printing
    score + how long each match_template() call took. Use this to:
      - tune cfg['confidence'] thresholds (raise if you get false positives,
        lower if a real icon never crosses the threshold)
      - see the real effect of --gpu / --fast on speed
      - sanity-check that assets still match after a UI update
    """
    print("\nTEST / CALIBRATE (no clicks, Ctrl+C to stop early)")
    print(f"gpu={'ON' if (USE_GPU and GPU_READY) else 'off'}  "
          f"fast={'ON' if FAST_MODE else 'off'}  "
          f"capture={'mss' if mss_mod else 'pyautogui'}\n")

    win = ensure_game_running()
    totals = {k: {"scores": [], "times_ms": [], "hits": 0} for k in ASSETS}

    try:
        for r in range(1, rounds + 1):
            region = get_region(win)
            screen = capture(region)
            print(f"--- round {r}/{rounds} ---")
            for asset_key in ASSETS:
                t0 = time.perf_counter()
                match = match_template(screen, asset_key, cfg, thorough=True)
                dt_ms = (time.perf_counter() - t0) * 1000

                if "error" in match:
                    print(f"  {asset_key:<16} ERROR: {match['error']}")
                    continue

                totals[asset_key]["scores"].append(match["score"])
                totals[asset_key]["times_ms"].append(dt_ms)
                if match["found"]:
                    totals[asset_key]["hits"] += 1

                status = "FOUND" if match["found"] else "miss "
                print(f"  {asset_key:<16} {status} score={match['score']:.3f} "
                      f"(need {match['threshold']:.2f}) scale={match['scale']:.2f} "
                      f"{dt_ms:6.1f} ms")
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n  [Ctrl+C] stopping test early...")

    print("\n=== Summary (avg score / avg time / hit rate) ===")
    for asset_key, d in totals.items():
        if not d["scores"]:
            print(f"  {asset_key:<16} no successful reads")
            continue
        n = len(d["scores"])
        avg_score = sum(d["scores"]) / n
        avg_ms = sum(d["times_ms"]) / n
        hit_rate = 100.0 * d["hits"] / n
        print(f"  {asset_key:<16} avg_score={avg_score:.3f}  "
              f"avg_time={avg_ms:6.1f} ms  hit_rate={hit_rate:5.1f}%  (n={n})")
    print()


# =========================================================================
# MAIN
# =========================================================================
def parse_args():
    p = argparse.ArgumentParser(description="Clash of Clans Auto-Attack Bot")
    p.add_argument("--gpu", action="store_true",
                    help="use cv2.cuda template matching if a CUDA device + "
                         "opencv-contrib-python (CUDA build) are available; "
                         "silently falls back to CPU otherwise")
    p.add_argument("--fast", action="store_true",
                    help="stop the multi-scale search at the first scale "
                         "that clears the confidence threshold instead of "
                         "always checking all 16 scales (faster, slightly "
                         "less accurate reported score - use mode 4 first "
                         "to make sure your thresholds are solid)")
    p.add_argument("--rounds", type=int, default=15,
                    help="number of scan rounds for mode 4 (test/calibrate)")
    return p.parse_args()


def main():
    global USE_GPU, FAST_MODE

    args = parse_args()
    USE_GPU = args.gpu
    FAST_MODE = args.fast

    print("Loading config...")
    cfg = load_config()
    ensure_log_header()
    if USE_GPU or FAST_MODE:
        print(f"Flags: gpu={USE_GPU}  fast={FAST_MODE}  "
              f"(GPU is only confirmed once a game mode loads OpenCV)")

    while True:
        print_menu()
        try:
            choice = input("Choose [0-4]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if choice == "0":
            print("Exiting.")
            break

        elif choice == "1":
            if not _ensure_full_deps(need_ocr=False):
                continue
            mode_full_auto(cfg, storage_check=False, auto_deploy=True)

        elif choice == "2":
            if not _ensure_full_deps(need_ocr=True):
                continue
            mode_full_auto(cfg, storage_check=True, auto_deploy=True)

        elif choice == "3":
            if not _ensure_full_deps(need_ocr=False):
                continue
            mode_full_auto(cfg, storage_check=False, auto_deploy=False)

        elif choice == "4":
            if not _ensure_full_deps(need_ocr=False):
                continue
            mode_test(cfg, rounds=args.rounds)

        else:
            print(f"Unknown choice: {choice!r}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        # Silences the "Exception ignored on threading shutdown" warning
        # that pygetwindow / pyautogui trigger on Ctrl+C during exit.
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except Exception:
            pass