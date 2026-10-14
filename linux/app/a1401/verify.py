"""Non-destructive packaged startup check (PackageVerification.cs): opens the real window, drives its handlers with
fixtures, closes it, and writes the result as JSON. No scan, no disk listing that writes, no upload."""
import json
import os
import re
import shutil
import tempfile

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import durable, engine, scanevidence
from .mainwindow import MainWindow, is_support_notice, new_batch


def verify_local_reports():
    root = tempfile.mkdtemp(prefix="1401-report-verification-")
    try:
        saved = durable.save(root, "verify", engine.product_version(), "preserved operation failure output")
        if not saved.saved:
            return False
        with open(saved.path, "rb") as fh:
            original = fh.read()
        if saved.path not in durable.find(root):
            return False
        second = durable.save(root, "verify", engine.product_version(), "second attempt output")
        with open(saved.path, "rb") as fh:
            if not second.saved or second.path == saved.path or fh.read() != original:
                return False
        foreign = os.path.join(root, "foreign-file")
        with open(foreign, "w") as fh:
            fh.write("retained foreign bytes")
        failure = durable.save(foreign, "verify", engine.product_version(), "must not overwrite")
        with open(foreign) as fh, open(saved.path, "rb") as fh2:
            return (not failure.saved) and bool(failure.failure) and fh.read() == "retained foreign bytes" and fh2.read() == original
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run(result_path, app):
    result = {"version": engine.product_version()}
    shown = False
    try:
        result["engine_complete"] = engine.missing() is None
        batches = set()
        fresh = True
        for _ in range(1024):
            b = new_batch()
            fresh &= bool(re.fullmatch(r"1401-app-[0-9a-f]{16}", b)) and b not in batches
            batches.add(b)
        result["fresh_upload_batches"] = fresh
        result["scan_evidence_binding"] = scanevidence.verify()
        result["packaged_local_report_lifecycle"] = verify_local_reports()
        result["support_notices_visible"] = (is_support_notice("Laptop display note: unsupported panel") and
                                             is_support_notice("Intel I225-LM vP requires link testing") and
                                             is_support_notice("NullMoth driver selected") and
                                             not is_support_notice("ACPI loader transcript"))
        form = MainWindow(verification=True)
        state = {}

        def check():
            state["shown"] = form.isVisible() and form.centralWidget() is not None
            result["support_notices_visible"] = result["support_notices_visible"] and form.verify_support_notice_presentation()
            result["firmware_prerequisite_navigation"] = form.verify_firmware_prerequisite_navigation(app)
            result["firmware_navigation_fixture_phase"] = form.firmware_verification_phase
            form.close()
            app.quit()
        form.show()
        QTimer.singleShot(250, check)
        app.exec()
        shown = state.get("shown", False)
        form.offer_stick_logs(True)   # a closed window collects nothing and asks nothing
        result["closed_form_log_collection_guard"] = form.closed
        result["window_shown_and_closed"] = shown
        result["ok"] = all(result.get(k) is True for k in (
            "engine_complete", "fresh_upload_batches", "scan_evidence_binding", "packaged_local_report_lifecycle",
            "support_notices_visible", "firmware_prerequisite_navigation", "closed_form_log_collection_guard",
            "window_shown_and_closed"))
    except Exception as error:  # noqa: BLE001
        result["ok"] = False
        result["error_type"] = type(error).__name__
        result["error"] = str(error)[:500]
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return 0 if result["ok"] else 1
