"""Mirrors every alert to a Fluxer channel through a webhook."""

from core.modules import Module


MODULE = Module(
    name="fluxer",
    description="Mirrors every alert to a Fluxer channel through a webhook.",
    required=(
        "FLUXER_WEBHOOK_URL",
    ),
    setup="modules.fluxer.fluxer_webhook:install",
)
