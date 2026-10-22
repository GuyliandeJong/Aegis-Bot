"""Wake and sleep a computer through GPTWOL."""

from core.modules import Module


MODULE = Module(
    name="wol",
    description="Wake and sleep a computer through GPTWOL.",
    required=(
        "GPTWOL_HOST",
        "GPTWOL_TARGET_IP",
        "GPTWOL_TARGET_MAC",
    ),
)
