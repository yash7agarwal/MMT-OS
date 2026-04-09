"""
tools/emulator_manager.py — AVD lifecycle and APK installation manager

Handles starting/detecting Android emulators, waiting for boot, and ensuring
the target APK is installed. Designed for both local development and cloud/CI
environments (headless mode).
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from tools.apk_manager import install_apk, launch_app, get_apk_version, get_package_name

logger = logging.getLogger(__name__)

_DEFAULT_SDK_ROOT = os.path.expanduser("~/Library/Android/sdk")
_BOOT_TIMEOUT = 120  # seconds


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command and return the CompletedProcess. Does NOT raise on non-zero exit."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


class EmulatorManager:
    """
    Manages Android emulator AVD lifecycle and APK installation.

    Typical usage:
        manager = EmulatorManager(avd_name="mmt_test", headless=True)
        serial = manager.ensure_running()
        manager.ensure_app_installed(apk_path, package_name, serial)
    """

    def __init__(
        self,
        avd_name: str = "mmt_test",
        sdk_root: str | None = None,
        headless: bool = True,
    ):
        self.avd_name = avd_name
        self.sdk_root = os.path.expanduser(sdk_root or _DEFAULT_SDK_ROOT)
        self.headless = headless
        self.emulator_bin = os.path.join(self.sdk_root, "emulator", "emulator")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def ensure_running(self) -> str:
        """
        Ensure emulator is running. Returns ADB serial (e.g. emulator-5554).

        - Checks if an emulator is already running via `adb devices`.
        - If not, starts it using the configured AVD name.
        - Waits up to 120s for boot to complete.
        """
        existing = self.detect_running_emulator()
        if existing:
            logger.info(f"[EmulatorManager] Emulator already running: {existing}")
            return existing

        logger.info(f"[EmulatorManager] No running emulator found. Starting AVD: {self.avd_name}")
        serial = self._start_emulator()
        logger.info(f"[EmulatorManager] Emulator started: {serial}. Waiting for boot...")
        self._wait_for_boot(serial)
        logger.info(f"[EmulatorManager] Boot complete: {serial}")
        return serial

    def ensure_app_installed(self, apk_path: str, package_name: str, serial: str) -> bool:
        """
        Ensure the package is installed on the device.

        Returns True if installed (either already present or freshly installed).
        """
        if self._is_package_installed(package_name, serial):
            logger.info(
                f"[EmulatorManager] Package already installed: {package_name} on {serial}"
            )
            return True

        logger.info(
            f"[EmulatorManager] Package not found. Installing {apk_path} on {serial}..."
        )
        install_apk(apk_path, serial=serial)
        logger.info(f"[EmulatorManager] Installed: {package_name} on {serial}")
        return True

    def get_installed_version(self, package_name: str, serial: str) -> str:
        """Returns version string of installed package, or '' if not installed."""
        result = _run(
            ["adb", "-s", serial, "shell", "dumpsys", "package", package_name]
        )
        if result.returncode != 0:
            return ""
        for line in result.stdout.splitlines():
            if "versionName=" in line:
                return line.strip().split("versionName=")[-1].split()[0]
        return ""

    def cold_start_for_cloud(
        self,
        apk_path: str,
        package_name: str | None = None,
    ) -> dict:
        """
        Full cold-start sequence for cloud/CI environments.

        Steps:
          1. ensure_running() — start or detect the emulator
          2. Wait for device to be fully ready
          3. ensure_app_installed() — install APK if missing
          4. launch_app() — bring the app to the foreground
          5. Return metadata dict

        If package_name is None, it is extracted from the APK using aapt/aapt2.
        """
        # Step 1: ensure emulator is up
        serial = self.ensure_running()

        # Step 2: extra readiness wait (services settle after boot)
        logger.info("[EmulatorManager] Waiting for device services to stabilise...")
        self._wait_for_package_manager(serial)

        # Step 3: resolve package name if not provided
        if not package_name:
            logger.info("[EmulatorManager] Extracting package name from APK...")
            package_name = get_package_name(apk_path)
            logger.info(f"[EmulatorManager] Resolved package name: {package_name}")

        # Step 4: install if needed
        was_installed = self._is_package_installed(package_name, serial)
        self.ensure_app_installed(apk_path, package_name, serial)

        # Step 5: launch
        logger.info(f"[EmulatorManager] Launching app: {package_name}")
        launch_app(package_name, serial=serial)

        return {
            "serial": serial,
            "package_name": package_name,
            "installed_fresh": not was_installed,
        }

    @staticmethod
    def detect_running_emulator() -> str | None:
        """
        Returns the serial of the first running emulator, or None.

        Parses `adb devices` output for lines starting with 'emulator-'.
        """
        try:
            result = subprocess.run(
                ["adb", "devices"], capture_output=True, text=True, timeout=15
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("emulator-") and "\tdevice" in line:
                return line.split("\t")[0]
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _start_emulator(self) -> str:
        """Launch the AVD process. Returns the serial once it appears in adb devices."""
        cmd = [
            self.emulator_bin,
            "-avd", self.avd_name,
            "-no-snapshot-load",
            "-no-audio",
        ]
        if self.headless:
            cmd.append("-no-window")

        logger.info(f"[EmulatorManager] Launching: {' '.join(cmd)}")
        # Launch detached — we poll adb devices for the serial
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )

        # Poll until the emulator serial shows up in adb devices (up to 60s)
        deadline = time.time() + 60
        while time.time() < deadline:
            serial = self.detect_running_emulator()
            if serial:
                return serial
            time.sleep(2)

        raise RuntimeError(
            f"Emulator '{self.avd_name}' did not appear in `adb devices` within 60s. "
            "Check that the AVD exists and the emulator binary is accessible."
        )

    def _wait_for_boot(self, serial: str, timeout: int = _BOOT_TIMEOUT) -> None:
        """Poll sys.boot_completed until '1' or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = _run(
                ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
                timeout=10,
            )
            if result.stdout.strip() == "1":
                return
            time.sleep(3)
        raise TimeoutError(
            f"Emulator {serial} did not complete boot within {timeout}s."
        )

    def _wait_for_package_manager(self, serial: str, timeout: int = 30) -> None:
        """Wait until package manager is responsive (post-boot services are ready)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = _run(
                ["adb", "-s", serial, "shell", "pm", "list", "packages"],
                timeout=15,
            )
            if result.returncode == 0 and "package:" in result.stdout:
                return
            time.sleep(2)
        logger.warning(
            f"[EmulatorManager] Package manager on {serial} may not be fully ready."
        )

    def _is_package_installed(self, package_name: str, serial: str) -> bool:
        """Return True if the package is installed on the device."""
        result = _run(
            ["adb", "-s", serial, "shell", "pm", "list", "packages"],
            timeout=20,
        )
        return f"package:{package_name}" in result.stdout


# ------------------------------------------------------------------
# Standalone convenience function
# ------------------------------------------------------------------

def start_emulator_if_needed(avd_name: str = "mmt_test", headless: bool = True) -> str:
    """
    Ensure an emulator is running and return its ADB serial.

    Convenience wrapper around EmulatorManager for CLI / script usage:

        from tools.emulator_manager import start_emulator_if_needed
        serial = start_emulator_if_needed()
    """
    manager = EmulatorManager(avd_name=avd_name, headless=headless)
    return manager.ensure_running()
