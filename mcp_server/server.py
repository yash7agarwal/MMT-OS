"""
mcp_server/server.py — FastMCP server for Android device control

Exposes Android device control as MCP tools for Claude Code or subagents.
Run with: python mcp_server/server.py

Tools exposed:
  - screenshot: Take a screenshot, save to .tmp/, return file path
  - get_ui_tree: Return XML accessibility hierarchy
  - tap: Tap at coordinates
  - tap_text: Tap element containing text
  - swipe: Swipe in a direction
  - type_text: Type text into focused element
  - press_back: Press back button
  - install_apk: Install APK and return package name
  - launch_app: Launch installed app
  - force_stop_app: Force stop app
  - get_device_info: Return device metadata
"""
from __future__ import annotations
import sys
import os
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from mcp.server.fastmcp import FastMCP
from tools.android_device import AndroidDevice, AndroidDeviceError
from tools.apk_manager import install_apk, launch_app, force_stop_app, list_connected_devices
from utils.config import get

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("Prism Android UAT")

# Lazy device connection — connect on first tool use
_device: AndroidDevice | None = None


def _get_device() -> AndroidDevice:
    global _device
    if _device is None:
        _device = AndroidDevice()
    return _device


@mcp.tool()
def screenshot(label: str = "screenshot") -> str:
    """
    Take a screenshot of the current screen.
    Returns the file path of the saved PNG.
    label: descriptive name for the screenshot (used in filename)
    """
    from datetime import datetime
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    ts = datetime.now().strftime("%H%M%S_%f")
    save_dir = Path(get("device.screenshot_dir", ".tmp/screenshots"))
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = str(save_dir / f"{safe_label}_{ts}.png")
    _get_device().screenshot(save_path=save_path)
    return save_path


@mcp.tool()
def get_ui_tree() -> str:
    """
    Get the full UI accessibility tree as XML.
    Use this to understand what elements are on screen before tapping.
    Returns XML string with all UI elements, their text, resource IDs, and bounds.
    """
    return _get_device().get_ui_tree()


@mcp.tool()
def tap(x: int, y: int) -> str:
    """
    Tap at screen coordinates (x, y).
    Get coordinates from get_ui_tree() — look for 'bounds' attributes like [x1,y1][x2,y2],
    then tap the center: x=(x1+x2)/2, y=(y1+y2)/2.
    """
    _get_device().tap(x, y)
    return f"Tapped ({x}, {y})"


@mcp.tool()
def tap_text(text: str, exact: bool = False) -> str:
    """
    Tap the first UI element containing the given text.
    exact=False: partial match (default). exact=True: exact match.
    Returns 'found' or 'not_found'.
    """
    found = _get_device().tap_text(text, exact=exact)
    return "found" if found else "not_found"


@mcp.tool()
def swipe(direction: str) -> str:
    """
    Swipe in a direction. direction must be one of: up, down, left, right.
    'up' scrolls the page down (finger moves up). 'down' scrolls up.
    """
    _get_device().swipe(direction)
    return f"Swiped {direction}"


@mcp.tool()
def type_text(text: str) -> str:
    """
    Type text into the currently focused input field.
    Tap on an input field first, then call this.
    """
    _get_device().type_text(text)
    return f"Typed: {text}"


@mcp.tool()
def press_back() -> str:
    """Press the Android back button."""
    _get_device().press_back()
    return "Pressed back"


@mcp.tool()
def press_home() -> str:
    """Press the Android home button."""
    _get_device().press_home()
    return "Pressed home"


@mcp.tool()
def wait_for_element(text: str = "", resource_id: str = "", timeout: int = 10) -> str:
    """
    Wait for an element to appear on screen.
    Provide either text or resource_id to wait for.
    timeout: seconds to wait (default 10).
    Returns 'found' or 'timeout'.
    """
    found = _get_device().wait_for_element(text=text, resource_id=resource_id, timeout=timeout)
    return "found" if found else "timeout"


@mcp.tool()
def install_apk_tool(apk_path: str) -> str:
    """
    Install an APK on the connected device.
    apk_path: absolute path to the APK file.
    Returns the package name of the installed app.
    """
    package_name = install_apk(apk_path, serial=get("device.serial") or None)
    return package_name


@mcp.tool()
def launch_app_tool(package_name: str) -> str:
    """
    Launch an installed app by package name.
    package_name: e.g. 'com.makemytrip'
    """
    launch_app(package_name, serial=get("device.serial") or None)
    return f"Launched {package_name}"


@mcp.tool()
def force_stop_app_tool(package_name: str) -> str:
    """Force stop an app by package name."""
    force_stop_app(package_name, serial=get("device.serial") or None)
    return f"Force stopped {package_name}"


@mcp.tool()
def get_device_info() -> dict:
    """
    Get current device information.
    Returns: serial, model, android_version, screen dimensions, current app package.
    """
    return _get_device().get_device_info()


@mcp.tool()
def list_devices() -> list[str]:
    """List all connected ADB device serials."""
    return list_connected_devices()


if __name__ == "__main__":
    mcp.run()
