"""Build the main.py + mcu_studio package as a single Windows executable."""

from __future__ import annotations

import os
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys
import tempfile


# Change this value to set the generated executable name.
# Do not include the .exe suffix.
APP_NAME = "Mcu_Studio"


PROJECT_ROOT = Path(__file__).resolve().parent
ENTRY_SCRIPT = PROJECT_ROOT / "main.py"
PACKAGE_DIR = PROJECT_ROOT / "mcu_studio"
SPEC_FILE = PROJECT_ROOT / "packaging" / "Mcu_Studio.spec"
ICON_PNG = PROJECT_ROOT / "assets" / "mcu_studio_anime.png"
ICON_FILE = PROJECT_ROOT / "assets" / "mcu_studio_anime.ico"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
BUILD_ROOT = ARTIFACTS_DIR / "build"
DIST_DIR = PROJECT_ROOT / "dist"

REQUIRED_SOURCE_FILES = (
    ENTRY_SCRIPT,
    PACKAGE_DIR / "__init__.py",
    PACKAGE_DIR / "core.py",
    PACKAGE_DIR / "workers.py",
    PACKAGE_DIR / "ui.py",
    SPEC_FILE,
    ICON_PNG,
    ICON_FILE,
)

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


def validate_project_layout() -> None:
    """Fail early when the maintained source layout is incomplete."""
    missing = [path.relative_to(PROJECT_ROOT) for path in REQUIRED_SOURCE_FILES if not path.is_file()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Missing required project files: {missing_text}")


def validate_output_available(executable: Path) -> None:
    """Report a running/locked previous build before PyInstaller does expensive work."""
    if not executable.exists():
        return
    try:
        with executable.open("ab"):
            pass
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot replace {executable.name}; close the running packaged application first."
        ) from exc


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


def run_packaged_self_test(executable: Path, env: dict[str, str]) -> None:
    """Make missing frozen imports fail by exit code instead of an error dialog."""
    with tempfile.TemporaryDirectory(prefix="mcu_studio_self_test_") as directory:
        report_path = Path(directory) / "report.json"
        test_env = env.copy()
        test_env["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [str(executable), "--self-test", str(report_path)],
            cwd=PROJECT_ROOT,
            env=test_env,
            timeout=90,
        )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Packaged self-test did not produce a valid report (exit {result.returncode})"
            ) from exc
        if result.returncode != 0 or not report.get("ok"):
            raise RuntimeError("Packaged self-test failed:\n" + str(report.get("error") or report))


def main() -> int:
    app_name = validate_app_name(APP_NAME)
    validate_project_layout()

    executable = DIST_DIR / f"{app_name}.exe"
    validate_output_available(executable)

    env = os.environ.copy()
    env["MCU_STUDIO_APP_NAME"] = app_name
    env["MCU_STUDIO_ENTRY_SCRIPT"] = str(ENTRY_SCRIPT)
    env["MCU_STUDIO_ICON_PNG"] = str(ICON_PNG)
    env["MCU_STUDIO_ICON_FILE"] = str(ICON_FILE)
    env["PYQTGRAPH_QT_LIB"] = "PyQt6"
    env["QT_API"] = "pyqt6"

    work_dir = BUILD_ROOT / app_name
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
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
    run_packaged_self_test(executable, env)
    print(f"\nEntry point: {ENTRY_SCRIPT}")
    print(f"Application icon: {ICON_FILE}")
    print(f"Build succeeded: {executable}")
    print("Qt audit passed: PyQt6 only.")
    print("Packaged startup self-test passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"\nBuild failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
