"""1401 for Linux: the window (Qt 6) around the same p1401 engine the Windows app runs.

Each module mirrors one file of windows/App: durable.py (DurableReport.cs), scanevidence.py (ScanEvidence.cs),
engine.py (Engine.cs), mainwindow.py (MainForm.cs), theme.py (Theme.cs), verify.py (PackageVerification.cs). What
Windows does through WMI, UAC and drive letters is done here through lsblk, polkit and mount points (privileged.py,
disks.py)."""
VERSION = "1.1.0"
