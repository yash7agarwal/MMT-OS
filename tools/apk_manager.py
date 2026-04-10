"""
tools/apk_manager.py — APK installation and management

Handles installing, launching, and extracting metadata from APKs.
Requires ADB to be in PATH and a connected device.
"""
from __future__ import annotations
import os
import re
import struct
import subprocess
import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


class ApkError(Exception):
    pass


def _run_adb(args: list[str], serial: str | None = None, timeout: int = 120) -> str:
    """Run an adb command and return stdout. Raises ApkError on failure."""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise ApkError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise ApkError(f"adb command timed out: {' '.join(args)}")
    except FileNotFoundError:
        raise ApkError("adb not found in PATH. Install Android SDK Platform Tools.")


def _parse_package_from_manifest(apk_path: str) -> str | None:
    """
    Extract package name from binary AndroidManifest.xml inside the APK.
    Parses the string pool and finds the value of the 'package' attribute
    on the root <manifest> element.
    """
    try:
        with zipfile.ZipFile(apk_path, "r") as z:
            raw = z.read("AndroidManifest.xml")

        # Parse string pool (starts at offset 8, after the file chunk header)
        sp_start = 8
        sp_header_size, sp_chunk_size, string_count = struct.unpack_from('<HII', raw, sp_start + 2)
        flags = struct.unpack_from('<I', raw, sp_start + 16)[0]
        is_utf8 = bool(flags & (1 << 8))
        offsets_start = sp_start + 28
        string_data_start = offsets_start + string_count * 4

        def read_string(idx: int) -> str:
            off = struct.unpack_from('<I', raw, offsets_start + idx * 4)[0]
            pos = string_data_start + off
            if is_utf8:
                char_len = raw[pos]
                if char_len & 0x80:
                    char_len = ((char_len & 0x7f) << 8) | raw[pos + 1]
                    pos += 1
                pos += 1
                byte_len = raw[pos]
                if byte_len & 0x80:
                    byte_len = ((byte_len & 0x7f) << 8) | raw[pos + 1]
                    pos += 1
                pos += 1
                return raw[pos:pos + byte_len].decode("utf-8", errors="replace")
            else:
                slen = struct.unpack_from('<H', raw, pos)[0]
                return raw[pos + 2:pos + 2 + slen * 2].decode("utf-16-le", errors="replace")

        # Build reverse lookup: string value → index
        pkg_attr_idx = None
        for i in range(string_count):
            if read_string(i) == "package":
                pkg_attr_idx = i
                break

        if pkg_attr_idx is None:
            return None

        # Walk XML events after the string pool chunk to find first START_ELEMENT (<manifest>)
        # Binary XML START_ELEMENT layout:
        #   +0  chunk_type (2)
        #   +2  chunk_header_size (2)
        #   +4  chunk_size (4)
        #   +8  line_number (4)
        #   +12 comment (4)
        #   +16 ns (4)
        #   +20 name (4)
        #   +24 attr_start (2)  — offset to attrs from start of ext (usually 20)
        #   +26 attr_size (2)   — bytes per attr (usually 20)
        #   +28 attr_count (2)
        #   +30 id_attr, class_attr, style_attr (2 each)
        #   +36 attributes[]    — each: ns(4) name(4) raw(4) val_size(2) res0(1) dtype(1) data(4)
        pos = sp_start + sp_chunk_size
        CHUNK_START_ELEMENT = 0x0102
        while pos < len(raw) - 36:
            chunk_type, chunk_header_size, chunk_size = struct.unpack_from('<HHI', raw, pos)
            if chunk_type == CHUNK_START_ELEMENT:
                attr_count = struct.unpack_from('<H', raw, pos + 28)[0]
                attr_size  = struct.unpack_from('<H', raw, pos + 26)[0]
                attr_base  = pos + 36  # attributes start after the 36-byte header+ext
                for a in range(attr_count):
                    a_off = attr_base + a * attr_size
                    name_idx = struct.unpack_from('<I', raw, a_off + 4)[0]
                    dtype    = raw[a_off + 15]   # dataType byte in Res_value
                    data     = struct.unpack_from('<I', raw, a_off + 16)[0]
                    if name_idx == pkg_attr_idx and dtype == 0x03:  # TYPE_STRING
                        return read_string(data)
                break  # only need the first element
            if chunk_size <= 0:
                break
            pos += chunk_size
    except Exception:
        pass
    return None


