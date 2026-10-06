# ------------------------------------------------------------
# Aegis-Bot visual theme
#
# This file contains the centralized visual settings used
# throughout the bot.
#
# Keeping colors and emojis in one place ensures that commands,
# monitoring alerts, and future features all use the same
# visual language.
# ------------------------------------------------------------


# ------------------------------------------------------------
# Embed colors
# ------------------------------------------------------------

# Main Aegis-Bot color.
# Used for normal commands and general information.
PRIMARY_COLOR = 0x00A8E8

# Healthy / recovered state.
SUCCESS_COLOR = 0x2ECC71

# Warning state.
WARNING_COLOR = 0xF1C40F

# Critical / error state.
ERROR_COLOR = 0xE74C3C

# Informational state.
INFO_COLOR = 0x00D4FF

# Neutral state.
NEUTRAL_COLOR = 0x7F8C8D


# ------------------------------------------------------------
# Bot identity
# ------------------------------------------------------------

# Display name used in Discord embed footers and other
# user-facing locations.
BOT_NAME = "Aegis-Bot"


# ------------------------------------------------------------
# Status emojis
#
# These provide a quick visual indication of the severity
# or type of a message.
# ------------------------------------------------------------

# Successful operation or recovered state.
SUCCESS_EMOJI = "✅"

# Warning condition requiring attention.
WARNING_EMOJI = "⚠️"

# Error or critical condition.
ERROR_EMOJI = "❌"

# General informational message.
INFO_EMOJI = "ℹ️"


# ------------------------------------------------------------
# System / monitoring emojis
#
# These identify the type of system or service represented
# by a command or notification.
# ------------------------------------------------------------

# General server/system information.
SERVER_EMOJI = "🖥️"

# Network and WAN-related information.
NETWORK_EMOJI = "🌐"

# Storage and filesystem information.
STORAGE_EMOJI = "💾"

# CPU information.
CPU_EMOJI = "⚙️"

# Memory information.
MEMORY_EMOJI = "🧠"

# Temperature information.
TEMPERATURE_EMOJI = "🌡️"

# ------------------------------------------------------------
# Usage bar
# ------------------------------------------------------------

# Blocks used by percentage-based usage bars.
# These are deliberately separate from the status emojis above
# because they represent a visual progress bar rather than a
# status notification.

USAGE_HEALTHY_BLOCK = "🟩"
USAGE_WARNING_BLOCK = "🟨"
USAGE_CRITICAL_BLOCK = "🟥"
USAGE_EMPTY_BLOCK = "⬜"