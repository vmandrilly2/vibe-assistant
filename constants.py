# constants.py

from pynput.keyboard import Key

# Define application-wide constants here

# Example:
APP_NAME = "Vibe Assistant (Refactored)"
VERSION = "2.0.0"

# --- State Keys (Examples - Align with GVM usage) ---
# General State
STATE_APP_STATUS = "app.status" # e.g., "initializing", "running", "stopping"
STATE_ERROR_MESSAGE = "app.error_message"
STATE_APP_CURRENT_STT_SESSION_ID = "app.current_stt_session_id" # Added for STTManager

# Application Modes
MODE_DICTATION = "Dictation"
MODE_COMMAND = "Command"

# Config State (prefix defined in GVM, e.g., "config.")
CONFIG_GENERAL_PREFIX = "config.general"
CONFIG_MODULES_PREFIX = "config.modules"
CONFIG_DEEPGRAM_PREFIX = "config.deepgram" # Added for Deepgram settings
CONFIG_TRANSLATION_ENABLED = "config.translation.enabled"
# ... other config keys ...

# Input State
STATE_INPUT_DICTATION_KEY_PRESSED = "input.dictation_key_pressed"

# Audio State
STATE_AUDIO_BUFFER_READY = "audio.buffer_ready" # Example, if buffer signals readiness
STATE_AUDIO_STATUS = "audio.status" # e.g., "recording", "idle", "processing"
STATE_AUDIO_CURRENT_CHUNKS = "audio.current_chunks" # List or Queue of audio bytes
STATE_AUDIO_LATEST_CHUNK_TIMESTAMP = "audio.latest_chunk_timestamp" # Added

# STT State
STATE_STT_SESSION_STATUS_TEMPLATE = "stt.session.{session_id}.status" # e.g., "connecting", "connected", "disconnected", "error"

# Session State
STATE_SESSION_PREFIX = "sessions"
STATE_SESSION_INTERIM_TRANSCRIPT_TEMPLATE = "sessions.{session_id}.interim_transcript"
STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE = "sessions.{session_id}.final_transcript_segment"
STATE_SESSION_FINAL_TRANSCRIPT_FULL_TEMPLATE = "sessions.{session_id}.final_transcript_full"
STATE_SESSION_RECOGNIZED_ACTIONS_TEMPLATE = "sessions.{session_id}.recognized_actions" # List of detected action strings
STATE_SESSION_HISTORY_TEMPLATE = "sessions.{session_id}.history" # List of typed words/segments for deletion logic

# Output State
STATE_OUTPUT_TYPING_QUEUE = "output.typing_queue" # List/Queue of strings/actions to type
STATE_OUTPUT_ACTION_QUEUE = "output.action_queue" # List/Queue of specific actions (might merge with typing queue)

# UI State
STATE_UI_CONFIRMATION_REQUEST = "ui.confirmation.request" # Set by GVM to trigger UI
STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS = "ui.confirmation.confirmed_actions" # Set by Action UI on hover/click
STATE_UI_AI_RESPONSE_DISPLAY = "ui.ai_response_display" # For showing AI query results
STATE_UI_MIC_MODE = "ui.mic.mode" # Added: e.g., "on", "off", "muted"
STATE_UI_SESSION_DISPLAY_DATA = "ui.session_monitor.data" # Added: Data for SessionMonitorUI
STATE_UI_INTERIM_TEXT = "ui.interim_text.value" # Added

# --- Other Constants ---
# Pynput Key Mapping (Example - needs refinement based on actual keys used)
# Map common string names to pynput Key objects/codes
PYNPUT_KEY_MAP = {
    'enter': Key.enter,
    'tab': Key.tab,
    'space': Key.space,
    'backspace': Key.backspace,
    'delete': Key.delete,
    'esc': Key.esc,
    'up': Key.up,
    'down': Key.down,
    'left': Key.left,
    'right': Key.right,
    # Add function keys, modifiers (shift, ctrl, alt, cmd/win), etc. as needed
    # 'f1': Key.f1,
    # 'ctrl': Key.ctrl,
    # 'alt': Key.alt,
    # 'shift': Key.shift,
}

# Example: Audio settings
AUDIO_CHUNK_SIZE = 2048
AUDIO_SAMPLE_RATE = 16000
AUDIO_SAMPLE_WIDTH = 2 # Bytes per sample (16-bit)
AUDIO_CHANNELS = 1

# Deepgram settings (placeholders, should ideally come from config or secure storage)
# DEEPGRAM_API_KEY = "YOUR_DEEPGRAM_API_KEY"

# OpenAI settings
# OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"

# Add more constants as needed 