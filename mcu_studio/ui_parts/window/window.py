"""Main workspace window composed from focused mixins."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMainWindow

from .lifecycle import _WindowLifecycleMixin
from .elf import _WindowElfMixin
from .backend import _WindowBackendMixin
from .workspace import _WindowWorkspaceMixin
from .sampling import _WindowSamplingMixin
from .variables import _WindowVariablesMixin
from .plot import _WindowPlotMixin
from .close import _WindowCloseMixin

from ..common import (
    LiveWatchDB,
    QApplication,
    QDialog,
    QIcon,
    Qt,
    QTimer,
    os,
    pg,
    sys,
)
from ..theme import (
    DARK_STYLE,
)
from ..login import (
    LoginConfigDialog,
)


class MCUStudioWindow(
    _WindowLifecycleMixin,
    _WindowElfMixin,
    _WindowBackendMixin,
    _WindowWorkspaceMixin,
    _WindowSamplingMixin,
    _WindowVariablesMixin,
    _WindowPlotMixin,
    _WindowCloseMixin,
    QMainWindow,
):
    """Workspace window assembled from lifecycle/ELF/backend/plot/sampling mixins."""

    WORKSPACE_STATE_KEY = "workspace_state_v1"
    INLINE_SLIDER_STATE_KEY = "inline_slider_state_v1"
    FFT_STATE_KEY = "fft_plot_state_v1"
    SLIDER_CONTROL_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    variable_write_result = pyqtSignal(str, int, bool, str, object)
    session_closed = pyqtSignal(str)
    # A backend thread wedged inside a vendor DLL call never finishes; retrying
    # the graceful close forever makes the window impossible to close.  After
    # this many 250 ms retries the exit is forced.
    CLOSE_RETRY_LIMIT = 8


def run_application() -> int:
    """Create the Qt application and run the login/main-window flow."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    icon_path = os.path.join(bundle_root, "assets", "mcu_studio_anime.png")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    pg.setConfigOption("foreground", "#e0e0e0")
    app.setStyleSheet(DARK_STYLE)
    db = LiveWatchDB()
    active = {"dialog": None, "window": None}

    def workspace_closed(destination):
        closed_window = active.get("window")
        active["window"] = None
        if closed_window is not None:
            QTimer.singleShot(0, closed_window.deleteLater)
        if destination == "config":
            QTimer.singleShot(0, show_configuration)
        else:
            QTimer.singleShot(0, app.quit)

    def show_configuration():
        dialog = LoginConfigDialog(db)
        active["dialog"] = dialog
        result = dialog.exec()
        active["dialog"] = None
        if result != QDialog.DialogCode.Accepted or not dialog.is_connected:
            QTimer.singleShot(0, app.quit)
            return
        window = MCUStudioWindow(dialog.config, db)
        active["window"] = window
        window.session_closed.connect(workspace_closed)
        window.show()

    QTimer.singleShot(0, show_configuration)
    return app.exec()
