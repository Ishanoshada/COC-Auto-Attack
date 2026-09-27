A Python-based automation bot for Clash of Clans running on the Google Play Games emulator (Windows). Handles attack initiation, troop deployment, hero abilities, and optional loot tracking via OCR.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Setting up Google Play Games](#setting-up-google-play-games)
- [First-Time Calibration](#first-time-calibration)
- [Usage](#usage)
- [Menu Options](#menu-options)
- [Configuration](#configuration)
- [Tuning & Performance](#tuning--performance)
- [Files Explained](#files-explained)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)

---

## Features

- **Full-auto attack loop** — attacks endlessly, deploys goblins + heroes + abilities, ends battle, returns home, repeats.
- **Lazy dependency loading** — heavy packages (`opencv`, `numpy`, `rapidocr`) only import when the chosen mode needs them.
- **Multi-scale template matching** — detects buttons/icons across 16 scales, so it works in fullscreen, windowed, or any resize.
- **Auto-adapting scale memory** — remembers the scale each button matched at, and tries it first on subsequent scans.
- **Optional OCR loot tracking** — reads gold / elixir / dark elixir / gems from the top bar using `rapidocr-onnxruntime` and can stop when storage fills.
- **Manual-deploy mode** — bot clicks through the menus but lets you deploy the army yourself.
- **Test / calibrate mode** — scans every icon repeatedly without clicking, so you can tune thresholds and measure detection speed.
- **Optional GPU acceleration** via `cv2.cuda` (falls back to CPU silently).
- **Optional `mss` capture** — faster than `pyautogui.screenshot` when available.

---

## Requirements

| | Minimum |
|---|---|
| OS | Windows 10 / 11 |
| Python | 3.9 – 3.12 |
| RAM | 4 GB |
| Storage | ~500 MB (with OCR dependencies) |

You must have a **Clash of Clans account loaded in Google Play Games**. See [Setting up Google Play Games](#setting-up-google-play-games).

---

## Installation

Clone or download the repo, then install the required packages.

### Core (required for modes 1 and 3)

```bash
pip install pyautogui pygetwindow opencv-python numpy pillow
```

### Optional but recommended

```bash
pip install mss
```
Uses direct OS framebuffer access for screen capture — 3–8× faster polling than `pyautogui.screenshot`.

### For storage-check mode (option 2)

```bash
pip install rapidocr-onnxruntime
```
Small ONNX-based OCR engine (~50 MB, no PyTorch needed).

### For GPU acceleration (optional, advanced)

Requires a CUDA-capable NVIDIA GPU and an OpenCV build with CUDA:

```bash
pip install opencv-contrib-python
```

Note: stock PyPI `opencv-contrib-python` does **not** ship CUDA. You'll need to build OpenCV yourself with `-DWITH_CUDA=ON`. The bot silently falls back to CPU if CUDA isn't available.

---

## Setting up Google Play Games

The bot is designed to work with **Google Play Games on PC** (the official Google emulator for Windows). It does **not** rely on ADB.

### 1. Install Google Play Games

Download from the official site:

https://play.google.com/googleplaygames

Run the installer, sign in with your Google account.

### 2. Install Clash of Clans

In the Google Play Games app, search for **Clash of Clans** and install it.

### 3. Launch Clash of Clans

Open the game from the Play Games library. Log into your Supercell account.

### 4. Set the window title (important)

The bot finds the game by looking for `"Clash of Clans"` in a window title. Google Play Games normally names the window:

```
Clash of Clans - Google Play Games
```

That matches. **If your window title is different** (e.g. just `Clash of Clans` without the suffix, or something custom), edit `WINDOW_TITLE_KEYWORD` at the top of `main.py`:

```python
WINDOW_TITLE_KEYWORD = "Clash of Clans"
```

The match is case-insensitive and looks for the keyword anywhere in the title.

### 5. Recommended emulator settings

In the Google Play Games settings:

| Setting | Value | Why |
|---|---|---|
| Display mode | **Windowed** (not fullscreen) | Easier to alt-tab to the terminal |
| Resolution | **1920 × 1080** or matching your monitor | Keeps coordinates predictable |
| Frame rate | 60 FPS | Smooth UI so template matching is reliable |
| Graphics quality | Medium / High | Low can distort icons |

### 6. Recommended game settings

Inside CoC:

| Setting | Value |
|---|---|
| Screen shake | Off |
| Sound effects | Off (or reduced) |
| Music | Off |
| Zoom | Any (bot zooms out itself) |

### 7. Position the window

The bot uses **absolute screen coordinates** for deploy. Keep the CoC window at the **same position and size** every session. Dragging or resizing it between runs will misalign the deploy pattern.

**Fastest option:** fullscreen the emulator on your primary monitor and never move it.

### 8. Disable screen savers / display sleep

Windows → Settings → System → Power & sleep → **Sleep: Never**.

---

## First-Time Calibration

Before running the bot, verify the icon-detection templates work on your machine.

### 1. Take screenshots of each button

Run CoC, get to the home screen, and take a screenshot. Crop **tight** boxes around each of these buttons (using Windows Snipping Tool, ShareX, or any screenshot utility):

| File | What to crop |
|---|---|
| `attack_btn_menu_1.png` | The **Attack!** button on the home screen (bottom-left) |
| `find_match_btn_2.png` | **Find a Match** button on the attack menu |
| `attack_btn_go_3.png` | **Attack!** button on the base-preview screen |
| `gollum_click_4.png` | The goblin troop card on the battle bar |
| `end_battlel_5.png` | **End Battle** button (bottom-right during battle) |
| `surrender_okay_6.png` | The surrender confirmation dialog |
| `return_home_7.png` | **Return Home** button (post-battle screen) |

Save each as PNG in the same folder as `main.py`.

**Tip:** crop only the button itself — no surrounding UI, no text labels. The tighter the crop, the more accurate the match.

### 2. Verify with test mode

Run:

```bash
python main.py
```

Choose **4) Test / calibrate asset detection**. The bot scans the current screen for all seven icons and prints:

```
--- round 1/15 ---
  attack_menu      FOUND score=0.883 (need 0.82) scale=0.95    42.1 ms
  find_match       miss  score=0.412 (need 0.72) scale=0.85    38.7 ms
  ...
```

**What to look for:**

- Icons that are actually on screen should show `FOUND`.
- Scores of real matches should be **at least 0.05 above the threshold**. If `attack_menu` matches at 0.83 with a threshold of 0.82, that's too tight — lower the threshold to 0.78.
- Icons that aren't on screen should be well below threshold (e.g. 0.3–0.5).

If a real icon is scoring below the threshold, edit `config.json`:

```json
{
  "confidence": {
    "attack_menu": 0.78,
    "find_match": 0.68,
    ...
  }
}
```

### 3. Verify deploy coordinates

The deploy loop (`GOBLIN_LOOP`) uses **absolute screen coordinates**. These are calibrated for **1920 × 1080 fullscreen**. If your window is different, you need to recalibrate.

See [Tuning & Performance](#tuning--performance) below.

---

## Usage

```bash
python main.py
```

Optionally with flags:

```bash
python main.py --fast             # faster scans, first-hit scale stop
python main.py --gpu              # try CUDA template matching
python main.py --fast --gpu
python main.py --rounds 30        # longer test runs (mode 4)
```

You'll get a menu:

```
============================================================
  Clash of Clans Auto-Attack Bot
============================================================
  1) Full auto
     attacks forever, auto-deploys goblins + heroes + abilities

  2) Full auto + storage check
     same as 1, but stops when gold or elixir reaches 24M

  3) Auto attack only (NO deploy)
     clicks Attack / Find Match / Attack!, then waits
     60s for YOU to deploy manually,
     then clicks End Battle / Surrender / Return Home

  4) Test / calibrate asset detection
     no clicking - repeatedly scans for every icon and prints
     live score + timing per asset

  0) Exit
============================================================
  gpu=off   fast=off   capture=mss
Choose [0-4]:
```

Make sure the CoC window is **visible and focused** before choosing a mode. Ctrl+C anywhere returns to the menu (or exits cleanly).

---

## Menu Options

### 1) Full auto

The bot runs an infinite loop:

1. Open attack menu → Find a Match → wait for matchmaking
2. Confirm on base preview
3. Wait 2s → **Ctrl + − twice** to zoom out → select gollum card
4. Deploy goblins in 8 perimeter passes (2–4s pause between)
5. Deploy heroes to 2 random perimeter spots (2 heroes per spot)
6. Wait 6s → activate all 4 hero abilities
7. Wait 6s → click **End Battle** → **Surrender** → **OK** → **Return Home**
8. Repeat

Ctrl+C returns to the menu. **Does not stop on storage full**.

### 2) Full auto + storage check

Same as mode 1, but before each cycle it:

- Reads gold / elixir / dark elixir / gems via OCR
- Prints a `BEFORE` line
- Stops the loop when **gold ≥ 24M** OR **elixir ≥ 24M**
- After the battle, reads again and logs `AFTER` + `GAINED`

Loot history is written to `loot_log.csv`.

Requires `rapidocr-onnxruntime` and a working `resource_regions.json` (see below).

### 3) Auto attack only (NO deploy)

Bot handles the menu clicks but **does not deploy troops**. It:

1. Clicks Attack → Find a Match → Attack!
2. Prints `>>> YOU HAVE THE CONTROLS` and waits **60 seconds**
3. You deploy the army manually in that window (Ctrl+C on the terminal to skip ahead early)
4. Bot clicks End Battle → Surrender → Return Home → repeats

Change `MANUAL_DEPLOY_WAIT` at the top of `main.py` to adjust the window.

### 4) Test / calibrate

Scans the screen for all 7 icons per round, prints score + timing + hit rate. Nothing is clicked. Use `--rounds N` to change the number of rounds. Great for:

- Tuning `confidence` thresholds
- Measuring the effect of `--fast` or `--gpu`
- Checking whether icons still match after a game UI update

---

## Configuration

### `config.json`

Auto-created on first run.

```json
{
  "confidence": {
    "attack_menu":      0.82,
    "find_match":       0.72,
    "attack_go":        0.82,
    "gollum":           0.85,
    "end_battle":       0.68,
    "surrender_dialog": 0.90,
    "return_home":      0.82
  },
  "scales": [0.5, 0.6, ..., 1.5]
}
```

| Key | Effect |
|---|---|
| `confidence.<asset>` | Minimum match score to consider the icon "found". Lower = more lenient. |
| `scales` | Allowed resize factors for the template. Wider range = slower but more robust. |

**`preferred_scale`** is added automatically after the first successful match of each asset, so subsequent scans try that scale first.

### `resource_regions.json` (mode 2 only)

Crop rectangles for OCR, as `[left, top, width, height]` relative to the CoC window.

The bot ships with sensible defaults for a 1920×1080 fullscreen window. If your layout is different, run a small helper to draw the regions — or edit the JSON by hand. Each rect should surround **just the digits** of the resource counter (e.g. `12 142 145`), not the icon next to it.

---

## Tuning & Performance

### Speeding things up

| Flag / setting | Speedup | Trade-off |
|---|---|---|
| `--fast` | ~2–3× faster scanning | Reported score isn't the true best score |
| `pip install mss` | 3–8× faster capture | None |
| `--gpu` (with CUDA OpenCV) | 5–20× faster matching | Requires NVIDIA GPU + custom OpenCV build |
| Lower `SCAN_INTERVAL` in `main.py` | Faster retry loop | Higher CPU |

The biggest wins, in order: **`mss` > `--fast` > `--gpu`**.

### Adjusting deploy pattern

Edit `GOBLIN_LOOP`, `HERO_BUTTONS`, and `DEPLOY_MIN_X/Y` / `DEPLOY_MAX_X/Y` at the top of `main.py`.

The default loop is calibrated for **1920 × 1080 fullscreen**. If your CoC window is:

- **Different position**: add the offset to every point's x/y.
- **Different size**: scale every point by `(your_width / 1920, your_height / 1080)`.

A quick way to recalibrate: use `click_logger.py`-style recording (see repo history) to log your own deploy clicks, then fit new points.

### Adjusting OCR regions

If mode 2 reads wrong values (e.g. gold reads as `61,997,722` when it should be `4,626,020`):

1. Open the debug output by setting `SAVE_DEBUG = True` in `main.py`.
2. Run mode 2 once.
3. Check `debug_crops/gold.png` — it should show **only the digits**.
4. If it's showing the wrong part of the screen, adjust the rect in `resource_regions.json` manually.

Sanity check: values over 100,000,000 are auto-dropped as bad reads, so a misread can't falsely trigger "storage full".

---

## Files Explained

```
Repo/
├── main.py                     # The bot
├── config.json                 # Thresholds, scales, preferred scale memory
├── resource_regions.json       # OCR crop rects (mode 2 only)
├── bot_log.csv                 # Every click attempt (score, position, timing)
├── loot_log.csv                # Loot gained per battle (mode 2 only)
├── attack_btn_menu_1.png       # Template: Home screen "Attack!" button
├── find_match_btn_2.png        # Template: "Find a Match" button
├── attack_btn_go_3.png         # Template: Base preview "Attack!" button
├── gollum_click_4.png          # Template: Goblin troop card
├── end_battlel_5.png           # Template: "End Battle" button
├── surrender_okay_6.png        # Template: Surrender confirm dialog
└── return_home_7.png           # Template: "Return Home" button
```

Files the bot creates:

- `config.json` — on first run
- `bot_log.csv` — on first click attempt
- `loot_log.csv` — on first completed storage-check cycle
- `debug_crops/` — only if `SAVE_DEBUG = True`

---

## Troubleshooting

### "No window matching 'Clash of Clans' found"

- CoC isn't running yet — the bot will try to launch the emulator at `GAME_EXE_PATH`.
- The window title doesn't match. Run `python -c "import pygetwindow; print([w.title for w in pygetwindow.getAllWindows()])"` and update `WINDOW_TITLE_KEYWORD` accordingly.

### Icons not detected (score below threshold)

- Recrop the template PNGs — they should be **tight** around the button.
- Try mode 4 to see live scores. If a real icon scores 0.75 and threshold is 0.82, lower the threshold to `0.70`.
- If the game UI changed (event, update), all templates need re-cropping.

### Icons detected but the wrong thing gets clicked

- The template is too loose — includes surrounding UI, so it matches multiple places. Recrop tighter.
- Or two assets are visually similar (e.g. **Attack!** on home vs base preview). Make sure `attack_btn_menu_1.png` and `attack_btn_go_3.png` are distinct crops.

### Deploy clicks land in the wrong place

- You moved or resized the CoC window. Either restore it to the original position, or recalibrate `GOBLIN_LOOP`.
- Check `DEPLOY_MAX_Y` — if it's too high, taps land on the troop bar and switch troops.

### Storage check reads garbage values

- Run mode 4 once to confirm OCR isn't corrupted.
- Set `SAVE_DEBUG = True`, run mode 2, open `debug_crops/*.png`. The crops should show exactly the digits of each counter.
- If a crop is off, edit `resource_regions.json` manually.

### `Exception ignored on threading shutdown: KeyboardInterrupt`

This is a Python warning from `pygetwindow`'s background threads on Ctrl+C. It's cosmetic. The current `main.py` silences it via a `signal.SIGINT` ignore in the `finally` block.

### `pip install rapidocr-onnxruntime` fails

Requires Python 3.9–3.12 and a working `onnxruntime`. If install fails, try:

```bash
pip install onnxruntime
pip install rapidocr-onnxruntime
```

Or skip mode 2 and use modes 1 or 3 (they don't need OCR).

### Bot detects the window but everything times out

- The CoC window must be **visible** (not minimized, not behind other windows).
- If running on a second monitor, ensure your primary display matches where the game is.
- Windows display scaling (125%, 150%) can shift coordinates. Set it to 100% for the primary monitor.

---

## Disclaimer

This project is provided for **educational purposes only**. Automating Clash of Clans violates Supercell's Terms of Service and can result in a **permanent account ban**.

- Use only on a throwaway account.
- Do not run the bot while streaming, recording, or sharing your screen.
- Do not share your account credentials or API keys with anyone.

The authors take no responsibility for any consequences of using this software.