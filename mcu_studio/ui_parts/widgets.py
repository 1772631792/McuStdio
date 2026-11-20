"""Reusable themed widgets shared by every MCU Studio window."""

from .common import (
    QAbstractItemView,
    QApplication,
    QColor,
    QDialog,
    QDoubleValidator,
    QDrag,
    QHBoxLayout,
    QIntValidator,
    QLabel,
    QLineEdit,
    QMenu,
    QMimeData,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPushButton,
    QRectF,
    QRegion,
    QStyle,
    QStyleOptionButton,
    QTabBar,
    QTreeWidget,
    QVBoxLayout,
    Qt,
    _QCheckBox,
    _QComboBox,
    _QMessageBox,
    json,
    pyqtSignal,
)


class SilentMessageBox:
    """Application-styled modal messages that never invoke a system alert sound."""

    StandardButton = _QMessageBox.StandardButton
    _BUTTONS = (
        (_QMessageBox.StandardButton.Yes, "是"),
        (_QMessageBox.StandardButton.No, "否"),
        (_QMessageBox.StandardButton.Ok, "确定"),
        (_QMessageBox.StandardButton.Cancel, "取消"),
    )
    _KINDS = {
        "information": ("i", "#0a84ff"),
        "warning": ("!", "#ff9f0a"),
        "critical": ("×", "#ff453a"),
        "question": ("?", "#0a84ff"),
    }

    @classmethod
    def _show(cls, parent, title, text, kind, buttons=None, default_button=None):
        buttons = buttons or cls.StandardButton.Ok
        default_button = default_button or cls.StandardButton.NoButton
        dialog = QDialog(parent)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(430)
        dialog.setMaximumWidth(720)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)

        content = QHBoxLayout()
        content.setSpacing(16)
        symbol, color = cls._KINDS[kind]
        icon = QLabel(symbol)
        icon.setFixedSize(34, 34)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background: {color}; color: white; border-radius: 17px; "
            "font-size: 20px; font-weight: 800;"
        )
        content.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        message = QLabel(str(text))
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(message, 1)
        layout.addLayout(content)

        result = {"button": cls.StandardButton.NoButton}
        if buttons & cls.StandardButton.No:
            result["button"] = cls.StandardButton.No
        elif buttons & cls.StandardButton.Cancel:
            result["button"] = cls.StandardButton.Cancel
        elif buttons & cls.StandardButton.Ok:
            result["button"] = cls.StandardButton.Ok

        actions = QHBoxLayout()
        actions.addStretch()
        for standard_button, label in cls._BUTTONS:
            if not buttons & standard_button:
                continue
            button = QPushButton(label)
            if standard_button in (cls.StandardButton.Yes, cls.StandardButton.Ok):
                button.setObjectName("primaryButton")
            button.clicked.connect(
                lambda _checked=False, value=standard_button: (
                    result.__setitem__("button", value), dialog.accept()
                )
            )
            actions.addWidget(button)
            if standard_button == default_button:
                button.setDefault(True)
                button.setFocus()
        layout.addLayout(actions)
        dialog.exec()
        return result["button"]

    @classmethod
    def information(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "information", buttons, default_button)

    @classmethod
    def warning(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "warning", buttons, default_button)

    @classmethod
    def critical(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "critical", buttons, default_button)

    @classmethod
    def question(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "question", buttons, default_button)


QMessageBox = SilentMessageBox


