# -*- coding: utf-8 -*-
"""
License Client - 卡密验证客户端
Handles local license verification against Cloudflare Worker API.
"""

import os
import sys
import json
import hashlib
import platform
import subprocess
import uuid
import urllib.request
import urllib.error

# ---- Configuration ----
# Change this to your deployed Worker URL after deployment
_API_BASE = "https://YOUR_WORKER_NAME.YOUR_ACCOUNT.workers.dev"
_LICENSE_FILE = "license.key"


def get_hwid():
    """Generate a hardware fingerprint unique to this machine."""
    parts = []

    try:
        # CPU Serial
        result = subprocess.run(
            ['wmic', 'cpu', 'get', 'ProcessorId'],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped and stripped != 'ProcessorId':
                    parts.append(stripped)
    except Exception:
        pass

    try:
        # BIOS Serial
        result = subprocess.run(
            ['wmic', 'bios', 'get', 'SerialNumber'],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped and stripped != 'SerialNumber':
                    parts.append(stripped)
    except Exception:
        pass

    try:
        # Baseboard Serial
        result = subprocess.run(
            ['wmic', 'baseboard', 'get', 'SerialNumber'],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped and stripped != 'SerialNumber':
                    parts.append(stripped)
    except Exception:
        pass

    try:
        # MAC Address
        mac = uuid.getnode()
        parts.append(hex(mac))
    except Exception:
        pass

    # Fallback: use hostname + machine info
    if not parts:
        parts.append(platform.node())
        parts.append(platform.machine())
        parts.append(platform.processor())

    combined = "||".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def _api_call(endpoint, data):
    """Make a POST request to the license API."""
    url = f"{_API_BASE}{endpoint}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": f"HTTP {e.code}", "valid": False}
    except urllib.error.URLError as e:
        return {"error": f"网络连接失败: {e.reason}", "valid": False, "_offline": True}


def verify_key(key):
    """Verify if a license key is valid (without binding)."""
    return _api_call("/api/verify", {"key": key})


def activate_key(key, hwid):
    """Activate (bind) a license key to this machine."""
    return _api_call("/api/activate", {"key": key, "hwid": hwid})


def load_saved_key():
    """Read the saved license key from local file."""
    license_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _LICENSE_FILE)
    if os.path.exists(license_path):
        try:
            with open(license_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return None


def save_key(key):
    """Save the license key to local file."""
    license_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _LICENSE_FILE)
    try:
        with open(license_path, "w", encoding="utf-8") as f:
            f.write(key)
        return True
    except Exception:
        return False


def run_license_check(api_base=None):
    """
    Main entry point for license verification.
    Returns True if license is valid, False otherwise.
    Blocks until a valid key is provided or user quits.

    Args:
        api_base: Override the API base URL (for testing or custom deployments)
    """
    global _API_BASE
    if api_base:
        _API_BASE = api_base

    if "workers.dev" in _API_BASE:
        print()
        print("=" * 60)
        print("   ⚠ 未配置 API 服务器地址！")
        print("   请先完成 Cloudflare Worker 部署，然后修改")
        print("   license_client.py 中的 _API_BASE 变量。")
        print()
        print("   开发模式：跳过卡密验证 (5秒后继续...)")
        print("=" * 60)
        print()
        sys.stdout.flush()
        import time
        time.sleep(5)
        return True

    hwid = get_hwid()

    # Try saved key first
    saved_key = load_saved_key()
    if saved_key:
        result = verify_key(saved_key)
        if result.get("valid"):
            return True
        if result.get("status") == "revoked":
            print()
            print("=" * 60)
            print("   ✗ 该卡密已被禁用，请联系管理员！")
            print("=" * 60)
            print()
            sys.stdout.flush()
            os.remove(os.path.join(os.path.dirname(os.path.abspath(__file__)), _LICENSE_FILE))
            return False
        if result.get("status") == "expired":
            print()
            print("=" * 60)
            print("   ✗ 该卡密已过期，请联系管理员续费！")
            print("=" * 60)
            print()
            sys.stdout.flush()
            return False

    # No valid saved key - prompt user
    print()
    print("=" * 60)
    print("   请输入卡密以激活软件")
    print("   如果没有卡密，请联系管理员购买")
    print("=" * 60)
    print()
    sys.stdout.flush()

    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            key = input(f"  卡密 ({attempt + 1}/{max_attempts}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return False

        if not key:
            print("   请输入有效的卡密。")
            continue

        # First verify the key
        verify_result = verify_key(key)
        if verify_result.get("error"):
            if verify_result.get("_offline"):
                print(f"   ✗ 网络连接失败，请检查网络后重试。")
                print(f"   {verify_result.get('error')}")
                continue
            print(f"   ✗ 验证失败: {verify_result.get('error', '未知错误')}")
            continue

        if not verify_result.get("valid"):
            print(f"   ✗ {verify_result.get('message', '无效卡密')}")
            continue

        # Key is valid, now activate (bind)
        if not verify_result.get("bound"):
            print("   正在激活卡密...")
            sys.stdout.flush()
            act_result = activate_key(key, hwid)
            if act_result.get("success"):
                save_key(key)
                print(f"   ✓ 激活成功！欢迎使用。")
                sys.stdout.flush()
                return True
            else:
                print(f"   ✗ 激活失败: {act_result.get('message', '未知错误')}")
                continue
        else:
            # Already bound elsewhere?
            act_result = activate_key(key, hwid)
            if act_result.get("success"):
                save_key(key)
                print(f"   ✓ 验证成功！欢迎回来。")
                sys.stdout.flush()
                return True
            else:
                print(f"   ✗ {act_result.get('message', '验证失败')}")
                continue

    print()
    print(f"   已达到最大尝试次数 ({max_attempts})。程序退出。")
    return False


if __name__ == "__main__":
    # Test: run license check standalone
    print(f"HWID: {get_hwid()}")
    print(f"Saved key: {load_saved_key()}")
    result = run_license_check()
    print(f"License valid: {result}")
    sys.exit(0 if result else 1)
