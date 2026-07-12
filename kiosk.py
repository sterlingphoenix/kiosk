#!/usr/bin/env python3
"""
kiosk.py — Touch pHAT video kiosk
Plays videos assigned to A/B/C/D buttons via mpv.

Button sequences:
  Back, Enter, Back, Enter          -> halt the system
  Back, Back, Back, Back,           -> enable the ssh service (takes effect
  Enter, Enter, Enter, Enter           next boot); confirmed by an LED blink
"""

import os
import subprocess
import sys
import time
import logging

# ---------------------------------------------------------------------------
# Logging — stderr goes to journal, nothing lands on tty2
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("kiosk")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kiosk.conf")

def load_config(path):
    config = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip().upper()] = value.strip()
    except FileNotFoundError:
        log.error(f"Config file not found: {path}")
        sys.exit(1)
    return config

# ---------------------------------------------------------------------------
# VT / Display
# ---------------------------------------------------------------------------
VT_DEV = "/dev/tty2"
WIDTH  = 80

YELLOW = "\033[1;33m"
CYAN   = "\033[1;36m"
WHITE  = "\033[1;37m"
RESET  = "\033[0m"

def setup_vt():
    try:
        subprocess.run(["sudo", "chvt", "2"], check=True)
        log.info("chvt 2 OK")
    except Exception as e:
        log.warning(f"chvt 2 failed: {e}")
    return sys.stdout

def teardown_vt(vt):
    try:
        vt.write("\033[2J\033[H\033[?25h")
        vt.flush()
    except Exception:
        pass
    try:
        subprocess.run(["sudo", "chvt", "1"], check=True)
    except Exception as e:
        log.warning(f"chvt 1 failed: {e}")

def draw_marquee(vt, description):
    """
    Render the NOW PLAYING marquee on tty2.

    Line 1:  * * * * * * * * ... (yellow, 80 chars)
    Line 2:  (blank)
    Line 3:  *    NOW PLAYING    * (yellow borders, cyan text centred)
    Line 4:  *  [description]    * (yellow borders, white text centred)
    Line 5:  (blank)
    Line 6:  * * * * * * * * ... (yellow, 80 chars)
    """
    lights      = YELLOW + ("* " * 40).rstrip() + RESET
    inner_width = WIDTH - 4
    border_l    = YELLOW + "* " + RESET
    border_r    = YELLOW + " *" + RESET

    line_np   = border_l + CYAN  + "NOW PLAYING".center(inner_width) + RESET + border_r
    line_desc = border_l + WHITE + description.center(inner_width)   + RESET + border_r

    vt.write(
        "\033[2J\033[H"     # clear + home
        "\033[?25l"         # hide cursor
        + lights + "\n"
        + "\n"
        + line_np   + "\n"
        + line_desc + "\n"
        + "\n"
        + lights + "\n"
    )
    vt.flush()

# ---------------------------------------------------------------------------
# Touch pHAT LEDs
# ---------------------------------------------------------------------------
LED_PADS = ["A", "B", "C", "D", "Back", "Enter"]

def blink_leds(count=3, interval=0.1):
    """
    Blink all six Touch pHAT LEDs as visual confirmation.
    Runs in the calling (callback) thread and blocks it for
    count * 2 * interval seconds. Auto touch-to-light behaviour
    resumes on its own afterward (verified on hardware).
    """
    import touchphat
    try:
        for _ in range(count):
            for pad in LED_PADS:
                touchphat.set_led(pad, True)
            time.sleep(interval)
            for pad in LED_PADS:
                touchphat.set_led(pad, False)
            time.sleep(interval)
    except Exception as e:
        log.warning(f"LED blink failed: {e}")

# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------
class VideoPlayer:
    def __init__(self, player_bin, player_args):
        self.player_bin  = player_bin
        self.player_args = player_args
        self._proc       = None

    def play(self, target):
        self.stop()
        cmd = [self.player_bin] + self.player_args + [target]
        log.info(f"Starting: {target}")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.error(f"Player binary not found: {self.player_bin}")
        except Exception as e:
            log.error(f"Failed to start player: {e}")

    def stop(self):
        if self._proc is not None and self._proc.poll() is None:
            log.info("Stopping current video...")
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    log.warning("mpv didn't exit cleanly; killing it.")
                    self._proc.kill()
                    self._proc.wait()
            except Exception as e:
                log.error(f"Error stopping player: {e}")
            finally:
                self._proc = None

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None

    def restart_if_dead(self, target):
        if not self.is_running() and target is not None:
            log.warning("Player exited unexpectedly; restarting.")
            self.play(target)

