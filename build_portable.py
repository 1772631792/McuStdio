"""Build a portable Windows directory containing MCU Studio and OpenOCD."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import build


PORTABLE_MARKER = "portable-mode.flag"
OPENOCD_SOURCE = build.PROJECT_ROOT / "runtime" / "OpenOCD"
SHORTCUT_SCRIPT = build.PROJECT_ROOT / "packaging" / "Create_Desktop_Shortcut.cmd"


def main() -> int:
    app_name = build.validate_app_name(build.APP_NAME)
    build.validate_project_layout()

    openocd_executable = OPENOCD_SOURCE / "bin" / "openocd.exe"
    openocd_scripts = OPENOCD_SOURCE / "share" / "openocd" / "scripts"
    if not openocd_executable.is_file() or not openocd_scripts.is_dir():
        raise FileNotFoundError(
            "Portable build requires runtime/OpenOCD/bin/openocd.exe and its scripts"
        )
    if not SHORTCUT_SCRIPT.is_file():
        raise FileNotFoundError(f"Portable shortcut script is missing: {SHORTCUT_SCRIPT}")

    release_dir = build.DIST_DIR / app_name
    executable = release_dir / f"{app_name}.exe"
    build.validate_output_available(executable)

    env = os.environ.copy()
    env["MCU_STUDIO_APP_NAME"] = app_name
    env["MCU_STUDIO_ENTRY_SCRIPT"] = str(build.ENTRY_SCRIPT)
    env["MCU_STUDIO_ICON_PNG"] = str(build.ICON_PNG)
    env["MCU_STUDIO_ICON_FILE"] = str(build.ICON_FILE)
    env["MCU_STUDIO_ONEDIR"] = "1"
    env["PYQTGRAPH_QT_LIB"] = "PyQt6"
    env["QT_API"] = "pyqt6"

    work_dir = build.BUILD_ROOT / f"{app_name}_portable"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath",
        str(work_dir),
        "--distpath",
        str(build.DIST_DIR),
        str(build.SPEC_FILE),
    ]
    build.run_command(command, env)
    if not executable.is_file():
        raise FileNotFoundError(f"PyInstaller did not create: {executable}")

    bundled_openocd = release_dir / "runtime" / "OpenOCD"
    shutil.copytree(OPENOCD_SOURCE, bundled_openocd, dirs_exist_ok=True)
    (release_dir / "data").mkdir(exist_ok=True)
    (release_dir / PORTABLE_MARKER).write_text(
        "MCU Studio portable data directory marker.\n",
        encoding="utf-8",
    )
    bundled_shortcut_script = release_dir / SHORTCUT_SCRIPT.name
    shutil.copy2(SHORTCUT_SCRIPT, bundled_shortcut_script)

    build.inspect_onedir(release_dir)
    build.run_packaged_self_test(executable, env)
    if not (bundled_openocd / "bin" / "openocd.exe").is_file():
        raise FileNotFoundError("Bundled OpenOCD verification failed")
    if not bundled_shortcut_script.is_file():
        raise FileNotFoundError("Desktop shortcut script verification failed")

    print(f"\nPortable build succeeded: {release_dir}")
    print(f"Application: {executable}")
    print(f"Bundled OpenOCD: {bundled_openocd}")
    print(f"Portable data directory: {release_dir / 'data'}")
    print(f"Desktop shortcut script: {bundled_shortcut_script}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"\nPortable build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