def get_package_name(apk_path: str) -> str:
    """Extract package name from APK. Tries aapt2/aapt first, then pure-Python fallback."""
    path = Path(apk_path)
    if not path.exists():
        raise ApkError(f"APK not found: {apk_path}")
    # Try aapt2 / aapt if available
    for tool in ["aapt2", "aapt"]:
        try:
            result = subprocess.run(
                [tool, "dump", "badging", str(path)],
                capture_output=True, text=True, timeout=30
            )
            match = re.search(r"package: name='([^']+)'", result.stdout)
            if match:
                return match.group(1)
        except FileNotFoundError:
            continue
    # Pure-Python fallback: parse binary AndroidManifest.xml from the APK zip
    pkg = _parse_package_from_manifest(str(path))
    if pkg:
        logger.info(f"Package name extracted via manifest parser: {pkg}")
        return pkg
    raise ApkError("Could not extract package name. Install aapt or aapt2 (Android SDK Build Tools).")


def get_apk_version(apk_path: str) -> dict:
    """Extract version name and code from APK."""
    path = Path(apk_path)
    if not path.exists():
        raise ApkError(f"APK not found: {apk_path}")
    for tool in ["aapt2", "aapt"]:
        try:
            result = subprocess.run(
                [tool, "dump", "badging", str(path)],
                capture_output=True, text=True, timeout=30
            )
            version_name = re.search(r"versionName='([^']+)'", result.stdout)
            version_code = re.search(r"versionCode='([^']+)'", result.stdout)
            return {
                "version_name": version_name.group(1) if version_name else "unknown",
                "version_code": version_code.group(1) if version_code else "unknown",
            }
        except FileNotFoundError:
            continue
    return {"version_name": "unknown", "version_code": "unknown"}


def install_apk(apk_path: str, serial: str | None = None, reinstall: bool = True) -> str:
    """
    Install APK on device. Returns the package name.
    reinstall=True replaces existing app (keeps data).
    """
    path = Path(apk_path)
    if not path.exists():
        raise ApkError(f"APK not found: {apk_path}")
    package_name = get_package_name(apk_path)
    args = ["install"]
    if reinstall:
        args.append("-r")
    args.append(str(path.resolve()))
    logger.info(f"Installing {path.name} ({package_name})...")
    _run_adb(args, serial=serial, timeout=180)
    logger.info(f"Installed: {package_name}")
    return package_name


def uninstall_apk(package_name: str, serial: str | None = None) -> None:
    """Uninstall an app by package name."""
    _run_adb(["uninstall", package_name], serial=serial)


def launch_app(package_name: str, activity: str | None = None, serial: str | None = None) -> None:
    """Launch an app. If activity not provided, uses the main launcher activity."""
    if activity:
        _run_adb(["shell", "am", "start", "-n", f"{package_name}/{activity}"], serial=serial)
    else:
        _run_adb(["shell", "monkey", "-p", package_name, "-c",
                  "android.intent.category.LAUNCHER", "1"], serial=serial)


def force_stop_app(package_name: str, serial: str | None = None) -> None:
    """Force stop an app."""
    _run_adb(["shell", "am", "force-stop", package_name], serial=serial)


def clear_app_data(package_name: str, serial: str | None = None) -> None:
    """Clear app data (useful for fresh-state testing)."""
    _run_adb(["shell", "pm", "clear", package_name], serial=serial)


def get_installed_version(package_name: str, serial: str | None = None) -> str:
    """Get installed version of a package."""
    output = _run_adb(["shell", "dumpsys", "package", package_name], serial=serial)
    match = re.search(r"versionName=([^\s]+)", output)
    return match.group(1) if match else "unknown"


def list_connected_devices() -> list[str]:
    """List all connected ADB device serials."""
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    lines = result.stdout.strip().split("\n")[1:]  # Skip header
    return [line.split("\t")[0] for line in lines if "\tdevice" in line]
