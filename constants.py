# constants.py

# --- Mode Constants ---
MODE_DICTATION = "Dictation"
MODE_COMMAND = "Command"

# --- Input Mappings ---
# Used for config interpretation and command execution

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode # Import KeyCode

PYNPUT_BUTTON_MAP = {
    "left": mouse.Button.left,
    "right": mouse.Button.right,
    "middle": mouse.Button.middle,
    "x1": mouse.Button.x1,
    "x2": mouse.Button.x2,
    None: None
}

PYNPUT_MODIFIER_MAP = {
    "shift": keyboard.Key.shift,
    "shift_l": keyboard.Key.shift_l,
    "shift_r": keyboard.Key.shift_r,
    "ctrl": keyboard.Key.ctrl,
    "ctrl_l": keyboard.Key.ctrl_l,
    "ctrl_r": keyboard.Key.ctrl_r,
    "alt": keyboard.Key.alt,
    "alt_l": keyboard.Key.alt_l,
    "alt_r": keyboard.Key.alt_r,
    "cmd": keyboard.Key.cmd, # For Mac compatibility if needed later
    None: None
}

# Pynput Key Name to Key Object Mapping
# Used by KeyboardSimulator and CommandProcessor
PYNPUT_KEY_MAP = {
    # Special Keys
    "enter": Key.enter,
    "esc": Key.esc,
    "escape": Key.esc,
    "tab": Key.tab,
    "space": Key.space,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "del": Key.delete,
    "insert": Key.insert,
    "home": Key.home,
    "end": Key.end,
    "pageup": Key.page_up,
    "pagedown": Key.page_down,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "capslock": Key.caps_lock,
    "numlock": Key.num_lock,
    "scrolllock": Key.scroll_lock,
    "printscreen": Key.print_screen,
    # Modifiers
    "shift": Key.shift,
    "shift_l": Key.shift_l,
    "shift_r": Key.shift_r,
    "ctrl": Key.ctrl,
    "control": Key.ctrl,
    "ctrl_l": Key.ctrl_l,
    "ctrl_r": Key.ctrl_r,
    "alt": Key.alt,
    "alt_l": Key.alt_l,
    "alt_r": Key.alt_r,
    "cmd": Key.cmd,
    "command": Key.cmd,
    "win": Key.cmd,
    "windows": Key.cmd,
    # Function Keys
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    "f13": Key.f13, "f14": Key.f14, "f15": Key.f15, "f16": Key.f16,
    "f17": Key.f17, "f18": Key.f18, "f19": Key.f19, "f20": Key.f20,
    # Symbols (spoken forms handled by i18n/replacements now)
    ".": ".", ",": ",", "?": "?", "!": "!", ":": ":", ";": ";", "'": "'", '"': '"',
    "/": "/", "\\": "\\", "|": "|", "-": "-", "_": "_", "+": "+", "=": "=", "*": "*",
    "&": "&", "@": "@", "#": "#", "$": "$", "%": "%", "^": "^", "~": "~", "`": "`",
    "(": "(", ")": ")", "[": "[", "]": "]", "{": "{", "}": "}", "<": "<", ">": ">",
}

# --- Language Definitions ---
# Used by systray_ui, mic_ui_manager, vibe_app

# Full list for selection menus
ALL_LANGUAGES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "fr-FR": "French",
    "es-ES": "Spanish",
    "de-DE": "German",
    "it-IT": "Italian",
    "pt-PT": "Portuguese",
    "pt-BR": "Portuguese (Brazil)",
    "ru-RU": "Russian",
    "zh": "Chinese (Mandarin)",
    "ko-KR": "Korean",
    "ja-JP": "Japanese",
    "hi-IN": "Hindi",
    "ar": "Arabic",
    "nl-NL": "Dutch",
    # Add more as needed
}

# Subset for preferred/quick access (optional, currently defined in vibe_app.py, could move here)
# PREFERRED_SOURCE_LANGUAGES = {
#     "en-US": "English (US)",
#     "fr-FR": "French",
# }
# PREFERRED_TARGET_LANGUAGES = {
#     None: "Aucune",
#     "en-US": "English (US)",
#     "fr-FR": "French",
# }

# --- NEW: Native Language Names ---
# Store the name of the language *in that language*.
# Fallback will be the English name from ALL_LANGUAGES if native is missing.
NATIVE_LANGUAGE_NAMES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "fr-FR": "Français",
    "es-ES": "Español",
    "de-DE": "Deutsch",
    "it-IT": "Italiano",
    "pt-PT": "Português",
    "pt-BR": "Português (Brasil)",
    "ru-RU": "Русский",
    "zh": "中文 (普通话)", # Simplified Chinese (Mandarin)
    "ko-KR": "한국어",
    "ja-JP": "Japanese", # No simple native name, using English
    "hi-IN": "हिन्दी",
    "ar": "العربية",
    "nl-NL": "Nederlands",
    # Add more as needed
}

# Target language list including the None option
ALL_LANGUAGES_TARGET = {None: "Aucune"} # Start with None (translated via i18n if needed)
ALL_LANGUAGES_TARGET.update(ALL_LANGUAGES)

# --- Mode Definitions ---
# Used by systray_ui, mic_ui_manager, vibe_app
AVAILABLE_MODES = {
    MODE_DICTATION: "Dictation Mode", # Use MODE_DICTATION constant defined above
    MODE_COMMAND: "Command Mode",
} 