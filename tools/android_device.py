"""
tools/android_device.py — Android device control via uiautomator2

Provides a clean wrapper around uiautomator2 for agentic UAT use.
All methods are synchronous. Errors raise AndroidDeviceError.
"""
from __future__ import annotations
import os
import time
import logging
from pathlib import Path
from typing import Optional
import uiautomator2 as u2

from utils.config import get

logger = logging.getLogger(__name__)


class AndroidDeviceError(Exception):
    pass


class AndroidDevice:
    def __init__(self, serial: str | None = None):
        serial = serial or os.getenv("DEVICE_SERIAL") or get("device.serial") or None
        try:
            self.d = u2.connect(serial) if serial else u2.connect()
            self.serial = self.d.serial
            logger.info(f"Connected to device: {self.serial}")
        except Exception as e:
            raise AndroidDeviceError(f"Failed to connect to device: {e}") from e

    def screenshot(self, save_path: str | None = None) -> bytes:
        """Take a screenshot. If save_path given, saves to disk and returns bytes."""
        img = self.d.screenshot()
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(png_bytes)
        return png_bytes

    def get_ui_tree(self) -> str:
        """Return the UI accessibility hierarchy as XML string."""
        try:
            return self.d.dump_hierarchy()
        except Exception as e:
            raise AndroidDeviceError(f"Failed to dump UI hierarchy: {e}") from e

    def tap(self, x: int, y: int) -> None:
        """Tap at coordinates via adb shell input tap (avoids INJECT_EVENTS on MIUI)."""
        self.d.shell(f"input tap {x} {y}")
        time.sleep(get("uat.default_wait_ms", 1500) / 1000)

    def tap_text(self, text: str, exact: bool = False) -> bool:
        """Tap the first element containing text. Returns True if found.

        Uses physical touch coordinates (d.click(x, y)) instead of accessibility
        ACTION_CLICK to ensure navigation works on elements where accessibility
        click does not trigger the intent (e.g. MMT LOB tiles).
        """
        try:
            el = self.d(text=text) if exact else self.d(textContains=text)
            if not el.exists(timeout=2):
                return False
            info = el[0].info
            bounds = info["bounds"]
            cx = (bounds["left"] + bounds["right"]) // 2
            cy = (bounds["top"] + bounds["bottom"]) // 2
            self.d.click(cx, cy)
            time.sleep(get("uat.default_wait_ms", 1500) / 1000)
            return True
        except (u2.exceptions.UiObjectNotFoundError, Exception):
            return False

    def tap_resource_id(self, resource_id: str) -> bool:
        """Tap element by resource ID. Returns True if found."""
        try:
            self.d(resourceId=resource_id).click()
            time.sleep(get("uat.default_wait_ms", 1500) / 1000)
            return True
        except u2.exceptions.UiObjectNotFoundError:
            return False

    def swipe(self, direction: str, duration: float = 0.3) -> None:
        """Swipe in a direction: up, down, left, right.
        Uses adb shell input swipe to avoid INJECT_EVENTS permission issues on MIUI."""
        valid = {"up", "down", "left", "right"}
        if direction not in valid:
            raise AndroidDeviceError(f"Invalid direction: {direction}. Must be one of {valid}")
        w, h = self.get_screen_size()
        cx = w // 2
        ms = int(duration * 1000) + 200
        # Map direction to swipe coordinates (scroll content = opposite finger direction)
        coords = {
            "up":    (cx, int(h * 0.75), cx, int(h * 0.25)),   # finger up → scroll down
            "down":  (cx, int(h * 0.25), cx, int(h * 0.75)),   # finger down → scroll up
            "left":  (int(w * 0.75), h // 2, int(w * 0.25), h // 2),
            "right": (int(w * 0.25), h // 2, int(w * 0.75), h // 2),
        }
        x1, y1, x2, y2 = coords[direction]
        self.d.shell(f"input swipe {x1} {y1} {x2} {y2} {ms}")
        time.sleep(0.6)

    def swipe_coords(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        """Swipe from (x1,y1) to (x2,y2) via adb shell input."""
        ms = int(duration * 1000) + 200
        self.d.shell(f"input swipe {x1} {y1} {x2} {y2} {ms}")
        time.sleep(0.5)

    def type_text(self, text: str) -> None:
        """Type text into the currently focused element."""
        self.d.send_keys(text)
        time.sleep(0.3)

    def clear_text(self) -> None:
        """Clear text in focused element."""
        self.d.clear_text()

    def press_back(self) -> None:
        """Press the back button."""
        self.d.press("back")
        time.sleep(0.8)

    def press_home(self) -> None:
        """Press the home button."""
        self.d.press("home")
        time.sleep(0.8)

    def wait_for_element(self, text: str = "", resource_id: str = "", timeout: int = 10) -> bool:
        """Wait for an element to appear. Returns True if found within timeout."""
        try:
            if text:
                self.d(textContains=text).wait(timeout=timeout)
                return True
            if resource_id:
                self.d(resourceId=resource_id).wait(timeout=timeout)
                return True
        except u2.exceptions.UiObjectNotFoundError:
            return False
        return False

    def get_screen_size(self) -> tuple[int, int]:
        """Returns (width, height) of screen."""
        info = self.d.info
        return info["displayWidth"], info["displayHeight"]

    def get_current_package(self) -> str:
        """Returns the package name of the currently focused app."""
        return self.d.app_current().get("package", "")

    def get_device_info(self) -> dict:
        """Returns device info dict."""
        info = self.d.info
        return {
            "serial": self.serial,
            "model": self.d.device_info.get("model", ""),
            "android_version": self.d.device_info.get("version", ""),
            "screen_width": info.get("displayWidth"),
            "screen_height": info.get("displayHeight"),
            "current_package": self.get_current_package(),
        }

    def scroll_to_text(self, text: str, max_swipes: int = 5) -> bool:
        """Scroll down until text is visible. Returns True if found."""
        for _ in range(max_swipes):
            if self.d(textContains=text).exists:
                return True
            self.swipe("up")
        return False