class HighContrastComboBox(_QComboBox):
    """Draw a crisp theme-aware chevron over the platform combo-box arrow."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._popup_open = False

    def showPopup(self):
        self._popup_open = True
        self.update()
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        self._popup_open = False
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        role = QPalette.ColorRole.Mid if not self.isEnabled() else QPalette.ColorRole.Text
        color = self.palette().color(role)
        if self.hasFocus() and self.isEnabled():
            color = self.palette().color(QPalette.ColorRole.Highlight)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center_x = self.width() - 12
        center_y = self.height() // 2
        direction = -1 if self._popup_open else 1
        painter.drawLine(center_x - 4, center_y - 2 * direction,
                         center_x, center_y + 2 * direction)
        painter.drawLine(center_x, center_y + 2 * direction,
                         center_x + 4, center_y - 2 * direction)


QComboBox = HighContrastComboBox


class PathComboBox(HighContrastComboBox):
    """Editable path selector that keeps the old line-edit call surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.setSizeAdjustPolicy(_QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(24)

    def text(self):
        return self.currentText()

    def setText(self, value):
        value = str(value or "")
        index = self.findText(value, Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setEditText(value)


class HighContrastCheckBox(_QCheckBox):
    """Keep the checked state visible with every platform and application theme."""

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        checked = self.checkState() != Qt.CheckState.Unchecked
        fill = self.palette().color(
            QPalette.ColorRole.Highlight if checked else QPalette.ColorRole.Base
        )
        border = self.palette().color(
            QPalette.ColorRole.Highlight if checked else QPalette.ColorRole.Mid
        )
        if not self.isEnabled():
            fill = self.palette().color(QPalette.ColorRole.Window)
            border = self.palette().color(QPalette.ColorRole.Mid)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(fill)
        painter.drawRoundedRect(indicator.adjusted(1, 1, -1, -1), 3, 3)
        if checked:
            path = QPainterPath()
            path.moveTo(indicator.left() + 3.5, indicator.center().y())
            path.lineTo(indicator.left() + 6.5, indicator.bottom() - 4.0)
            path.lineTo(indicator.right() - 3.0, indicator.top() + 4.0)
            check_pen = QPen(QColor("#ffffff"), 2.0)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)


QCheckBox = HighContrastCheckBox


class RoundedMenu(QMenu):
    """Rounded application menu with matching rounded submenus."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 11, 11)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def addMenu(self, menu_or_title):
        if isinstance(menu_or_title, str):
            submenu = RoundedMenu(menu_or_title, self)
            submenu.setStyleSheet(self.styleSheet())
            super().addMenu(submenu)
            return submenu
        return super().addMenu(menu_or_title)


class DetachableTabBar(QTabBar):
    """Tab bar that detaches a page when its tab is dragged outside the bar."""

    detach_requested = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pressed_index = -1
        self._press_global_pos = None
        self._dragging_out = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_index = self.tabAt(event.position().toPoint())
            self._press_global_pos = event.globalPosition().toPoint()
            self._dragging_out = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed_index >= 0 and self._press_global_pos is not None:
            distance = (event.globalPosition().toPoint() - self._press_global_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._dragging_out = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        detach_index = self._pressed_index
        should_detach = (
            event.button() == Qt.MouseButton.LeftButton
            and self._dragging_out
            and detach_index >= 0
            and not self.rect().contains(event.position().toPoint())
        )
        global_pos = event.globalPosition().toPoint()
        self._pressed_index = -1
        self._press_global_pos = None
        self._dragging_out = False
        if should_detach:
            self.detach_requested.emit(detach_index, global_pos)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DebugVariableTree(QTreeWidget):
    """Watch tree supporting variable moves between docked and floating panes."""

    MIME_TYPE = "application/x-mcu-studio-variable"

    def __init__(self, owner, slot, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.slot = int(slot)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def startDrag(self, _supported_actions):
        item = self.currentItem()
        if item is None or item.data(0, self.owner.SLIDER_CONTROL_ROLE):
            return
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "").strip()
        if not path:
            return
        payload = {
            "path": path,
            "source_slot": self.slot,
            "is_member": item.parent() is not None,
        }
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(self.MIME_TYPE):
            super().dropEvent(event)
            return
        try:
            payload = json.loads(bytes(event.mimeData().data(self.MIME_TYPE)).decode("utf-8"))
            self.owner.move_debug_variable(
                str(payload["path"]),
                int(payload["source_slot"]),
                self.slot,
                bool(payload.get("is_member")),
            )
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            event.ignore()


class FloatingDebugWindow(QDialog):
    reattach_requested = pyqtSignal(int)

    def __init__(self, slot, title, tree, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.slot = slot
        self._reattaching = False
        self.setWindowTitle(f"{title} — 悬浮调试")
        self.resize(620, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        tree.setParent(self)
        layout.addWidget(tree)
        # QTabWidget.removeTab() hides its page.  Reparenting alone does not
        # reliably clear that explicit hidden state, so make the complete
        # interactive watch tree visible in the floating window.
        tree.show()

    def closeEvent(self, event):
        if not self._reattaching:
            self.reattach_requested.emit(self.slot)
        event.accept()

    def keyPressEvent(self, event):
        owner = self.parent()
        tree = owner.debug_trees.get(self.slot) if owner and hasattr(owner, "debug_trees") else None
        if event.key() == Qt.Key.Key_Delete and tree is not None:
            item = tree.currentItem()
            if item is not None:
                while item.parent():
                    item = item.parent()
                owner.remove_variable_root(item, tree)
            return
        super().keyPressEvent(event)


class InPlaceEditLineEdit(QLineEdit):
    editing_finished_signal = pyqtSignal(str)

    def __init__(self, parent, initial_text, val_type, type_info=None):
        super().__init__(parent)
        self.setText(initial_text)
        if type_info and type_info.get("kind") == "enum":
            self.setPlaceholderText("可输入枚举名或数值")
        elif type_info and (type_info.get("kind") == "pointer" or int(type_info.get("encoding", 0) or 0) == 0x02):
            self.setPlaceholderText("可输入 true/false 或 0x 数值")
        elif (type_info and int(type_info.get("encoding", 0) or 0) == 0x04) or "float" in val_type or "double" in val_type:
            self.setValidator(QDoubleValidator(self))
        else:
            self.setValidator(QIntValidator(-2147483648, 2147483647, self))
        self.selectAll()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.editing_finished_signal.emit(self.text().strip())
        elif event.key() == Qt.Key.Key_Escape:
            self.editing_finished_signal.emit("")  
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.editing_finished_signal.emit(self.text().strip())
        super().focusOutEvent(event)
