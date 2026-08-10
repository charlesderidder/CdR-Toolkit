"""Admin-rechten controleren, UAC-herstart en reboot-detectie."""
import ctypes
import os
import sys
import winreg


def is_admin() -> bool:
    """True als het proces met administrator-rechten draait."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Herstart de app verhoogd via een UAC-prompt. True als het starten lukte."""
    try:
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, ""
        else:
            exe = sys.executable
            params = f'"{os.path.abspath(sys.argv[0])}"'
        # "runas" triggert de UAC-prompt; retourwaarde > 32 = gelukt
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32
    except Exception:
        return False


def reboot_pending() -> bool:
    """Detecteer via het register of Windows op een herstart wacht."""
    keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    ]
    for path in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path):
                return True
        except OSError:
            pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager") as k:
            winreg.QueryValueEx(k, "PendingFileRenameOperations")
            return True
    except OSError:
        pass
    return False
