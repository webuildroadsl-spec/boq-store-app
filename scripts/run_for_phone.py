"""
Starts the app so phones on the same Wi-Fi can open it.

Run it by double-clicking windows\\2_run_for_phone.bat. Keep the window
open while you use the app; closing it (or pressing Ctrl+C) stops it.
"""

import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000


def lan_address():
    # Asks the system which network card it would use to reach the
    # internet. A UDP "connect" sends nothing; it only picks the route.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def venv_python():
    windows = ROOT / "venv" / "Scripts" / "python.exe"
    return windows if windows.exists() else ROOT / "venv" / "bin" / "python"


if __name__ == "__main__":
    if not (ROOT / ".env").exists() or not venv_python().exists():
        print("Setup hasn't been run yet. Double-click windows\\1_setup.bat first.")
        sys.exit(1)

    address = lan_address()
    print("=" * 60)
    if address:
        print("  On your PHONE (same Wi-Fi as this computer), open Chrome and go to:")
        print()
        print(f"      http://{address}:{PORT}")
    else:
        print("  Couldn't detect this computer's Wi-Fi address.")
        print("  Run 'ipconfig' and use the IPv4 Address, e.g. http://192.168.1.23:8000")
    print()
    print(f"  On THIS computer:  http://127.0.0.1:{PORT}")
    print(f"  Admin pages:       http://127.0.0.1:{PORT}/admin/")
    print()
    print("  If Windows Firewall asks, tick 'Private networks' and click Allow.")
    print("  Keep this window open. Close it to stop the app.")
    print("=" * 60)
    subprocess.run([str(venv_python()), "manage.py", "runserver", f"0.0.0.0:{PORT}"], cwd=ROOT)
