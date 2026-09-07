#!/usr/bin/env python3
"""
Take thumb.png headlessly: QT_QPA_PLATFORM=offscreen python tools/screenshot.py [photo-folder]
Runs the app on REAL sample inputs, waits for the queue to finish, then grabs the window.
Pass a folder of real, freely-licensed photos (CC0 raws) for the screenshot that ships in the
README - never anyone's personal material (STANDARD.md 11) and never a faked state. Without an
argument it falls back to the generated scenes used by CI.
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SUMMONER_NO_DIALOGS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from config import DEFAULT_SETTINGS
import develop
import main as app_main
from tools.make_samples import write_samples

work = tempfile.mkdtemp(prefix="summoner-shot-")
if len(sys.argv) > 1:
    folder = sys.argv[1]
    samples = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
               if develop.is_photo(os.path.join(folder, f))]
else:
    samples = write_samples(os.path.join(work, "shoot"))

app = QApplication(sys.argv)
app.setStyle("Fusion")
win = app_main.MainWindow()
win.apply_settings({**DEFAULT_SETTINGS, "output_dir": os.path.join(work, "developed")})
win.resize(1180, 760)
win.show()
win.add_paths(samples[:3])
win.queue.setCurrentItem(win.queue.topLevelItem(0))


def grab():
    win.grab().save("thumb.png")
    print("thumb.png saved - now LOOK at it and fix overflow or bad text", flush=True)
    win.close()
    app.quit()


def poll():
    if win.worker is not None and not win.worker.isRunning():
        QTimer.singleShot(800, grab)      # give the preview a beat to land
        timer.stop()


timer = QTimer()
timer.setInterval(250)
timer.timeout.connect(poll)
QTimer.singleShot(1500, win.start)        # let the preview render first
QTimer.singleShot(1600, timer.start)
app.exec()
