#!/usr/bin/env python3
"""
Safe bot restart script.
Stops the running bot, waits for cleanup, then starts a new instance.
Prevents double-running by enforcing sequential start/stop.
"""

import os
import sys
import subprocess
import time
import signal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(SCRIPT_DIR, "AI-FSY.py")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".bot.lock")
PYTHON_EXE = sys.executable

def read_pid_from_lock():
    """Read the PID from the lock file, if it exists."""
    if not os.path.exists(LOCK_FILE):
        return None
    try:
        with open(LOCK_FILE, "r") as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None

def pid_is_running(pid):
    """Check if a PID is still running."""
    try:
        if sys.platform == "win32":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False

def stop_running_instance():
    """Stop the currently running bot instance if one exists."""
    pid = read_pid_from_lock()
    if pid is None:
        print("[restart] No lock file found. Starting fresh.")
        return True
    
    if not pid_is_running(pid):
        print(f"[restart] Old process (PID {pid}) is not running. Cleaning up lock file.")
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass
        return True
    
    print(f"[restart] Found running bot (PID {pid}). Sending stop signal...")
    try:
        if sys.platform == "win32":
            # On Windows, send Ctrl+C via taskkill
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5
            )
        else:
            # On Unix, send SIGTERM
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        print(f"[restart] Error stopping process: {e}")
        return False
    
    # Wait up to 5 seconds for graceful shutdown
    for attempt in range(50):
        time.sleep(0.1)
        if not pid_is_running(pid):
            print(f"[restart] Process stopped cleanly.")
            break
    else:
        # After 5 seconds, still running
        print(f"[restart] WARNING: Process did not stop gracefully after 5 seconds.")
        print(f"[restart] Forcing termination...")
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception as e:
            print(f"[restart] Error force-killing: {e}")
    
    # Wait for lock file to be cleaned up by the stopped process
    print("[restart] Waiting for lock file to be released...")
    for attempt in range(50):
        time.sleep(0.1)
        if not os.path.exists(LOCK_FILE):
            print("[restart] Lock file released.")
            return True
    
    # If lock file still exists, force remove it
    print("[restart] Lock file still exists. Force removing...")
    try:
        os.remove(LOCK_FILE)
        print("[restart] Lock file force-removed.")
    except OSError as e:
        print(f"[restart] WARNING: Could not remove lock file: {e}")
    
    return True

def start_bot():
    """Start a new instance of the bot."""
    print(f"[restart] Starting bot: {MAIN_SCRIPT}")
    try:
        subprocess.Popen(
            [PYTHON_EXE, MAIN_SCRIPT],
            cwd=SCRIPT_DIR,
            stdout=None,
            stderr=None,
        )
        print("[restart] Bot started successfully.")
        return True
    except Exception as e:
        print(f"[restart] ERROR: Could not start bot: {e}")
        return False

def main():
    print("[restart] ====== Bot Restart Sequence ======")
    
    # Step 1: Stop any running instance
    if not stop_running_instance():
        print("[restart] FAILED: Could not stop running instance.")
        sys.exit(1)
    
    # Step 2: Wait longer for all file handles to release (critical for Windows)
    time.sleep(3)
    
    # Step 3: Double-check lock file is gone
    if os.path.exists(LOCK_FILE):
        print("[restart] Lock file still present. Force removing...")
        try:
            os.remove(LOCK_FILE)
        except OSError as e:
            print(f"[restart] WARNING: Could not remove lock file: {e}")
    
    # Step 4: Start the bot
    if not start_bot():
        print("[restart] FAILED: Could not start bot.")
        sys.exit(1)
    
    print("[restart] ====== Restart Complete ======")
    sys.exit(0)

if __name__ == "__main__":
    main()
