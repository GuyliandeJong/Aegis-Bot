"""Alerts when the backup script has not succeeded recently."""

from core.modules import Module


MODULE = Module(
    name="backup",
    description="Alerts when the backup script has not succeeded recently.",
    required=(
        "BACKUP_PATHS",
    ),
    monitors=(
        "modules.backup.backup_monitor:BackupMonitor",
    ),
)
