"""python -m a1401 [--verify-package <result.json>]"""
import os
import sys
import time
import traceback


def main(argv):
    from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415

    from . import durable, engine  # noqa: PLC0415
    app = QApplication(sys.argv[:1])
    app.setApplicationName("1401")
    app.setDesktopFileName("1401")
    if len(argv) == 2 and argv[0] == "--verify-package":
        from . import verify  # noqa: PLC0415
        return verify.run(argv[1], app)
    from .mainwindow import MainWindow  # noqa: PLC0415
    window = MainWindow()

    def hook(etype, value, tb):
        # A saved local crash report is offered after restart; saving failures are reported explicitly.
        try:
            window._crash(etype, value, tb)
        except Exception:  # noqa: BLE001
            durable.save(engine.WORK, "crash", engine.product_version(),
                         "time: " + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()) + "\n" + " ".join(os.uname()) +
                         "\n\n" + "".join(traceback.format_exception(etype, value, tb)))
            QMessageBox.critical(None, "1401", str(value))
    sys.excepthook = hook
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
