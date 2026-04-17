"""
smoke_test.py — Validates the full Prism stack without a UAT run.

Tests in order:
  1. Config loads correctly
  2. Claude API is reachable
  3. ADB device is connected
  4. uiautomator2 can connect to device
  5. Can take a screenshot
  6. Can read UI tree
  7. MCP server imports cleanly

Run from Prism root:
  .venv/bin/python3 smoke_test.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

PASS = "✓"
FAIL = "✗"
results = []

def check(name, fn):
    try:
        result = fn()
        msg = result if isinstance(result, str) else "OK"
        print(f"  {PASS} {name}: {msg}")
        results.append(True)
    except Exception as e:
        print(f"  {FAIL} {name}: {e}")
        results.append(False)

print("\n=== Prism Smoke Test ===\n")

# 1. Config
print("[1/7] Config")
check("settings.yaml loads", lambda: __import__("utils.config", fromlist=["get"]).get("agent.model"))

# 2. Claude API
print("\n[2/7] Claude API")
def test_claude():
    from utils.claude_client import ask
    resp = ask("Reply with exactly: PONG", max_tokens=10)
    assert "PONG" in resp, f"Expected PONG, got: {resp}"
    return f"response: {resp.strip()}"
check("Claude API reachable", test_claude)

# 3. ADB
print("\n[3/7] ADB")
def test_adb():
    import subprocess
    r = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    lines = [l for l in r.stdout.strip().split("\n")[1:] if "\tdevice" in l]
    if not lines:
        raise RuntimeError("No ADB device connected. Connect a device or start an emulator first.")
    return f"{len(lines)} device(s): {', '.join(l.split(chr(9))[0] for l in lines)}"
check("ADB device connected", test_adb)

# 4. uiautomator2 connection
print("\n[4/7] uiautomator2")
def test_u2_connect():
    from tools.android_device import AndroidDevice
    d = AndroidDevice()
    info = d.get_device_info()
    return f"serial={info['serial']} model={info['model']}"
check("uiautomator2 connects to device", test_u2_connect)

# 5. Screenshot
print("\n[5/7] Screenshot")
def test_screenshot():
    from tools.android_device import AndroidDevice
    d = AndroidDevice()
    path = ".tmp/smoke_test_screenshot.png"
    os.makedirs(".tmp", exist_ok=True)
    d.screenshot(save_path=path)
    size = os.path.getsize(path)
    assert size > 1000, f"Screenshot too small: {size} bytes"
    return f"saved to {path} ({size} bytes)"
check("Screenshot captured", test_screenshot)

# 6. UI tree
print("\n[6/7] UI Tree")
def test_ui_tree():
    from tools.android_device import AndroidDevice
    d = AndroidDevice()
    xml = d.get_ui_tree()
    assert len(xml) > 100, "UI tree too short"
    return f"{len(xml)} chars of XML"
check("UI hierarchy readable", test_ui_tree)

# 7. MCP server imports
print("\n[7/7] MCP Server")
def test_mcp_import():
    import importlib.util
    spec = importlib.util.spec_from_file_location("mcp_server", "mcp_server/server.py")
    # Just check it parses — don't run it
    import ast
    with open("mcp_server/server.py") as f:
        ast.parse(f.read())
    return "server.py parses OK"
check("MCP server parses cleanly", test_mcp_import)

# Summary
passed = sum(results)
total = len(results)
print(f"\n=== {passed}/{total} checks passed ===")
if passed == total:
    print("All systems ready. You can now run the MCP server and test flows.\n")
else:
    print("\nFix the failing checks above before running UAT.\n")
    if not results[2]:  # ADB check failed
        print("To connect a device:")
        print("  • Physical Android phone: enable Developer Options → USB Debugging → plug in via USB")
        print("  • Emulator: run bash setup_emulator.sh (needs Java first)")
        print("    Then: ~/Library/Android/sdk/emulator/emulator -avd mmt_test &")
sys.exit(0 if passed == total else 1)
