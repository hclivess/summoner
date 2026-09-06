#!/usr/bin/env python3
"""
summoner - entry point. Layout and behaviour follow STANDARD.md of hclivess/beautiful-software.

Drop photos (raw or JPEG/PNG/TIFF) into the queue, press Start, get developed photos.
"""
import json
import os
import sys
import tempfile
import time

from PySide6.QtCore import QRect, QSize, Qt, QThread, QTimer, QSettings, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFontDatabase, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QSpinBox, QSplitter, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from config import (APP_NAME, APP_VERSION, DEFAULT_SETTINGS, OUTPUT_FORMATS, PROFILES, REPO_URL,
                    WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH)
import develop
from utils import childproc

SELFTEST_VAR = f"{APP_NAME.upper().replace('-', '_')}_SELFTEST"
NO_DIALOGS = bool(os.environ.get(SELFTEST_VAR) or os.environ.get(f"{APP_NAME.upper()}_NO_DIALOGS"))

STATUS_COLORS = {"pending": "#ffffff", "running": "#fff59d", "done": "#a5d6a7",
                 "failed": "#ef9a9a", "stopped": "#cfd8dc"}


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.dirname(os.path.abspath(__file__))


def find_icon() -> str:
    for base in (app_dir(), getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(__file__))):
        if base:
            path = os.path.join(base, "icon.ico")
            if os.path.exists(path):
                return path
    return ""