# ---------------------------------------------------------------------------
# Kiosk
# ---------------------------------------------------------------------------
class Kiosk:
    def __init__(self, config, vt):
        self.vt            = vt
        self.player_bin    = config.get("PLAYER_BIN", "/usr/bin/mpv")
        self.ytdlp_bin     = config.get("YTDLP_BIN", "/usr/local/bin/yt-dlp")
        self.debounce_secs = float(config.get("INPUT_DEBOUNCE_SECONDS", "7"))

        raw_args = config.get("PLAYER_ARGS", "")
        self.player_args = raw_args.split() + [
            f"--script-opts=ytdl_hook-ytdl_path={self.ytdlp_bin}"
        ]

        self.key_map = {
            "A": (config.get("KEY_A"), config.get("KEY_A_DESC", "Video A")),
            "B": (config.get("KEY_B"), config.get("KEY_B_DESC", "Video B")),
            "C": (config.get("KEY_C"), config.get("KEY_C_DESC", "Video C")),
            "D": (config.get("KEY_D"), config.get("KEY_D_DESC", "Video D")),
        }

        self.player          = VideoPlayer(self.player_bin, self.player_args)
        self.current_target  = None
        self.last_press_time = 0.0

    def _debounce_ok(self):
        return (time.monotonic() - self.last_press_time) >= self.debounce_secs

    def switch_to(self, key):
        if not self._debounce_ok():
            log.info(f"Key '{key}' ignored (debounce).")
            return
        entry = self.key_map.get(key)
        if not entry or not entry[0]:
            log.warning(f"No video assigned to key '{key}'.")
            return
        target, description = entry
        self.last_press_time = time.monotonic()
        self.current_target  = target
        draw_marquee(self.vt, description)
        self.player.play(target)

# ---------------------------------------------------------------------------
# Secret button sequences
# ---------------------------------------------------------------------------
class SequenceMatcher:
    """
    Tracks multiple independent button sequences. Each press is fed to
    advance(); when any sequence matches in full, its action fires and
    all progress counters reset.
    """
    def __init__(self):
        self._entries = []  # list of dicts: sequence, action, name, progress

    def add(self, name, sequence, action):
        self._entries.append({
            "name":     name,
            "sequence": sequence,
            "action":   action,
            "progress": 0,
        })

    def advance(self, key):
        fired = None
        for e in self._entries:
            if key == e["sequence"][e["progress"]]:
                e["progress"] += 1
                if e["progress"] == len(e["sequence"]):
                    fired = e
            else:
                # Reset, but allow this key to start the sequence over
                e["progress"] = 1 if key == e["sequence"][0] else 0

        if fired is not None:
            log.info(f"Sequence '{fired['name']}' complete — firing action.")
            for e in self._entries:      # reset everything after a match
                e["progress"] = 0
            fired["action"]()

# ---------------------------------------------------------------------------
# Watchdog / main loop
# ---------------------------------------------------------------------------
def watchdog_loop(kiosk, poll_interval=5):
    import touchphat

    log.info("Initializing Touch pHAT...")

    @touchphat.on_touch("A")
    def handle_a(event):
        kiosk.switch_to("A")

    @touchphat.on_touch("B")
    def handle_b(event):
        kiosk.switch_to("B")

    @touchphat.on_touch("C")
    def handle_c(event):
        kiosk.switch_to("C")

    @touchphat.on_touch("D")
    def handle_d(event):
        kiosk.switch_to("D")

    # --- secret sequences -------------------------------------------------
    def action_halt():
        log.info("Halting system.")
        kiosk.player.stop()
        teardown_vt(kiosk.vt)
        subprocess.run(["sudo", "halt"])

    def action_enable_ssh():
        log.info("Enabling ssh service (effective next boot).")
        try:
            subprocess.run(["sudo", "systemctl", "enable", "ssh"], check=True)
            log.info("ssh service enabled.")
        except Exception as e:
            log.error(f"Failed to enable ssh: {e}")
        blink_leds(count=3, interval=0.1)

    sequences = SequenceMatcher()
    sequences.add("shutdown", ["Back", "Enter", "Back", "Enter"], action_halt)
    sequences.add("enable_ssh",
                  ["Back", "Back", "Back", "Back", "Enter", "Enter", "Enter", "Enter"],
                  action_enable_ssh)

    @touchphat.on_touch("Back")
    def handle_back(event):
        sequences.advance("Back")

    @touchphat.on_touch("Enter")
    def handle_enter(event):
        sequences.advance("Enter")

    # touchphat fully initialised — safe to start mpv
    log.info("Kiosk starting. Auto-playing key A.")
    kiosk.switch_to("A")
    kiosk.last_press_time = 0.0  # don't block the first real keypress

    log.info("Listening for button presses. Press Ctrl-C to exit.")
    try:
        while True:
            time.sleep(poll_interval)
            kiosk.player.restart_if_dead(kiosk.current_target)
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    finally:
        kiosk.player.stop()
        teardown_vt(kiosk.vt)
        log.info("Kiosk stopped.")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    config = load_config(CONFIG_PATH)
    vt     = setup_vt()
    kiosk  = Kiosk(config, vt)
    watchdog_loop(kiosk)
