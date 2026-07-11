# Video Kiosk — Installation & Usage

## File layout

Place all files in `/home/pi/kiosk/`:

```
/home/pi/kiosk/
  kiosk.py        — main kiosk script
  kiosk.conf      — configuration (paths, debounce, video assignments)
```

The touchphat venv lives separately at `/home/pi/touchphat/venv/` as already set up.

---

## Installation

### 1. Copy files

```bash
mkdir -p /home/pi/kiosk
cp kiosk.py kiosk.conf /home/pi/kiosk/
```

### 2. Install the systemd service

```bash
sudo cp kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kiosk.service
```

### 3. Make sure the pi user is in the video group

```bash
groups pi   # should include 'video'
# If not:
sudo usermod -aG video pi
```

### 4. Test manually before enabling the service

```bash
cd /home/pi/kiosk
/home/pi/touchphat/venv/bin/python kiosk.py
```

If everything looks good, start the service:

```bash
sudo systemctl start kiosk.service
sudo journalctl -u kiosk -f   # watch logs
```

---

## Configuration (kiosk.conf)

| Key                     | Description                                              |
|-------------------------|----------------------------------------------------------|
| `PLAYER_BIN`            | Path to mpv                                              |
| `YTDLP_BIN`             | Path to yt-dlp                                           |
| `PLAYER_ARGS`           | Arguments passed to mpv (space-separated)                |
| `INPUT_DEBOUNCE_SECONDS`| Seconds to ignore button input after a press (default 7) |
| `KEY_A` / `B` / `C` / `D` | Full path to a local file, or a YouTube URL           |

After editing `kiosk.conf`, restart the service:

```bash
sudo systemctl restart kiosk.service
```

---

## Behaviour

- On startup, the video assigned to **A** plays automatically.
- Pressing **A/B/C/D** switches to that button's video.
- **Back** and **Enter** do nothing (reserved for future use).
- After a button press, further presses are ignored for `INPUT_DEBOUNCE_SECONDS`
  to prevent a new video starting while the previous one is still shutting down.
- If mpv exits unexpectedly (e.g. a YouTube stream dies), the watchdog restarts
  it within 5 seconds.
- The systemd service restarts the entire kiosk if it crashes.
- yt-dlp is updated automatically each time the service starts.
- So is the program itself.

---

## Logs

```bash
sudo journalctl -u kiosk -f
```
