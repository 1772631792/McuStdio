"""Source entry point for MCU Live Watch Studio."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import traceback


def packaged_self_test(report_path="") -> int:
    """Import and construct the packaged UI stack without accessing hardware."""
    report = {"ok": False, "checks": [], "error": ""}
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtCore, QtGui, QtWidgets, uic
        import pyqtgraph as pg
        from mcu_studio import core, ui, workers

        report["checks"].append("PyQt6.uic")
        application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        plot = pg.PlotWidget()
        plot.plot([0.0, 1.0], [0.0, 1.0])
        application.processEvents()
        plot.close()
        original_argv0 = sys.argv[0]
        database = None
        dialog = None
        try:
            self_test_dir = Path(report_path).resolve().parent if report_path else Path.cwd()
            sys.argv[0] = str(self_test_dir / "mcu_studio_self_test.exe")
            database = core.LiveWatchDB()
            dialog = ui.LoginConfigDialog(database)
            dialog.show()
            application.processEvents()
            if not dialog.windowTitle().startswith("MCU Live Watch Studio"):
                raise RuntimeError(f"Unexpected login window title: {dialog.windowTitle()!r}")
        finally:
            if dialog is not None:
                dialog.close()
            if database is not None:
                database.conn.close()
            sys.argv[0] = original_argv0
        report["checks"].extend([
            QtCore.__name__, QtGui.__name__, ui.MCUStudioWindow.__name__,
            workers.VariableWriteWorker.__name__, core.ElfDatabase.__name__,
            "pyqtgraph.PlotWidget", "LoginConfigDialog",
        ])
        report["ok"] = True
    except BaseException:
        report["error"] = traceback.format_exc()
    if report_path:
        try:
            Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return 2
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        index = sys.argv.index("--self-test")
        report_file = sys.argv[index + 1] if index + 1 < len(sys.argv) else ""
        raise SystemExit(packaged_self_test(report_file))
    from mcu_studio import run_application

    raise SystemExit(run_application())
