"""
Opener - Self-healing automated browser launcher for meteo.kg.
Supports Windows 10, Windows 11, and ANY Google Chrome version.
Outputs detailed logs to opener.log.
"""

import atexit
import ctypes
import datetime
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# Configuration
APP_VERSION = "2.2.0"
ENABLE_ZOOM_IN = True
ZOOM_IN_STEPS = 1
TARGET_PROXY_TRANSFORM = "translate3d(29596px, 15604px, 0px) scale(81.5719)"
TARGET_URL = "https://www.meteo.kg/"
ERROR_ALREADY_EXISTS = 183

ACTUAL_WEATHER_SELECTOR = "li[data-map-id='actual-weather']"
LEAFLET_CONTAINER_SELECTOR = "div.leaflet-container"
FULLSCREEN_BUTTON_SELECTOR = "a.leaflet-control-fullscreen-button"
PROXY_SELECTOR = "div.leaflet-proxy.leaflet-zoom-animated"

driver = None
instance_mutex = None


def app_dir() -> str:
    """Returns the base application directory."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def is_directory_writable(dir_path: str) -> bool:
    """Checks whether the specified directory is writable by creating a test file."""
    try:
        os.makedirs(dir_path, exist_ok=True)
        test_file = os.path.join(dir_path, f".write_test_{os.getpid()}_{int(time.time())}")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
        if os.path.exists(test_file):
            os.remove(test_file)
        return True
    except Exception:
        return False


def get_base_dir() -> str:
    """
    Returns app_dir() if writable, or safely falls back to %LOCALAPPDATA%/opener.
    Guarantees that logging, profile and drivers can always be written.
    """
    primary = app_dir()
    if is_directory_writable(primary):
        return primary

    fallback = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "opener",
    )
    os.makedirs(fallback, exist_ok=True)
    return fallback


BASE_DIR = get_base_dir()
LOG_FILE = os.path.join(BASE_DIR, "opener.log")
CHROME_PROFILE_DIR = os.path.join(BASE_DIR, "chrome_profile")
DRIVERS_DIR = os.path.join(BASE_DIR, "drivers")


def write_log(message: str, level: str = "INFO", exc_info: bool = False) -> None:
    """
    Writes timestamped detailed log entries to opener.log and stdout.
    Format: [YYYY-MM-DD HH:MM:SS.mmm] [LEVEL] message
    """
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    formatted = f"[{now_str}] [{level.upper()}] {message}"

    try:
        print(formatted, flush=True)
    except Exception:
        pass

    try:
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(formatted + "\n")
            if exc_info:
                traceback.print_exc(file=f)
    except Exception:
        pass


def log_system_diagnostics() -> None:
    """Logs initial environment diagnostics."""
    write_log("=" * 65)
    write_log(f"Starting Opener v{APP_VERSION} (Self-Healing Edition)")
    write_log(f"Execution path: {os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)}")
    write_log(f"Frozen (PyInstaller): {getattr(sys, 'frozen', False)}")
    write_log(f"Base directory: {BASE_DIR}")
    write_log(f"Log file: {LOG_FILE}")
    write_log(f"Chrome profile: {CHROME_PROFILE_DIR}")
    write_log(f"OS: {platform.system()} {platform.release()} ({platform.version()}) - Arch: {platform.machine()}")
    write_log(f"Python: {sys.version.split()[0]} ({platform.architecture()[0]})")
    write_log("=" * 65)


def release_single_instance() -> None:
    """Releases the Windows single-instance mutex if held."""
    global instance_mutex
    if instance_mutex and sys.platform == "win32":
        try:
            ctypes.windll.kernel32.CloseHandle(instance_mutex)
        except Exception:
            pass
        instance_mutex = None


def ensure_single_instance() -> None:
    """
    Ensures only a single instance of Opener runs concurrently.
    Cross-platform safe (Windows kernel mutex via Win32 API).
    """
    global instance_mutex

    if instance_mutex:
        return

    if sys.platform != "win32":
        write_log("Non-Windows platform; skipping Win32 mutex single-instance check.", "DEBUG")
        return

    mutex_key = os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)
    mutex_source = mutex_key.encode("utf-8")
    mutex_hash = hashlib.sha1(mutex_source).hexdigest()
    mutex_name = f"Local\\opener_{mutex_hash}"

    handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        write_log("Warning: Could not create single-instance mutex handle.", "WARN")
        return

    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        write_log("Another instance of Opener is already running. Exiting cleanly.", "INFO")
        raise SystemExit(0)

    instance_mutex = handle
    atexit.register(release_single_instance)
    write_log("Single-instance check passed. Mutex acquired.", "INFO")


def find_chrome_executable() -> Optional[str]:
    """
    Locates Google Chrome executable on Windows across registry and filesystem.
    """
    if sys.platform == "win32":
        try:
            import winreg
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            ]
            for root_key, subkey in reg_paths:
                try:
                    with winreg.OpenKey(root_key, subkey) as key:
                        val, _ = winreg.QueryValueEx(key, "")
                        if val and os.path.isfile(val):
                            write_log(f"Found Chrome binary in registry: {val}", "DEBUG")
                            return os.path.abspath(val)
                except Exception:
                    pass
        except Exception:
            pass

    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramData%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]

    for path in candidates:
        if os.path.isfile(path):
            write_log(f"Found Chrome binary at standard path: {path}", "DEBUG")
            return os.path.abspath(path)

    for name in ["chrome", "chrome.exe", "google-chrome"]:
        which_path = shutil.which(name)
        if which_path and os.path.isfile(which_path):
            write_log(f"Found Chrome binary in PATH: {which_path}", "DEBUG")
            return os.path.abspath(which_path)

    return None


def get_chrome_version(chrome_exe: Optional[str] = None) -> Optional[str]:
    """
    Determines installed Google Chrome version with multiple detection tiers:
    1. Scan Chrome/Application directory for version subfolders (e.g., 156.0.8078.80)
    2. Read Windows Registry BLBeacon / Clients keys
    3. Run chrome.exe --product-version
    """
    # Tier 1: Directory scan in Chrome/Application
    if chrome_exe and os.path.isfile(chrome_exe):
        app_folder = os.path.dirname(chrome_exe)
        try:
            version_dirs = []
            for entry in os.listdir(app_folder):
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", entry):
                    full_dir = os.path.join(app_folder, entry)
                    if os.path.isdir(full_dir):
                        parts = tuple(int(x) for x in entry.split("."))
                        version_dirs.append((parts, entry))
            if version_dirs:
                version_dirs.sort()
                ver = version_dirs[-1][1]
                write_log(f"Detected Chrome version via Application folder: {ver}", "DEBUG")
                return ver
        except Exception as err:
            write_log(f"Directory scan for Chrome version failed: {err}", "DEBUG")

    # Tier 2: Windows Registry
    if sys.platform == "win32":
        try:
            import winreg
            reg_keys = [
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon", "version"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon", "version"),
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}", "pv"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}", "pv"),
            ]
            for root_key, subkey, val_name in reg_keys:
                try:
                    with winreg.OpenKey(root_key, subkey) as key:
                        val, _ = winreg.QueryValueEx(key, val_name)
                        if val and re.match(r"^\d+", str(val)):
                            ver = str(val).strip()
                            write_log(f"Detected Chrome version via Registry: {ver}", "DEBUG")
                            return ver
                except Exception:
                    pass
        except Exception:
            pass

    # Tier 3: Subprocess chrome.exe --product-version
    if chrome_exe and os.path.isfile(chrome_exe):
        try:
            res = subprocess.run(
                [chrome_exe, "--product-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = res.stdout.strip()
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", out):
                write_log(f"Detected Chrome version via --product-version: {out}", "DEBUG")
                return out
        except Exception:
            pass

    return None


def sanitize_chrome_profile(profile_dir: str) -> None:
    """
    Self-healing routine for Chrome profile:
    1. Checks if orphan Chrome processes are holding profile locks and safely frees them.
    2. Clears stale lock files (SingletonLock, DevToolsActivePort, etc.).
    3. Clears temporary caches with robust error handling.
    4. Suppresses 'Restore pages' prompt by setting exit_type='Normal' in Preferences.
    """
    os.makedirs(profile_dir, exist_ok=True)

    # 1. Check and terminate lingering Chrome processes bound to this profile
    if sys.platform == "win32":
        profile_folder_name = os.path.basename(profile_dir)
        try:
            # Terminate lingering chromedriver processes
            subprocess.run(
                ["taskkill", "/f", "/im", "chromedriver.exe"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

        # If lock files are present, check if orphan chrome is using our profile
        lock_file = os.path.join(profile_dir, "SingletonLock")
        if os.path.exists(lock_file):
            try:
                ps_cmd = (
                    f"Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{profile_folder_name}*' }} | "
                    f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                    capture_output=True,
                    timeout=6,
                )
                time.sleep(0.5)
            except Exception:
                pass

    # 2. Delete stale lock files
    lock_files = [
        "SingletonLock",
        "SingletonCookie",
        "SingletonSocket",
        "DevToolsActivePort",
        "lockfile",
    ]
    for lf in lock_files:
        path = os.path.join(profile_dir, lf)
        if os.path.exists(path):
            try:
                os.remove(path)
                write_log(f"Removed stale Chrome lock: {lf}", "DEBUG")
            except Exception as err:
                write_log(f"Notice: Could not remove lock file {lf}: {err}", "DEBUG")

    # 3. Clean temporary cache directories
    cache_dirs = [
        os.path.join(profile_dir, "Default", "Cache"),
        os.path.join(profile_dir, "Default", "Code Cache"),
        os.path.join(profile_dir, "Default", "GPUCache"),
        os.path.join(profile_dir, "ShaderCache"),
    ]
    for path in cache_dirs:
        if not os.path.exists(path):
            continue
        try:
            def _remove_readonly(func, file_path, _):
                try:
                    os.chmod(file_path, 0o777)
                    func(file_path)
                except Exception:
                    pass

            shutil.rmtree(path, onerror=_remove_readonly)
            write_log(f"Deleted Chrome cache: {path}", "DEBUG")
        except Exception as err:
            write_log(f"Failed to delete Chrome cache {path}: {err}", "DEBUG")

    # 4. Patch Default/Preferences to prevent 'Restore pages' prompt and allow fullscreen
    pref_file = os.path.join(profile_dir, "Default", "Preferences")
    if os.path.exists(pref_file):
        try:
            with open(pref_file, "r", encoding="utf-8", errors="ignore") as f:
                pref_data = json.load(f)
            if isinstance(pref_data, dict):
                profile_section = pref_data.setdefault("profile", {})
                profile_section["exit_type"] = "Normal"
                profile_section["exited_cleanly"] = True

                # Allow automatic fullscreen for meteo.kg without requiring physical user gestures
                default_content = profile_section.setdefault("default_content_setting_values", {})
                default_content["automatic_fullscreen"] = 1
                content_settings = profile_section.setdefault("content_settings", {})
                exceptions = content_settings.setdefault("exceptions", {})
                auto_fs = exceptions.setdefault("automatic_fullscreen", {})
                auto_fs["https://www.meteo.kg,*"] = {"setting": 1}
                auto_fs["http://www.meteo.kg,*"] = {"setting": 1}
                auto_fs["https://meteo.kg,*"] = {"setting": 1}

                with open(pref_file, "w", encoding="utf-8") as f:
                    json.dump(pref_data, f)
                write_log("Patched profile Preferences to suppress crash restore bubbles and enable automatic fullscreen.", "DEBUG")
        except Exception as err:
            write_log(f"Could not patch Preferences: {err}", "DEBUG")


def purge_selenium_manager_cache() -> None:
    """Purges outdated or corrupted drivers from Selenium Manager cache."""
    user_home = os.path.expanduser("~")
    sm_cache = os.path.join(user_home, ".cache", "selenium", "chromedriver")
    if os.path.exists(sm_cache):
        try:
            shutil.rmtree(sm_cache, ignore_errors=True)
            write_log("Purged outdated Selenium Manager ChromeDriver cache.", "INFO")
        except Exception as err:
            write_log(f"Could not purge Selenium Manager cache: {err}", "WARN")


def download_matching_chromedriver(version_str: str, target_drivers_dir: str) -> Optional[str]:
    """
    Downloads matching ChromeDriver from official Google repositories for ANY Chrome version.
    Supports:
    - Chrome >= 115: Google Chrome for Testing (CFT) endpoints
    - Chrome < 115: Legacy Google Storage endpoints
    Extracts chromedriver.exe and returns its absolute path.
    """
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?", version_str.strip())
    if not m:
        write_log(f"Invalid Chrome version format: {version_str}", "ERROR")
        return None

    major = int(m.group(1))
    driver_folder = os.path.join(target_drivers_dir, str(major))
    os.makedirs(driver_folder, exist_ok=True)
    driver_exe = os.path.join(driver_folder, "chromedriver.exe")

    # If already cached and valid size (> 1MB), return immediately
    if os.path.isfile(driver_exe) and os.path.getsize(driver_exe) > 1000000:
        write_log(f"Found existing cached ChromeDriver for Chrome v{major}: {driver_exe}", "INFO")
        return driver_exe

    is_64bit = (
        platform.machine().endswith("64")
        or "64" in os.environ.get("PROCESSOR_ARCHITECTURE", "")
        or sys.maxsize > 2**32
    )
    platform_key = "win64" if is_64bit else "win32"

    write_log(f"Downloading ChromeDriver for Chrome v{version_str} (major: {major}, {platform_key})...", "INFO")

    download_urls = []

    if major >= 115:
        # Tier A: Query LATEST_RELEASE_<major> on CFT
        try:
            meta_url = f"https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_{major}"
            req = urllib.request.Request(meta_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resolved_version = resp.read().decode("utf-8").strip()
                if resolved_version:
                    download_urls.append(
                        f"https://storage.googleapis.com/chrome-for-testing-public/{resolved_version}/{platform_key}/chromedriver-{platform_key}.zip"
                    )
                    write_log(f"Resolved CFT version: {resolved_version}", "DEBUG")
        except Exception as err:
            write_log(f"CFT LATEST_RELEASE_{major} check returned: {err}", "DEBUG")

        # Tier B: Direct URL with the full browser version
        download_urls.append(
            f"https://storage.googleapis.com/chrome-for-testing-public/{version_str}/{platform_key}/chromedriver-{platform_key}.zip"
        )
        if platform_key != "win32":
            download_urls.append(
                f"https://storage.googleapis.com/chrome-for-testing-public/{version_str}/win32/chromedriver-win32.zip"
            )

        # Tier C: Query CFT known-good-versions JSON
        try:
            json_url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
            req = urllib.request.Request(json_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                cft_data = json.loads(resp.read().decode("utf-8"))
                matching_versions = [
                    v for v in cft_data.get("versions", [])
                    if v.get("version", "").startswith(f"{major}.")
                ]
                if matching_versions:
                    latest_match = matching_versions[-1]
                    for d in latest_match.get("downloads", {}).get("chromedriver", []):
                        if d.get("platform") == platform_key:
                            download_urls.append(d.get("url"))
        except Exception as err:
            write_log(f"CFT known-good-versions query returned: {err}", "DEBUG")

    else:
        # Legacy Chrome (< 115)
        try:
            meta_url = f"https://chromedriver.storage.googleapis.com/LATEST_RELEASE_{major}"
            req = urllib.request.Request(meta_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resolved_version = resp.read().decode("utf-8").strip()
                download_urls.append(
                    f"https://chromedriver.storage.googleapis.com/{resolved_version}/chromedriver_win32.zip"
                )
        except Exception:
            pass
        download_urls.append(
            f"https://chromedriver.storage.googleapis.com/{version_str}/chromedriver_win32.zip"
        )

    # Try candidate download URLs
    last_error = None
    for url in download_urls:
        if not url:
            continue
        try:
            write_log(f"Attempting download from: {url}", "INFO")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                archive_data = io.BytesIO(resp.read())

            with zipfile.ZipFile(archive_data) as zf:
                for item_name in zf.namelist():
                    if item_name.lower().endswith("chromedriver.exe"):
                        temp_exe = driver_exe + f".tmp_{os.getpid()}"
                        with zf.open(item_name) as src, open(temp_exe, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        if os.path.exists(driver_exe):
                            try:
                                os.remove(driver_exe)
                            except Exception:
                                pass
                        os.replace(temp_exe, driver_exe)
                        write_log(f"Successfully downloaded and extracted ChromeDriver: {driver_exe}", "INFO")
                        return driver_exe
        except Exception as err:
            last_error = err
            write_log(f"Download attempt from {url} failed: {err}", "DEBUG")

    write_log(f"Failed to auto-download ChromeDriver for Chrome v{major}. Last error: {last_error}", "WARN")
    return None


def create_chrome_driver(profile_dir: str = CHROME_PROFILE_DIR, retries: int = 2) -> webdriver.Chrome:
    """
    Creates and initializes Chrome WebDriver with full multi-tier self-healing:
    - Tier 1: Cached matching ChromeDriver (fastest, offline-ready)
    - Tier 2: Selenium Manager default
    - Tier 3: Auto-download matching ChromeDriver from Google CFT on mismatch or failure
    - Tier 4: webdriver-manager package fallback
    - Tier 5: Bundled chromedriver.exe fallback
    - Tier 6: Clean profile fallback if profile lock / corruption occurs
    """
    sanitize_chrome_profile(profile_dir)

    chrome_exe = find_chrome_executable()
    chrome_ver = get_chrome_version(chrome_exe)

    if chrome_exe:
        write_log(f"Target Chrome binary: {chrome_exe}", "INFO")
    else:
        write_log("Notice: Chrome binary not found in standard paths; relying on Selenium detection.", "DEBUG")

    if chrome_ver:
        write_log(f"Detected Chrome version: {chrome_ver}", "INFO")

    options = Options()
    options.add_experimental_option("detach", True)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-session-crashed-bubble")
    options.add_argument("--hide-crash-restore-bubble")
    options.add_argument("--disable-search-engine-choice-screen")
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gesture-requirement-for-presentation")
    options.add_argument("--disable-features=Translate,OptimizationHints,MediaRouter")

    if chrome_exe:
        options.binary_location = chrome_exe

    # Tier 1: Check if matching driver is already downloaded in local cache
    if chrome_ver:
        major = int(chrome_ver.split(".")[0])
        cached_exe = os.path.join(DRIVERS_DIR, str(major), "chromedriver.exe")
        if os.path.isfile(cached_exe):
            try:
                write_log(f"Launching Chrome with cached matching driver: {cached_exe}", "INFO")
                service = Service(executable_path=cached_exe)
                return webdriver.Chrome(service=service, options=options)
            except Exception as err:
                write_log(f"Cached driver launch failed: {err}. Proceeding to fallback.", "WARN")

    # Tier 2: Try Selenium Manager (built-in Selenium 4)
    try:
        write_log("Attempting Chrome launch via Selenium Manager...", "INFO")
        return webdriver.Chrome(options=options)
    except Exception as err:
        err_msg = str(err)
        write_log(f"Selenium Manager launch failed: {err_msg}", "WARN")

        # Extract target Chrome version from error message if available
        target_version = chrome_ver
        v_match = re.search(r"Current browser version is ([\d\.]+)", err_msg)
        if v_match:
            target_version = v_match.group(1)
            write_log(f"Parsed current browser version from exception: {target_version}", "INFO")

        p_match = re.search(r"binary path ([^;\r\n]+)", err_msg)
        if p_match and not chrome_exe:
            chrome_exe = p_match.group(1).strip()
            options.binary_location = chrome_exe
            write_log(f"Parsed Chrome binary path from exception: {chrome_exe}", "INFO")

        # Tier 3: Self-healing auto-download from Google Chrome for Testing (CFT)
        if target_version:
            write_log(f"Self-healing: initiating driver download for Chrome {target_version}...", "INFO")
            purge_selenium_manager_cache()
            downloaded = download_matching_chromedriver(target_version, DRIVERS_DIR)
            if downloaded:
                try:
                    write_log(f"Launching Chrome with newly downloaded driver: {downloaded}", "INFO")
                    service = Service(executable_path=downloaded)
                    return webdriver.Chrome(service=service, options=options)
                except Exception as dl_err:
                    write_log(f"Launch with downloaded driver failed: {dl_err}", "ERROR")

        # Tier 4: webdriver-manager package fallback if available
        try:
            write_log("Attempting launch via webdriver-manager package...", "INFO")
            from webdriver_manager.chrome import ChromeDriverManager
            wm_path = ChromeDriverManager().install()
            write_log(f"webdriver-manager installed driver at: {wm_path}", "INFO")
            service = Service(executable_path=wm_path)
            return webdriver.Chrome(service=service, options=options)
        except Exception as wm_err:
            write_log(f"webdriver-manager fallback failed: {wm_err}", "DEBUG")

        # Tier 5: Bundled chromedriver.exe near executable
        bundled_candidates = [
            os.path.join(BASE_DIR, "chromedriver.exe"),
            os.path.join(app_dir(), "chromedriver.exe"),
            os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "chromedriver.exe"),
        ]
        for bp in bundled_candidates:
            if os.path.isfile(bp):
                try:
                    write_log(f"Attempting launch with bundled driver: {bp}", "INFO")
                    service = Service(executable_path=bp)
                    return webdriver.Chrome(service=service, options=options)
                except Exception as bp_err:
                    write_log(f"Bundled driver {bp} failed: {bp_err}", "DEBUG")

        # Tier 6: Profile corruption recovery fallback
        is_profile_error = any(kw in err_msg for kw in ["DevToolsActivePort", "user data directory", "crashed", "session not created"])
        if retries > 0 and is_profile_error:
            fallback_dir = os.path.join(BASE_DIR, f"chrome_profile_fallback_{int(time.time())}")
            write_log(f"Retrying Chrome launch with clean fallback profile: {fallback_dir}", "WARN")
            return create_chrome_driver(profile_dir=fallback_dir, retries=retries - 1)

        raise RuntimeError(
            f"Не удалось запустить Google Chrome WebDriver: {err_msg}\n"
            f"Подробный отчет записан в: {LOG_FILE}"
        ) from err


def find_visible_element(css_selector: str, timeout: int = 15, require_in_viewport: bool = False):
    """
    Finds the first visible DOM element matching css_selector.
    Uses robust JS boundingClientRect and computedStyle checks.
    """
    script = """
        const selector = arguments[0];
        const requireInViewport = arguments[1];

        return [...document.querySelectorAll(selector)].find((el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);

            const isVisible = (
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                style.opacity !== '0' &&
                rect.width > 0 &&
                rect.height > 0
            );

            if (!isVisible) return false;
            if (!requireInViewport) return true;

            return (
                rect.bottom > 0 &&
                rect.right > 0 &&
                rect.top < window.innerHeight &&
                rect.left < window.innerWidth
            );
        }) || null;
    """

    element = WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script(script, css_selector, require_in_viewport)
    )

    if not element:
        raise TimeoutException(f"Не найден видимый элемент: {css_selector}")

    return element


def robust_click(element_or_selector, timeout: int = 15) -> bool:
    """
    Multi-tier robust click fallback:
    1. Standard Selenium click()
    2. ActionChains move & click()
    3. Direct JavaScript click()
    4. Synthetic MouseEvent dispatch
    """
    if isinstance(element_or_selector, str):
        element = find_visible_element(element_or_selector, timeout=timeout)
    else:
        element = element_or_selector

    if not element:
        return False

    driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
    time.sleep(0.1)

    # Strategy 1: Standard Selenium click
    try:
        if element.is_displayed() and element.is_enabled():
            element.click()
            return True
    except Exception:
        pass

    # Strategy 2: ActionChains click
    try:
        ActionChains(driver).move_to_element(element).pause(0.1).click().perform()
        return True
    except Exception:
        pass

    # Strategy 3: Direct JS click
    try:
        clicked = driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return false;
            el.click();
            return true;
            """,
            element,
        )
        if clicked:
            return True
    except Exception:
        pass

    # Strategy 4: Synthetic MouseEvent
    try:
        dispatched = driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return false;
            el.dispatchEvent(new MouseEvent('click', {
                bubbles: true,
                cancelable: true,
                view: window
            }));
            return true;
            """,
            element,
        )
        return bool(dispatched)
    except Exception:
        return False


def activate_actual_weather(timeout: int = 15) -> None:
    """
    Activates the 'actual-weather' tab/layer.
    Checks if already active, clicks with fallback, and verifies state.
    """
    write_log("Checking 'actual-weather' layer status...", "INFO")
    element = find_visible_element(ACTUAL_WEATHER_SELECTOR, timeout=timeout, require_in_viewport=False)

    classes = (element.get_attribute("class") or "").split()
    if "active" in classes:
        write_log("Layer 'actual-weather' is already active.", "INFO")
        return

    write_log("Clicking 'actual-weather' layer...", "INFO")
    for attempt in range(1, 4):
        robust_click(ACTUAL_WEATHER_SELECTOR, timeout=timeout)

        try:
            WebDriverWait(driver, 4).until(
                lambda d: "active" in (
                    find_visible_element(ACTUAL_WEATHER_SELECTOR, timeout=timeout).get_attribute("class") or ""
                ).split()
            )
            write_log(f"Layer 'actual-weather' activated successfully on attempt {attempt}.", "INFO")
            return
        except TimeoutException:
            write_log(f"Activation check timed out on attempt {attempt}, retrying...", "DEBUG")
            time.sleep(0.5)

    raise TimeoutException("Не удалось переключиться на 'actual-weather'.")


def apply_exact_proxy_zoom_transform(timeout: int = 15) -> None:
    """
    Applies the target Leaflet CSS transform to the active map proxy element.
    Target: translate3d(29596px, 15604px, 0px) scale(81.5719)
    """
    write_log(f"Applying target map proxy transform: {TARGET_PROXY_TRANSFORM}...", "INFO")

    script = """
        const targetTransform = arguments[0];
        // Target the proxy inside the visible Leaflet container first
        let proxy = document.querySelector('.leaflet-container:not(.hidden) div.leaflet-proxy.leaflet-zoom-animated')
                    || document.querySelector('div.leaflet-proxy.leaflet-zoom-animated')
                    || document.querySelector('div.leaflet-proxy');

        if (!proxy) return false;
        proxy.style.transform = targetTransform;
        return proxy.style.transform === targetTransform;
    """

    applied = WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script(script, TARGET_PROXY_TRANSFORM)
    )

    if not applied:
        raise TimeoutException("Не удалось применить целевой transform для зума.")

    write_log("Map proxy transform successfully applied.", "INFO")


def is_map_fullscreen() -> bool:
    """Checks whether the Leaflet map is in fullscreen mode (HTML5, Leaflet flag, or CSS class)."""
    check_script = """
        const cont = document.querySelector('.leaflet-container:not(.hidden)') || document.querySelector('.leaflet-container');
        const map = (typeof otherMap !== 'undefined' && otherMap) || (typeof weatherMap !== 'undefined' && weatherMap);
        const html5Fs = Boolean(document.fullscreenElement || document.webkitFullscreenElement || document.mozFullScreenElement);
        const mapFs = Boolean(map && typeof map.isFullscreen === 'function' && map.isFullscreen());
        const classFs = Boolean(cont && (cont.classList.contains('leaflet-fullscreen-on') || cont.classList.contains('leaflet-pseudo-fullscreen')));
        return html5Fs || mapFs || classFs;
    """
    try:
        return bool(driver.execute_script(check_script))
    except Exception:
        return False


def click_visible_map_fullscreen(timeout: int = 15) -> None:
    """
    Finds the fullscreen button belonging to the ACTIVE Leaflet map container.
    Guarantees fullscreen activation using multiple fallback tiers:
    1. Chrome DevTools Protocol Runtime.evaluate with userGesture=True (native HTML5 element fullscreen)
    2. ActionChains native click gesture
    3. Standard Selenium element click
    4. Leaflet programmatic toggleFullscreen / _enablePseudoFullscreen API
    5. Direct DOM pseudo-fullscreen CSS styling + driver.fullscreen_window()
    """
    write_log("Locating visible map fullscreen button...", "INFO")

    find_btn_script = """
        const buttons = [...document.querySelectorAll("a.leaflet-control-fullscreen-button")];
        return buttons.find((btn) => {
            const container = btn.closest('.leaflet-container');
            if (container) {
                const cStyle = window.getComputedStyle(container);
                if (container.classList.contains('hidden') || cStyle.display === 'none' || cStyle.visibility === 'hidden') {
                    return false;
                }
            }
            const rect = btn.getBoundingClientRect();
            const style = window.getComputedStyle(btn);
            return (
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 0 &&
                rect.height > 0
            );
        }) || buttons[0] || null;
    """

    button = None
    try:
        button = WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script(find_btn_script)
        )
    except Exception as find_err:
        write_log(f"Fullscreen button lookup notice: {find_err}", "DEBUG")

    if button:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", button)
            time.sleep(0.2)
        except Exception:
            pass

    if is_map_fullscreen():
        write_log("Map is already in fullscreen mode.", "INFO")
        return

    # Tier 1: CDP Runtime.evaluate with simulated userGesture (bypasses browser user-activation block)
    try:
        write_log("Activating fullscreen via CDP simulated user gesture...", "DEBUG")
        driver.execute_cdp_cmd(
            "Runtime.evaluate",
            {
                "expression": """
                    (() => {
                        const btn = document.querySelector('.leaflet-container:not(.hidden) a.leaflet-control-fullscreen-button')
                                 || document.querySelector('a.leaflet-control-fullscreen-button');
                        if (btn) btn.click();
                        const container = document.querySelector('.leaflet-container:not(.hidden)') || document.querySelector('.leaflet-container');
                        if (container && container.requestFullscreen && !document.fullscreenElement) {
                            return container.requestFullscreen().then(() => true).catch(() => false);
                        }
                        return true;
                    })()
                """,
                "userGesture": True,
                "awaitPromise": True,
            },
        )
        time.sleep(0.5)
    except Exception as cdp_err:
        write_log(f"CDP userGesture fullscreen notice: {cdp_err}", "DEBUG")

    if is_map_fullscreen():
        write_log("Fullscreen activated successfully via CDP native gesture.", "INFO")
        return

    # Tier 2: ActionChains native click
    if button:
        try:
            ActionChains(driver).move_to_element(button).pause(0.1).click().perform()
            time.sleep(0.5)
        except Exception as err:
            write_log(f"ActionChains click notice: {err}", "DEBUG")

        if is_map_fullscreen():
            write_log("Fullscreen activated successfully via ActionChains.", "INFO")
            return

        # Tier 3: Selenium element click
        try:
            button.click()
            time.sleep(0.5)
        except Exception:
            pass

        if is_map_fullscreen():
            write_log("Fullscreen activated successfully via element click.", "INFO")
            return

    # Tier 4: Leaflet programmatic API & Pseudo-fullscreen
    write_log("Activating fullscreen via Leaflet programmatic pseudo-fullscreen...", "INFO")
    js_enable_fs = """
        const map = (typeof otherMap !== 'undefined' && otherMap) || (typeof weatherMap !== 'undefined' && weatherMap);
        const container = document.querySelector('.leaflet-container:not(.hidden)') 
                       || (map && typeof map.getContainer === 'function' ? map.getContainer() : null)
                       || document.querySelector('.leaflet-container');

        if (map) {
            try {
                if (typeof map._enablePseudoFullscreen === 'function') {
                    map._enablePseudoFullscreen(container || map.getContainer());
                } else if (typeof map.toggleFullscreen === 'function') {
                    map.toggleFullscreen({ pseudoFullscreen: true });
                }
            } catch(e) {}
        }

        if (container) {
            container.classList.add('leaflet-pseudo-fullscreen');
            container.classList.add('leaflet-fullscreen-on');
            container.style.position = 'fixed';
            container.style.top = '0px';
            container.style.left = '0px';
            container.style.width = '100vw';
            container.style.height = '100vh';
            container.style.zIndex = '99999';

            // Update fullscreen button UI icon to "Exit Fullscreen"
            const btn = container.querySelector('a.leaflet-control-fullscreen-button') || document.querySelector('a.leaflet-control-fullscreen-button');
            if (btn) {
                btn.title = 'Exit Fullscreen';
            }

            if (map && typeof map.invalidateSize === 'function') {
                map.invalidateSize();
            }
            window.dispatchEvent(new Event('resize'));
        }

        // Attach Escape key listener to cleanly exit pseudo-fullscreen if user presses Esc
        if (!window._fsEscapeHandlerAttached) {
            window._fsEscapeHandlerAttached = true;
            window.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' || e.keyCode === 27) {
                    const cont = document.querySelector('.leaflet-container');
                    const m = (typeof otherMap !== 'undefined' && otherMap) || (typeof weatherMap !== 'undefined' && weatherMap);
                    if (m && typeof m._disablePseudoFullscreen === 'function') {
                        m._disablePseudoFullscreen(cont);
                    } else if (cont) {
                        cont.classList.remove('leaflet-pseudo-fullscreen');
                        cont.classList.remove('leaflet-fullscreen-on');
                        cont.style.position = '';
                        cont.style.top = '';
                        cont.style.left = '';
                        cont.style.width = '';
                        cont.style.height = '';
                        cont.style.zIndex = '';
                        if (m && typeof m.invalidateSize === 'function') m.invalidateSize();
                        window.dispatchEvent(new Event('resize'));
                    }
                }
            });
        }
        return true;
    """
    driver.execute_script(js_enable_fs)
    time.sleep(0.5)

    # If native HTML5 fullscreen wasn't enabled, also maximize or fullscreen the browser window
    is_html5_fs = False
    try:
        is_html5_fs = bool(driver.execute_script("return Boolean(document.fullscreenElement || document.webkitFullscreenElement);"))
    except Exception:
        pass

    if not is_html5_fs:
        try:
            driver.fullscreen_window()
            write_log("Browser window set to fullscreen (F11).", "INFO")
        except Exception as win_err:
            try:
                driver.maximize_window()
            except Exception:
                pass
            write_log(f"Window fullscreen notice: {win_err}", "DEBUG")

    # Re-verify and maintain proxy transform if invalidateSize slightly affected it
    try:
        driver.execute_script(
            """
            let proxy = document.querySelector('.leaflet-container:not(.hidden) div.leaflet-proxy.leaflet-zoom-animated')
                        || document.querySelector('div.leaflet-proxy.leaflet-zoom-animated')
                        || document.querySelector('div.leaflet-proxy');
            if (proxy && arguments[0]) {
                proxy.style.transform = arguments[0];
            }
            """,
            TARGET_PROXY_TRANSFORM,
        )
    except Exception:
        pass

    if is_map_fullscreen():
        write_log("Fullscreen activated successfully.", "INFO")
    else:
        write_log("Notice: Fullscreen trigger completed.", "WARN")


def click_zoom_in(times: int = 1, timeout: int = 15) -> None:
    """
    Performs zoom-in steps on the active Leaflet map container:
    - Primary: Wheel scroll gesture (-120px)
    - Fallback: Click Leaflet zoom-in control button (+)
    """
    write_log(f"Zooming in: {times} step(s)...", "INFO")

    find_container_script = """
        const containers = [...document.querySelectorAll('div.leaflet-container')];
        return containers.find((c) => {
            const style = window.getComputedStyle(c);
            const rect = c.getBoundingClientRect();
            return (
                !c.classList.contains('hidden') &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 100 &&
                rect.height > 100
            );
        }) || containers[0] || null;
    """

    map_element = WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script(find_container_script)
    )

    if not map_element:
        raise TimeoutException("Не найден видимый контейнер карты для зума.")

    for step in range(times):
        scrolled = False
        try:
            (
                ActionChains(driver)
                .move_to_element(map_element)
                .pause(0.2)
                .scroll_from_origin(ScrollOrigin.from_element(map_element), 0, -120)
                .perform()
            )
            scrolled = True
        except Exception as err:
            write_log(f"Scroll gesture notice: {err}, trying Leaflet zoom button fallback...", "DEBUG")

        if not scrolled:
            # Fallback: click Leaflet zoom-in control button (+)
            try:
                zoom_in_btn = driver.execute_script(
                    """
                    const container = arguments[0];
                    return container ? container.querySelector('a.leaflet-control-zoom-in') : document.querySelector('a.leaflet-control-zoom-in');
                    """,
                    map_element,
                )
                if zoom_in_btn:
                    robust_click(zoom_in_btn, timeout=5)
            except Exception as btn_err:
                write_log(f"Zoom button fallback notice: {btn_err}", "DEBUG")

        if step < times - 1:
            time.sleep(0.5)

    write_log("Zoom-in completed successfully.", "INFO")


def main() -> None:
    global driver

    start_time = time.time()
    log_system_diagnostics()

    ensure_single_instance()

    write_log("Initializing Chrome WebDriver...", "INFO")
    driver = create_chrome_driver()
    write_log("Chrome WebDriver started successfully.", "INFO")

    write_log(f"Navigating to {TARGET_URL}...", "INFO")
    driver.set_page_load_timeout(35)

    page_loaded = False
    for nav_attempt in range(1, 4):
        try:
            driver.get(TARGET_URL)
            page_loaded = True
            break
        except Exception as nav_err:
            write_log(f"Page load attempt {nav_attempt} warning: {nav_err}", "WARN")
            time.sleep(2)

    if not page_loaded:
        write_log("Direct navigation timed out; continuing with DOM wait...", "WARN")

    # Wait for document ready state
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") in ["interactive", "complete"]
        )
        page_title = driver.title or "(No title)"
        write_log(f"Page loaded: '{page_title}'", "INFO")
    except Exception as ready_err:
        write_log(f"Document ready wait notice: {ready_err}", "DEBUG")

    # Step 1: Switch to actual weather layer
    write_log("Step 1/4: Activating actual weather layer...", "INFO")
    activate_actual_weather()

    # Step 2: Apply zoom transform
    write_log("Step 2/4: Applying map zoom transform...", "INFO")
    apply_exact_proxy_zoom_transform()

    # Step 3: Fullscreen button
    write_log("Step 3/4: Activating fullscreen mode...", "INFO")
    click_visible_map_fullscreen()

    # Step 4: Zoom in
    if ENABLE_ZOOM_IN:
        write_log(f"Step 4/4: Performing zoom in ({ZOOM_IN_STEPS} step)...", "INFO")
        click_zoom_in(times=ZOOM_IN_STEPS)

    elapsed = round(time.time() - start_time, 2)
    write_log(f"SUCCESS: All operations completed in {elapsed}s. Browser remains open (detached).", "INFO")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        write_log(f"FATAL ERROR: {err}", "ERROR", exc_info=True)
        # Give user time to see console output if running directly
        if sys.stdout and sys.stdout.isatty():
            print("\nПроизошла ошибка при запуске. Подробности смотрите в файле opener.log")
            try:
                input("Нажмите Enter для выхода...")
            except Exception:
                pass
        sys.exit(1)
