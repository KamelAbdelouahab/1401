"""Read only the bounded receipt for an explicit scan run; saved evidence is never a current-PC claim. (ScanEvidence.cs)"""
import json
import os
import re
import shutil
import tempfile
import uuid

MAX_BYTES = 2 * 1024 * 1024
RUN_TOKEN = re.compile(r"\A[0-9a-f]{32}\Z")


class Selection:
    def __init__(self, path, current_run):
        self.path, self.current_run = path, current_run


def run_directory(work, token):
    if token is None or not RUN_TOKEN.match(token):
        raise ValueError("Invalid scan run token.")
    return os.path.join(work, "scan-" + token)


def remember(work, token):
    run_directory(work, token)
    os.makedirs(work, exist_ok=True)
    destination = os.path.join(work, "latest-scan.json")
    temporary = destination + ".tmp-" + uuid.uuid4().hex
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump({"schema": "nullmoth-latest-scan/1", "run_id": token}, fh)
    os.replace(temporary, destination)


def _read(path, maximum):
    try:
        if os.path.islink(path) or os.path.getsize(path) > maximum:
            return None
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else None
    except Exception:  # noqa: BLE001
        return None


def find(work, current_token, allow_saved):
    token = current_token
    current = token is not None
    if not current:
        if not allow_saved:
            return None
        latest = _read(os.path.join(work, "latest-scan.json"), 4096)
        if not latest or latest.get("schema") != "nullmoth-latest-scan/1" or "run_id" not in latest:
            return None
        token = str(latest["run_id"])
    if token is None or not RUN_TOKEN.match(token):
        return None
    directory = run_directory(work, token)
    if os.path.islink(directory):
        return None
    path = os.path.join(directory, "scan-evidence.json")
    receipt = _read(path, MAX_BYTES)
    if not receipt or receipt.get("schema") != "nullmoth-scan-evidence/1" or receipt.get("run_id") != token:
        return None
    return Selection(path, current)


def verify():
    work = tempfile.mkdtemp(prefix="1401-scan-test-")
    try:
        token = uuid.uuid4().hex
        directory = run_directory(work, token)
        os.makedirs(directory)
        path = os.path.join(directory, "scan-evidence.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": "nullmoth-scan-evidence/1", "run_id": token, "scan_status": "failed", "report_sha256": None}, fh)
        remember(work, token)
        current, saved = find(work, token, False), find(work, None, True)
        if not current or not current.current_run or not saved or saved.current_run or current.path != saved.path:
            return False
        if find(work, uuid.uuid4().hex, True) is not None:
            return False  # never substitute a stale run
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"schema":"nullmoth-scan-evidence/1","run_id":"other"}')
        if find(work, token, False) is not None:
            return False
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x" * (MAX_BYTES + 1))
        return find(work, token, False) is None
    finally:
        shutil.rmtree(work, ignore_errors=True)