class WrapLabel(QLabel):
    """A word-wrapping QLabel that reports its real height (see STANDARD.md 3b)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setStyleSheet("color: #666; font-size: 11px;")

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        rect = self.fontMetrics().boundingRect(QRect(0, 0, max(width, 40), 2000),
                                               Qt.TextFlag.TextWordWrap, self.text())
        return rect.height() + 4

    def sizeHint(self) -> QSize:
        return QSize(0, self.heightForWidth(self.width() or 320))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.heightForWidth(self.width() or 320))

    def setText(self, text: str) -> None:
        super().setText(text)
        self.updateGeometry()


class StopRequested(Exception):
    pass


class Worker(QThread):
    """Develops the queue one photo at a time; degrades per item, stops mid-file on request."""
    item_status = Signal(int, str, str)          # row, status, result text
    phase = Signal(str, int, int)                # phase name, file number, total
    file_progress = Signal(int)
    overall_progress = Signal(int)
    log = Signal(str)
    finished_all = Signal(int, int, bool)        # done, failed, stopped

    PHASE_PERCENT = {"decode": 10, "develop": 45, "save": 90}

    def __init__(self, paths: list, settings: dict, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.settings = settings
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        done = failed = 0
        stopped = False
        total = len(self.paths)
        for n, path in enumerate(self.paths):
            if self._stop:
                stopped = True
                self.item_status.emit(n, "stopped", "")
                continue
            self.item_status.emit(n, "running", "")
            self.file_progress.emit(0)

            def on_phase(name, n=n):
                if self._stop:
                    raise StopRequested()
                self.phase.emit(name, n + 1, total)
                self.file_progress.emit(self.PHASE_PERCENT.get(name, 0))

            try:
                out = develop.develop_file(path, self.settings, on_phase)
                size = os.path.getsize(out) / (1024 * 1024)
                self.item_status.emit(n, "done", f"{os.path.basename(out)} · {size:.1f} MB")
                self.log.emit(f"{os.path.basename(path)} -> {out}")
                self.file_progress.emit(100)
                done += 1
            except StopRequested:
                stopped = True
                self.item_status.emit(n, "stopped", "")
                self.log.emit(f"stopped during {os.path.basename(path)}")
            except Exception as exc:                       # degrade per item, never per queue
                failed += 1
                self.item_status.emit(n, "failed", str(exc))
                self.log.emit(f"{os.path.basename(path)} FAILED: {exc}")
            self.overall_progress.emit(round((n + 1) * 100 / total))
        self.finished_all.emit(done, failed, stopped)


class PreviewWorker(QThread):
    """Renders the selected photo at preview size off the GUI thread; latest request wins."""
    ready = Signal(object, str)                  # uint8 RGB ndarray, source path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._request = None
        self._cache = (None, None, None)         # path, small float image, analysis
        self._quit = False

    def render(self, path: str, settings: dict, show_original: bool):
        self._request = (path, settings, show_original)

    def shutdown(self):
        self._quit = True

    def run(self):
        import numpy as np
        import cv2
        while not self._quit:
            request, self._request = self._request, None
            if request is None:
                self.msleep(50)
                continue
            path, settings, show_original = request
            try:
                if self._cache[0] != path:
                    img, _exif = develop.decode(path)
                    scale = 1100 / max(img.shape[:2])
                    if scale < 1:
                        img = cv2.resize(img, (max(1, round(img.shape[1] * scale)),
                                               max(1, round(img.shape[0] * scale))),
                                         interpolation=cv2.INTER_AREA)
                    raw = os.path.splitext(path)[1].lower() in develop.RAW_EXTENSIONS
                    self._cache = (path, img, develop.analyze(img, raw=raw))
                _, base, analysis = self._cache
                if show_original:
                    out = base
                else:
                    if not settings.get("auto_develop", True):
                        analysis = develop.NEUTRAL_ANALYSIS
                    out = develop.render(base, settings, analysis)
                self.ready.emit((np.clip(out, 0, 1) * 255 + 0.5).astype("uint8"), path)
            except Exception as exc:
                self.ready.emit(None, f"{os.path.basename(path)}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.setAcceptDrops(True)
        icon = find_icon()
        if icon:
            self.setWindowIcon(QIcon(icon))

        self.controls = {}
        self.worker = None
        self.queued = set()
        self.started_at = None

        self._build_menu()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Drop photos or folders anywhere in the window")

        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(250)
        self.preview_timer.timeout.connect(self._request_preview)
        self.preview_worker = PreviewWorker(self)
        self.preview_worker.ready.connect(self._show_preview)
        self.preview_worker.start()

        self._load_persisted_settings()
        self.resize(1180, 760)

    # ------------------------------------------------------------- menu

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(QAction("Add files…", self, triggered=self.add_files_dialog))
        file_menu.addAction(QAction("Add folder…", self, triggered=self.add_folder_dialog))
        file_menu.addSeparator()
        file_menu.addAction(QAction("Open output folder", self, triggered=self.open_output_folder))
        file_menu.addSeparator()
        file_menu.addAction(QAction("Exit", self, triggered=self.close))

        presets = self.menuBar().addMenu("&Presets")
        for name, values in self._builtin_presets().items():
            presets.addAction(QAction(name, self,
                                      triggered=lambda _=False, v=values: self.apply_settings({**self.get_settings(), **v})))
        presets.addSeparator()
        presets.addAction(QAction("Save preset…", self, triggered=self.save_preset))
        self.load_menu = presets.addMenu("Load preset")
        self.delete_menu = presets.addMenu("Delete preset")
        for menu, handler in ((self.load_menu, self.load_preset), (self.delete_menu, self.delete_preset)):
            menu.aboutToShow.connect(lambda m=menu, h=handler: self._fill_preset_menu(m, h))
        presets.addSeparator()
        presets.addAction(QAction("Import preset…", self, triggered=self.import_preset))
        presets.addAction(QAction("Export preset…", self, triggered=self.export_preset))
        presets.addSeparator()
        presets.addAction(QAction("Save current as defaults", self, triggered=self.save_as_defaults))
        presets.addAction(QAction("Reset to defaults", self, triggered=self.reset_defaults))

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(QAction("About", self, triggered=self.show_about))

    @staticmethod
    def _builtin_presets():
        return {
            "Web JPEG (2048 px)": {"output_format": "JPEG", "jpeg_quality": 85, "resize_long_edge": 2048},
            "Archive TIFF 16-bit": {"output_format": "TIFF 16-bit", "resize_long_edge": 0},
            "Quick black && white": {"profile": "Black & white", "auto_develop": True},
        }

    # ------------------------------------------------------------- left column

    def _build_left(self) -> QWidget:
        left = QWidget()
        layout = QVBoxLayout(left)

        queue_box = QGroupBox("Queue (drop photos or folders here)")
        queue_layout = QVBoxLayout(queue_box)
        self.queue = QTreeWidget()
        self.queue.setHeaderLabels(["File", "Status", "Result"])
        self.queue.setRootIsDecorated(False)
        self.queue.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.queue.header().resizeSection(0, 260)
        self.queue.header().resizeSection(1, 70)
        self.queue.itemSelectionChanged.connect(self._selection_changed)
        queue_layout.addWidget(self.queue)
        row = QHBoxLayout()
        for label, handler in (("Add files", self.add_files_dialog), ("Add folder", self.add_folder_dialog),
                               ("Remove", self.remove_selected), ("Clear", self.clear_queue)):
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            row.addWidget(btn)
        queue_layout.addLayout(row)
        layout.addWidget(queue_box, 3)

        self.phase_label = QLabel("Idle")
        layout.addWidget(self.phase_label)
        self.file_bar = QProgressBar()
        self.file_bar.setFormat("file %p%")
        self.overall_bar = QProgressBar()
        self.overall_bar.setFormat("overall %p%")
        bars = QHBoxLayout()
        bars.addWidget(self.file_bar, 3)
        bars.addWidget(self.overall_bar, 2)
        layout.addLayout(bars)

        tiles = QHBoxLayout()
        self.tiles = {}
        for key, caption in (("photos", "photos"), ("done", "done"), ("failed", "failed"),
                             ("elapsed", "elapsed")):
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            box = QVBoxLayout(frame)
            box.setContentsMargins(8, 4, 8, 4)
            value = QLabel("0" if key != "elapsed" else "0:00")
            value.setStyleSheet("font-size: 18px; font-weight: bold;")
            cap = QLabel(caption)
            cap.setStyleSheet("color: #666; font-size: 11px;")
            box.addWidget(value)
            box.addWidget(cap)
            tiles.addWidget(frame)
            self.tiles[key] = value
        layout.addLayout(tiles)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log.setMaximumBlockCount(5000)
        layout.addWidget(self.log, 2)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start")
        self.start_btn.setStyleSheet(
            "QPushButton {background:#2e7d32; color:white; font-weight:bold; padding:8px;}"
            "QPushButton:disabled {background:#9e9e9e;}")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setStyleSheet(
            "QPushButton {background:#c62828; color:white; font-weight:bold; padding:8px;}"
            "QPushButton:disabled {background:#9e9e9e;}")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        buttons.addWidget(self.start_btn, 3)
        buttons.addWidget(self.stop_btn, 1)
        layout.addLayout(buttons)
        return left

    # ------------------------------------------------------------- right column

    def _slider(self, key: str, low: int, high: int, tooltip: str) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(DEFAULT_SETTINGS[key])
        slider.setToolTip(tooltip)
        slider.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        slider.setMinimumWidth(0)
        value = QLabel(str(DEFAULT_SETTINGS[key]))
        value.setMinimumWidth(32)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider.valueChanged.connect(lambda v, lab=value: (lab.setText(str(v)), self._settings_changed()))
        slider.sliderReleased.connect(self._settings_changed)
        row.addWidget(slider)
        row.addWidget(value)
        self.controls[key] = slider
        return holder

    def _build_right(self) -> QWidget:
        right = QWidget()
        layout = QVBoxLayout(right)

        preview_box = QGroupBox("Preview")
        pv = QVBoxLayout(preview_box)
        self.preview = QLabel("Select a photo in the queue")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(180)
        self.preview.setStyleSheet("background:#222; color:#aaa;")
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        pv.addWidget(self.preview)
        self.show_original = QCheckBox("Show original")
        self.show_original.setToolTip("Compare with the photo as it came out of the camera")
        self.show_original.toggled.connect(self._settings_changed)
        pv.addWidget(self.show_original)
        layout.addWidget(preview_box, 2)

        tabs = QTabWidget()
        tabs.addTab(self._wrap_scroll(self._build_develop_tab()), "Develop")
        tabs.addTab(self._wrap_scroll(self._build_output_tab()), "Output")
        layout.addWidget(tabs, 3)
        return right

    @staticmethod
    def _wrap_scroll(widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(widget)
        return area

    def _build_develop_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        auto_box = QGroupBox("Auto")
        form = QFormLayout(auto_box)
        auto = QCheckBox("Auto develop each photo")
        auto.setChecked(DEFAULT_SETTINGS["auto_develop"])
        auto.setToolTip("Analyse every photo and correct white balance, exposure, levels and\n"
                        "blown or crushed areas before your sliders are applied")
        auto.toggled.connect(self._settings_changed)
        self.controls["auto_develop"] = auto
        form.addRow(auto)
        profile = QComboBox()
        profile.addItems(list(PROFILES))
        profile.setToolTip("The look applied on top of the auto correction")
        profile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        profile.setMinimumWidth(0)
        profile.currentTextChanged.connect(lambda _t: self._settings_changed())
        self.controls["profile"] = profile
        form.addRow("Profile", profile)
        form.addRow(WrapLabel("Auto measures each photo separately; the sliders below then adjust "
                              "on top of it, so 0 means \"trust the analysis\"."))
        layout.addWidget(auto_box)

        light_box = QGroupBox("Light")
        form = QFormLayout(light_box)
        form.addRow("Exposure", self._slider("exposure", -400, 400, "Brightness in 1/100 EV: 100 = one stop brighter"))
        form.addRow("Contrast", self._slider("contrast", -100, 100, "Steepen or flatten the tone curve around the midtones"))
        form.addRow("Highlights", self._slider("highlights", -100, 100, "Negative recovers bright skies and blown areas"))
        form.addRow("Shadows", self._slider("shadows", -100, 100, "Positive opens dark areas without washing out the rest"))
        layout.addWidget(light_box)

        color_box = QGroupBox("Colour")
        form = QFormLayout(color_box)
        form.addRow("Temperature", self._slider("temperature", -100, 100, "Blue (negative) to amber (positive)"))
        form.addRow("Tint", self._slider("tint", -100, 100, "Green (negative) to magenta (positive)"))
        form.addRow("Vibrance", self._slider("vibrance", -100, 100, "Saturates muted colours first; kind to skin"))
        form.addRow("Saturation", self._slider("saturation", -100, 100, "All colours equally; -100 is black and white"))
        layout.addWidget(color_box)

        detail_box = QGroupBox("Detail")
        form = QFormLayout(detail_box)
        form.addRow("Clarity", self._slider("clarity", -100, 100, "Local midtone contrast: punch (positive) or soften (negative)"))
        form.addRow("Sharpen", self._slider("sharpen", 0, 100, "Fine detail sharpening applied after everything else"))
        form.addRow("Denoise", self._slider("denoise", 0, 100, "Edge-preserving noise reduction; higher is slower"))
        form.addRow("Vignette", self._slider("vignette", -100, 100, "Darken (negative) or brighten (positive) the corners"))
        layout.addWidget(detail_box)
        layout.addStretch(1)
        return page

    def _build_output_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox("Output")
        form = QFormLayout(box)
        fmt = QComboBox()
        fmt.addItems(OUTPUT_FORMATS)
        fmt.setToolTip("JPEG for sharing, PNG for lossless 8-bit, TIFF for 16-bit archival")
        fmt.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        fmt.setMinimumWidth(0)
        self.controls["output_format"] = fmt
        form.addRow("Format", fmt)

        quality = QSpinBox()
        quality.setRange(1, 100)
        quality.setValue(DEFAULT_SETTINGS["jpeg_quality"])
        quality.setToolTip("JPEG quality; 92 keeps artefacts invisible, 85 is fine for the web")
        quality.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        quality.setMinimumWidth(0)
        self.controls["jpeg_quality"] = quality
        form.addRow("JPEG quality", quality)

        resize = QSpinBox()
        resize.setRange(0, 20000)
        resize.setSingleStep(256)
        resize.setSpecialValueText("keep original size")
        resize.setSuffix(" px")
        resize.setValue(DEFAULT_SETTINGS["resize_long_edge"])
        resize.setToolTip("Resize so the long edge is this many pixels; 0 keeps the original size")
        resize.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        resize.setMinimumWidth(0)
        self.controls["resize_long_edge"] = resize
        form.addRow("Resize long edge", resize)

        out_row = QWidget()
        row = QHBoxLayout(out_row)
        row.setContentsMargins(0, 0, 0, 0)
        out_dir = QLineEdit(DEFAULT_SETTINGS["output_dir"])
        out_dir.setPlaceholderText("a \"developed\" folder next to each photo")
        out_dir.setToolTip("Where developed photos go; empty writes a \"developed\" folder next to each photo")
        out_dir.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        out_dir.setMinimumWidth(0)
        self.controls["output_dir"] = out_dir
        browse = QPushButton("…")
        browse.setFixedWidth(28)
        browse.clicked.connect(self.browse_output)
        row.addWidget(out_dir)
        row.addWidget(browse)
        form.addRow("Output folder", out_row)
        form.addRow(WrapLabel("Originals are never modified or overwritten. JPEG output keeps the "
                              "camera's EXIF when the source was a JPEG or TIFF."))
        layout.addWidget(box)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------- settings plumbing

    def get_settings(self) -> dict:
        values = {}
        for key, widget in self.controls.items():
            if isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                values[key] = widget.currentText()
            elif isinstance(widget, (QSpinBox, QSlider)):
                values[key] = widget.value()
            elif isinstance(widget, QLineEdit):
                values[key] = widget.text().strip()
        return values

    def apply_settings(self, values: dict):
        for key, widget in self.controls.items():
            if key not in values:
                continue
            value = values[key]
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                index = widget.findText(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, (QSpinBox, QSlider)):
                widget.setValue(int(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
        self._settings_changed()

    def _load_persisted_settings(self):
        stored = QSettings("hclivess", APP_NAME).value("settings")
        if stored:
            try:
                self.apply_settings({**DEFAULT_SETTINGS, **json.loads(stored)})
            except (ValueError, TypeError):
                pass

    def closeEvent(self, event):
        QSettings("hclivess", APP_NAME).setValue("settings", json.dumps(self.get_settings()))
        self.preview_worker.shutdown()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        self.preview_worker.wait(2000)
        super().closeEvent(event)

    # ------------------------------------------------------------- presets

    def _presets_dir(self) -> str:
        path = os.path.join(app_dir(), "presets")
        os.makedirs(path, exist_ok=True)
        return path

    def _fill_preset_menu(self, menu, handler):
        menu.clear()
        names = sorted(os.path.splitext(fn)[0] for fn in os.listdir(self._presets_dir())
                       if fn.endswith(".json"))
        if not names:
            action = menu.addAction("(no saved presets)")
            action.setEnabled(False)
        for name in names:
            menu.addAction(QAction(name, menu, triggered=lambda _=False, n=name: handler(n)))

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        path = os.path.join(self._presets_dir(), name.strip() + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.get_settings(), fh, indent=2)
        self.log_line(f"preset saved: {path}")

    def load_preset(self, name: str):
        with open(os.path.join(self._presets_dir(), name + ".json"), encoding="utf-8") as fh:
            self.apply_settings({**DEFAULT_SETTINGS, **json.load(fh)})
        self.log_line(f"preset loaded: {name}")

    def delete_preset(self, name: str):
        os.remove(os.path.join(self._presets_dir(), name + ".json"))
        self.log_line(f"preset deleted: {name}")

    def import_preset(self):
        path, _f = QFileDialog.getOpenFileName(self, "Import preset", "", "Presets (*.json)")
        if path:
            with open(path, encoding="utf-8") as fh:
                self.apply_settings({**DEFAULT_SETTINGS, **json.load(fh)})

    def export_preset(self):
        path, _f = QFileDialog.getSaveFileName(self, "Export preset", "preset.json", "Presets (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.get_settings(), fh, indent=2)

    def save_as_defaults(self):
        QSettings("hclivess", APP_NAME).setValue("settings", json.dumps(self.get_settings()))
        self.log_line("current settings saved as defaults")

    def reset_defaults(self):
        QSettings("hclivess", APP_NAME).remove("settings")
        self.apply_settings(dict(DEFAULT_SETTINGS))
        self.log_line("settings reset to defaults")

    # ------------------------------------------------------------- queue

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls()])

    def add_files_dialog(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(develop.ALL_EXTENSIONS))
        paths, _f = QFileDialog.getOpenFileNames(self, "Add photos", "", f"Photos ({patterns})")
        self.add_paths(paths)

    def add_folder_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "Add folder")
        if path:
            self.add_paths([path])

    def add_paths(self, paths):
        added = duplicates = 0
        for path in develop.collect(paths):
            key = os.path.normcase(path)
            if key in self.queued:
                duplicates += 1
                continue
            self.queued.add(key)
            item = QTreeWidgetItem([os.path.basename(path), "pending", ""])
            item.setToolTip(0, path)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            self._paint_status(item, "pending")
            self.queue.addTopLevelItem(item)
            added += 1
        if added:
            self.log_line(f"{added} photo{'s' if added != 1 else ''} added")
            if self.queue.currentItem() is None:
                self.queue.setCurrentItem(self.queue.topLevelItem(0))
        if duplicates:
            self.log_line(f"{duplicates} already in queue")
        self._update_tiles()

    def remove_selected(self):
        for item in self.queue.selectedItems():
            self.queued.discard(os.path.normcase(item.data(0, Qt.ItemDataRole.UserRole)))
            self.queue.takeTopLevelItem(self.queue.indexOfTopLevelItem(item))
        self._update_tiles()

    def clear_queue(self):
        self.queue.clear()
        self.queued.clear()
        self._update_tiles()

    @staticmethod
    def _paint_status(item: QTreeWidgetItem, status: str):
        item.setText(1, status)
        brush = QBrush(QColor(STATUS_COLORS.get(status, "#ffffff")))
        for col in range(3):
            item.setBackground(col, brush)
            item.setForeground(col, QBrush(QColor("#000000")))

    # ------------------------------------------------------------- preview

    def _selection_changed(self):
        self.preview_timer.start()

    def _settings_changed(self):
        self.preview_timer.start()

    def _request_preview(self):
        item = self.queue.currentItem()
        if item is None:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        self.preview_worker.render(path, self.get_settings(), self.show_original.isChecked())

    def _show_preview(self, array, path: str):
        if array is None:
            self.preview.setText(f"Preview failed — {path}")
            self.preview.setPixmap(QPixmap())
            return
        height, width, _c = array.shape
        image = QImage(array.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.preview.width() or 400, self.preview.height() or 300,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.preview_timer.start()

    # ------------------------------------------------------------- run

    def sanity_check(self, settings: dict) -> list:
        warnings = []
        if settings["profile"] == "Black & white" and (settings["vibrance"] > 0 or settings["saturation"] > 0):
            warnings.append("Black & white profile with vibrance or saturation raised: the raise has no effect.")
        if settings["denoise"] >= 60:
            warnings.append("Denoise above 60 is slow on large photos.")
        if 0 < settings["resize_long_edge"] < 256:
            warnings.append(f"Resize to {settings['resize_long_edge']} px produces thumbnail-size output.")
        if settings["output_format"] == "JPEG" and settings["jpeg_quality"] < 50:
            warnings.append(f"JPEG quality {settings['jpeg_quality']} shows visible artefacts.")
        return warnings

    def start(self):
        if self.worker and self.worker.isRunning():
            return
        paths = [self.queue.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                 for i in range(self.queue.topLevelItemCount())]
        if not paths:
            self.log_line("nothing to do: the queue is empty")
            return
        settings = self.get_settings()
        warnings = self.sanity_check(settings)
        if warnings and not NO_DIALOGS:
            answer = QMessageBox.warning(self, "Before you start", "\n\n".join(warnings),
                                         QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                return
        for i in range(self.queue.topLevelItemCount()):
            item = self.queue.topLevelItem(i)
            self._paint_status(item, "pending")
            item.setText(2, "")
        self.started_at = time.monotonic()
        self.elapsed_timer.start()
        self.overall_bar.setValue(0)
        self.file_bar.setValue(0)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker = Worker(paths, settings, self)
        self.worker.item_status.connect(self._on_item_status)
        self.worker.phase.connect(lambda phase, n, total:
                                  self.phase_label.setText(f"{phase} · photo {n}/{total}"))
        self.worker.file_progress.connect(self.file_bar.setValue)
        self.worker.overall_progress.connect(self.overall_bar.setValue)
        self.worker.log.connect(self.log_line)
        self.worker.finished_all.connect(self._on_finished)
        self.worker.start()
        self.log_line(f"started: {len(paths)} photo{'s' if len(paths) != 1 else ''}, "
                      f"profile {settings['profile']}, auto {'on' if settings['auto_develop'] else 'off'}")

    def stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.log_line("stopping…")

    def _on_item_status(self, row: int, status: str, result: str):
        item = self.queue.topLevelItem(row)
        if item:
            self._paint_status(item, status)
            item.setText(2, result)
        self._update_tiles()

    def _on_finished(self, done: int, failed: int, stopped: bool):
        self.elapsed_timer.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.phase_label.setText("Stopped" if stopped else "Finished")
        word = "stopped" if stopped else "finished"
        self.log_line(f"{word}: {done} developed, {failed} failed")
        self.statusBar().showMessage(f"{word}: {done} developed, {failed} failed")
        self._update_tiles()

    def _update_tiles(self):
        total = self.queue.topLevelItemCount()
        done = failed = 0
        for i in range(total):
            status = self.queue.topLevelItem(i).text(1)
            done += status == "done"
            failed += status == "failed"
        self.tiles["photos"].setText(str(total))
        self.tiles["done"].setText(str(done))
        self.tiles["failed"].setText(str(failed))

    def _tick_elapsed(self):
        if self.started_at is not None:
            seconds = int(time.monotonic() - self.started_at)
            self.tiles["elapsed"].setText(f"{seconds // 60}:{seconds % 60:02d}")

    # ------------------------------------------------------------- misc

    def log_line(self, text: str):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Output folder")
        if path:
            self.controls["output_dir"].setText(path)

    def open_output_folder(self):
        out = self.get_settings()["output_dir"]
        if not out and self.queue.topLevelItemCount():
            first = self.queue.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole)
            out = os.path.join(os.path.dirname(first), "developed")
        if out and os.path.isdir(out):
            QDesktopServices.openUrl(f"file:///{out.replace(os.sep, '/')}")
        else:
            self.log_line("no output folder yet")

    def show_about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME} v{APP_VERSION}</b><br>"
                          f"Drop photos in, press Start, get developed photos.<br>"
                          f"<a href='{REPO_URL}'>{REPO_URL}</a>")


def _selftest(win: MainWindow, path: str):
    """
    Headless smoke test for CI: develop `path` with safe defaults, exit 0 only on real output.
    Everything runs on the GUI thread via a poll timer - worker signals to plain functions would
    execute on the worker thread - and the window is closed properly so no QThread dies running.
    """
    out_dir = tempfile.mkdtemp(prefix="summoner-selftest-")
    win.apply_settings({**DEFAULT_SETTINGS, "output_dir": out_dir, "denoise": 10})
    win.add_paths([path])
    started = [False]
    timer = QTimer(win)

    def finish(ok: bool, why: str = ""):
        timer.stop()
        print(f"selftest: {'ok' if ok else 'FAILED'}{f' ({why})' if why else ''}", flush=True)
        win.close()
        QTimer.singleShot(0, lambda: QApplication.exit(0 if ok else 1))

    def poll():
        if not started[0]:
            if win.queue.topLevelItemCount() == 0:
                return finish(False, "nothing queued")
            win.start()
            if win.worker is None:
                return finish(False, "worker did not start")
            started[0] = True
            return
        if win.worker.isRunning():
            return
        statuses = [win.queue.topLevelItem(i).text(1)
                    for i in range(win.queue.topLevelItemCount())]
        if not statuses or any(status != "done" for status in statuses):
            return finish(False, f"statuses: {statuses}")
        from PIL import Image
        outputs = os.listdir(out_dir)
        if not outputs:
            return finish(False, "no output files")
        for fn in outputs:
            full = os.path.join(out_dir, fn)
            try:
                with Image.open(full) as im:
                    im.verify()
                print(f"selftest output ok: {fn} ({os.path.getsize(full)} bytes)", flush=True)
            except Exception as exc:
                return finish(False, f"bad output {fn}: {exc}")
        finish(True)

    timer.setInterval(250)
    timer.timeout.connect(poll)
    timer.start()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"hclivess.{APP_NAME}")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    childproc.install_qt_hook(app)
    icon = find_icon()
    if icon:
        app.setWindowIcon(QIcon(icon))
    win = MainWindow()
    win.show()
    selftest = os.environ.get(SELFTEST_VAR)
    if selftest:
        _selftest(win, selftest)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
