"""Build MCU Live Watch Studio with the repository's verified PyInstaller spec."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


# Change this value to set the generated executable name.
# Do not include the .exe suffix.
APP_NAME = "Mcu_Studio"


PROJECT_ROOT = Path(__file__).resolve().parent
SPEC_FILE = PROJECT_ROOT / "packaging" / "Mcu_Studio.spec"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
BUILD_ROOT = ARTIFACTS_DIR / "build"
DIST_DIR = PROJECT_ROOT / "dist"

OTHER_QT_BINDINGS = (
    "PySide6",
    "shiboken6",
    "PyQt5",
    "PySide2",
    "shiboken2",
)


def validate_app_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("APP_NAME cannot be empty")
    if name.lower().endswith(".exe"):
        raise ValueError("APP_NAME must not include the .exe suffix")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("APP_NAME may contain only letters, numbers, '.', '_' and '-'")
    return name


def run_command(command: list[str], env: dict[str, str]) -> None:
    print("\n>", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def inspect_archive(executable: Path, env: dict[str, str]) -> None:
    viewer = shutil.which("pyi-archive_viewer")
    if viewer:
        command = [viewer, "-l", str(executable)]
    else:
        command = [
            sys.executable,
            "-m",
            "PyInstaller.utils.cliutils.archive_viewer",
            "-l",
            str(executable),
        ]

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    archive_listing = result.stdout + result.stderr
    unwanted = [name for name in OTHER_QT_BINDINGS if name.lower() in archive_listing.lower()]
    if unwanted:
        raise RuntimeError(
            "The executable contains another Qt binding: " + ", ".join(unwanted)
        )
    if "PyQt6" not in archive_listing:
        raise RuntimeError("PyQt6 was not found in the packaged executable")


def main() -> int:
    app_name = validate_app_name(APP_NAME)
    if not SPEC_FILE.is_file():
        raise FileNotFoundError(f"Missing PyInstaller spec: {SPEC_FILE}")

    env = os.environ.copy()
    env["MCU_STUDIO_APP_NAME"] = app_name
    env["PYQTGRAPH_QT_LIB"] = "PyQt6"
    env["QT_API"] = "pyqt6"

    work_dir = BUILD_ROOT / app_name
    executable = DIST_DIR / f"{app_name}.exe"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--workpath",
        str(work_dir),
        "--distpath",
        str(DIST_DIR),
        str(SPEC_FILE),
    ]

    run_command(command, env)
    if not executable.is_file():
        raise FileNotFoundError(f"PyInstaller did not create: {executable}")

    inspect_archive(executable, env)
    print(f"\nBuild succeeded: {executable}")
    print("Qt audit passed: PyQt6 only.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"\nBuild failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
