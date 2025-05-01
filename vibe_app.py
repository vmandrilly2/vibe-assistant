# vibe_app.py

import asyncio
import os
import threading
import logging
import time
from dotenv import load_dotenv
import queue # Import queue for thread-safe communication
import tkinter as tk # Import tkinter for the tooltip GUI
import pyautogui # Import pyautogui to get mouse position
import json # Import json module
import sys # Import sys for exiting on critical config error
import numpy as np # Import numpy
import pyaudio     # Import PyAudio
from openai import OpenAI, AsyncOpenAI # Use AsyncOpenAI for non-blocking calls

# --- Systray UI Import ---
# We need threading anyway, so let's use it for the systray
# Import the run function and the reload event
import systray_ui

# --- Audio Buffer Import ---
from audio_buffer import BufferedAudioInput

# --- Status Indicator Import (NEW) ---
from status_indicator import StatusIndicatorManager, DEFAULT_MODES # Import DEFAULT_MODES

# --- Internationalization (i18n) Import >
import i18n
from i18n import load_translations, _ # Import the main translation function

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode # Import KeyCode
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone, # Import Microphone class
)

# --- Configuration Loading ---
CONFIG_FILE = "config.json"
LOG_DIR = "logs" # Define log directory
DEFAULT_CONFIG = {
  "general": {
    "min_duration_sec": 0.5,
    "selected_language": "en-US",
    "target_language": None,
    "openai_model": "gpt-4.1-nano",
    "active_mode": "Dictation", # Added default active mode
    "recent_source_languages": [], # Added for tracking
    "recent_target_languages": []  # Added for tracking
  },
  "triggers": {
    "dictation_button": "middle",
    "command_button": None,
    "command_modifier": None
  },
  "tooltip": {
    "alpha": 0.85,
    "bg_color": "lightyellow",
    "fg_color": "black",
    "font_family": "Arial",
    "font_size": 10
  }
}

# --- Mode Constants ---
MODE_DICTATION = "Dictation"
MODE_KEYBOARD = "Keyboard"
# Add MODE_COMMAND = "Command" later if reimplemented

# Define available modes and their display names (matches status_indicator)
# This could be moved to a shared config/constants file later
AVAILABLE_MODES = DEFAULT_MODES

# --- Global Configurable Variables ---
# These will be updated by apply_config()
DICTATION_TRIGGER_BUTTON = None
COMMAND_TRIGGER_BUTTON = None
COMMAND_MODIFIER_KEY_STR = None
COMMAND_MODIFIER_KEY = None
MIN_DURATION_SEC = 0.5
SELECTED_LANGUAGE = "en-US"
TARGET_LANGUAGE = None
OPENAI_MODEL = "gpt-4.1-nano"
TOOLTIP_ALPHA = 0.85
TOOLTIP_BG = "lightyellow"
TOOLTIP_FG = "black"
TOOLTIP_FONT_FAMILY = "Arial"
TOOLTIP_FONT_SIZE = 10
ACTIVE_MODE = MODE_DICTATION # Initialize with default

# --- NEW: State Variables for Dictation Flow ---
last_interim_transcript = "" # Store the most recent interim result
final_processed_this_session = False # Flag to ensure final processing happens only once

# --- Pynput Mappings ---
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

# --- NEW: Pynput Key Name to Key Object Mapping ---
# Add common keys here. Needs careful mapping.
# Keys without a simple name (like ';') might need KeyCode.from_char(':')
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
    # Modifiers (ensure consistency with PYNPUT_MODIFIER_MAP keys if possible)
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
    "cmd": Key.cmd, # Mac command key
    "command": Key.cmd,
    "win": Key.cmd, # Windows key often maps to cmd
    "windows": Key.cmd,
    # Function Keys
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    "f13": Key.f13, "f14": Key.f14, "f15": Key.f15, "f16": Key.f16,
    "f17": Key.f17, "f18": Key.f18, "f19": Key.f19, "f20": Key.f20,
    # Alphanumeric and Symbols (handle single chars directly later)
    # Simple symbols that might be spoken
    "dot": ".",
    "period": ".",
    "comma": ",",
    "question": "?",
    "exclamation": "!",
    "colon": ":",
    "semicolon": ";",
    "quote": "'",
    "doublequote": '"',
    "slash": "/",
    "backslash": "\\",
    "pipe": "|",
    "dash": "-",
    "hyphen": "-",
    "underscore": "_",
    "plus": "+",
    "equal": "=",
    "asterisk": "*",
    "ampersand": "&",
    "at": "@",
    "hash": "#",
    "dollar": "$",
    "percent": "%",
    "caret": "^",
    "tilde": "~",
    "backtick": "`",
    "openparen": "(",
    "closeparen": ")",
    "openbracket": "[",
    "closebracket": "]",
    "openbrace": "{",
    "closebrace": "}",
    "less": "<",
    "greater": ">",
}

def load_config():
    """Loads configuration from JSON file, creates default if not found."""
    if not os.path.exists(CONFIG_FILE):
        logging.warning(f"{CONFIG_FILE} not found. Creating default config.")
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            return DEFAULT_CONFIG
        except IOError as e:
            logging.error(f"Unable to create default config file {CONFIG_FILE}: {e}")
            # Return default anyway, but log the error
            return DEFAULT_CONFIG
    else:
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded_config = json.load(f)
                # --- Merge with defaults for missing keys/sections --- >
                for section, defaults in DEFAULT_CONFIG.items():
                    if section not in loaded_config:
                        loaded_config[section] = defaults
                    elif isinstance(defaults, dict):
                        for key, default_value in defaults.items():
                            if key not in loaded_config[section]:
                                loaded_config[section][key] = default_value
                # Ensure recent lists exist even if loading an old config
                if "recent_source_languages" not in loaded_config.get("general", {}):
                    loaded_config.setdefault("general", {})["recent_source_languages"] = []
                if "recent_target_languages" not in loaded_config.get("general", {}):
                     loaded_config.setdefault("general", {})["recent_target_languages"] = []

                logging.info(f"Loaded and merged configuration from {CONFIG_FILE}")
                return loaded_config
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding {CONFIG_FILE}: {e}. Using default config.")
            return DEFAULT_CONFIG
        except IOError as e:
            logging.error(f"Unable to read config file {CONFIG_FILE}: {e}. Using default config.")
            return DEFAULT_CONFIG
        except Exception as e:
             logging.error(f"Unexpected error loading config: {e}. Using default config.")
             return DEFAULT_CONFIG

config = load_config()

# --- PyAudio Constants --- (Define globally for AudioMonitor)
MONITOR_CHUNK_SIZE = 1024
MONITOR_FORMAT = pyaudio.paInt16
MONITOR_CHANNELS = 1
MONITOR_RATE = 16000
MAX_RMS = 5000 # Adjust based on microphone sensitivity

def apply_config(cfg):
    """Applies the loaded configuration to the global variables."""
    global DICTATION_TRIGGER_BUTTON, COMMAND_TRIGGER_BUTTON, COMMAND_MODIFIER_KEY_STR
    global COMMAND_MODIFIER_KEY, MIN_DURATION_SEC, SELECTED_LANGUAGE
    global TOOLTIP_ALPHA, TOOLTIP_BG, TOOLTIP_FG, TOOLTIP_FONT_FAMILY, TOOLTIP_FONT_SIZE
    global TARGET_LANGUAGE, OPENAI_MODEL, ACTIVE_MODE # Add ACTIVE_MODE

    logging.info("Applying configuration...")
    old_source_lang = SELECTED_LANGUAGE # Store old language
    try:
        # Triggers
        triggers_cfg = cfg.get("triggers", {})
        DICTATION_TRIGGER_BUTTON = PYNPUT_BUTTON_MAP.get(triggers_cfg.get("dictation_button", "middle"))
        COMMAND_TRIGGER_BUTTON = PYNPUT_BUTTON_MAP.get(triggers_cfg.get("command_button"))
        COMMAND_MODIFIER_KEY_STR = triggers_cfg.get("command_modifier")
        COMMAND_MODIFIER_KEY = PYNPUT_MODIFIER_MAP.get(COMMAND_MODIFIER_KEY_STR)

        # General
        general_cfg = cfg.get("general", {})
        MIN_DURATION_SEC = float(general_cfg.get("min_duration_sec", 0.5))
        SELECTED_LANGUAGE = str(general_cfg.get("selected_language", "en-US"))
        TARGET_LANGUAGE = general_cfg.get("target_language")
        OPENAI_MODEL = str(general_cfg.get("openai_model", "gpt-4.1-nano"))
        # Apply active mode, defaulting to Dictation if not found or invalid
        loaded_mode = general_cfg.get("active_mode", MODE_DICTATION)
        ACTIVE_MODE = loaded_mode if loaded_mode in AVAILABLE_MODES else MODE_DICTATION

        # Tooltip
        TOOLTIP_ALPHA = float(cfg.get("tooltip", {}).get("alpha", 0.85))
        TOOLTIP_BG = str(cfg.get("tooltip", {}).get("bg_color", "lightyellow"))
        TOOLTIP_FG = str(cfg.get("tooltip", {}).get("fg_color", "black"))
        TOOLTIP_FONT_FAMILY = str(cfg.get("tooltip", {}).get("font_family", "Arial"))
        TOOLTIP_FONT_SIZE = int(cfg.get("tooltip", {}).get("font_size", 10))

        # --- Reload Translations if Source Language Changed --- >
        if SELECTED_LANGUAGE != old_source_lang:
            logging.info(f"Source language changed from {old_source_lang} to {SELECTED_LANGUAGE}. Reloading translations.")
            # --- Add debug logging --- >
            old_i18n_lang = i18n.get_current_language()
            load_translations(SELECTED_LANGUAGE)
            new_i18n_lang = i18n.get_current_language()
            logging.debug(f"apply_config: i18n language after load_translations: {new_i18n_lang} (was {old_i18n_lang})")
            sample_key = 'systray.menu.exit' # Use a known key
            sample_translation = _(sample_key)
            logging.debug(f"apply_config: Sample translation for '{sample_key}' in {new_i18n_lang}: '{sample_translation}'")
            # --- End debug logging --- >
            # TODO: Potentially signal StatusIndicator/Systray to update display names if needed?
            # The systray rebuilds its menu anyway. StatusIndicator redraws on state change.

        target_lang_str = TARGET_LANGUAGE if TARGET_LANGUAGE else "None"
        logging.info(f"Config applied: Mode={ACTIVE_MODE}, SourceLang={SELECTED_LANGUAGE}, TargetLang={target_lang_str}, Model={OPENAI_MODEL}, Dictation={triggers_cfg.get('dictation_button', 'middle')}, ...")

    except (ValueError, TypeError, KeyError) as e:
        logging.error(f"Error applying configuration: {e}. Some settings may not be updated correctly.")
        # Keep existing values or fall back to defaults? For now, just log.

# --- Initial Configuration Application --- (After defining apply_config)
load_dotenv() # Load environment variables from .env file (still used for API key)
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # --- Load OpenAI Key ---
if not DEEPGRAM_API_KEY:
    logging.critical("DEEPGRAM_API_KEY not found in environment variables or .env file. Exiting.")
    sys.exit(1)
# --- Check for OpenAI Key (warn if missing, needed for translation) ---
if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY not found in environment variables or .env file. Translation feature will be disabled.")
    # Don't exit, allow other modes to work

# Apply the initially loaded config
apply_config(config)
# --- Load Initial Translations --- >
load_translations(SELECTED_LANGUAGE)
logging.info(f"Initial translations loaded for language: {i18n.get_current_language()}")

# --- Initialize OpenAI Client ---
# Initialize AsyncOpenAI client if key exists
openai_client = None
if OPENAI_API_KEY:
    try:
        # Use the correct model name from config if needed during init, though usually not required here
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize OpenAI client: {e}")
else:
    logging.warning("OpenAI client not initialized due to missing API key. Translation disabled.")

logging.info(f"Using Source Language: {SELECTED_LANGUAGE}")
logging.info(f"Initial Active Mode: {ACTIVE_MODE}") # Log initial mode
# --- Log Target Language ---
if TARGET_LANGUAGE:
    logging.info(f"Translation Enabled: Target Language = {TARGET_LANGUAGE}, Model = {OPENAI_MODEL}")
else:
    logging.info("Translation Disabled (Target Language is None)")
logging.info(f"Dictation Trigger: {config.get('triggers', {}).get('dictation_button', 'middle')}")
if COMMAND_TRIGGER_BUTTON:
    mod_str = f" + {COMMAND_MODIFIER_KEY_STR}" if COMMAND_MODIFIER_KEY else ""
    logging.info(f"Command Trigger: {config.get('triggers', {}).get('command_button')}{mod_str}")
else:
    logging.info("Command Trigger: Disabled")

# --- Logging Setup ---
# Include milliseconds in timestamp
log_formatter = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)s: %(message)s', datefmt='%H:%M:%S')
log_level = logging.DEBUG

# Remove all handlers before adding new ones to avoid duplicates
def clear_log_handlers():
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
clear_log_handlers()

# Console Handler
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

# File Handler (logs to vibe_app.log in the same directory)
try:
    # --- MODIFICATION: Add encoding='utf-8' ---
    file_handler = logging.FileHandler("vibe_app.log", mode='w', encoding='utf-8')
    file_handler.setFormatter(log_formatter)
except Exception as e:
    print(f"Error setting up file logging: {e}")
    file_handler = None

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(log_level)
root_logger.addHandler(stream_handler)
if file_handler:
    root_logger.addHandler(file_handler)
    logging.info("File logging configured to vibe_app.log")

# --- Modifier Key Logging Buffer ---
modifier_log_buffer = []
modifier_log_last_time = 0
MODIFIER_LOG_FLUSH_INTERVAL = 0.5  # seconds

def flush_modifier_log(force=False):
    global modifier_log_buffer, modifier_log_last_time
    if modifier_log_buffer and (force or (time.time() - modifier_log_last_time > MODIFIER_LOG_FLUSH_INTERVAL)):
        logging.debug(' '.join(modifier_log_buffer))
        modifier_log_buffer = []
        modifier_log_last_time = time.time()

# --- Global State ---
is_command_active = threading.Event() # Keep for potential command mode later
transcription_active_event = threading.Event() # True if any trigger is active
tooltip_queue = queue.Queue()
status_queue = queue.Queue()
modifier_keys_pressed = set()
ui_action_queue = queue.Queue()
ui_interaction_cancelled = False # Flag specifically for UI hover interactions
initial_activation_pos = None

# --- Keyboard Controller (for typing simulation) ---
kb_controller = keyboard.Controller()

# --- State for Dictation Typing Simulation ---
last_simulated_text = "" # Store the transcript corresponding to the last simulation action
typed_word_history = [] # Store history of typed words
final_source_text = "" # Store final source text from dictation *before* potential translation

# --- State for Command Mode ---
current_command_transcript = "" # Store the transcript for command mode
last_command_executed = None # For potential undo feature

# --- State for Keyboard Input Mode ---
final_keyboard_input_text = "" # Store final text from Keyboard Input Mode

# --- Tooltip Manager Class ---
class TooltipManager:
    """Manages a simple Tkinter tooltip window in a separate thread."""
    def __init__(self, q):
        self.queue = q
        self.root = None
        self.label = None
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event() # Signal when Tkinter root is ready
        # --- Add config references ---
        self._apply_tooltip_config()

    def _apply_tooltip_config(self):
        """Applies tooltip config to internal variables."""
        # Use the global config variables directly
        self.alpha = TOOLTIP_ALPHA
        self.bg_color = TOOLTIP_BG
        self.fg_color = TOOLTIP_FG
        self.font_family = TOOLTIP_FONT_FAMILY
        self.font_size = TOOLTIP_FONT_SIZE
        logging.debug(f"Tooltip config applied: Alpha={self.alpha}, BG={self.bg_color}, FG={self.fg_color}")

    def reload_config(self):
        """Called when main config reloads to update tooltip appearance."""
        self._apply_tooltip_config()
        # If the window exists, update its attributes
        if self.root and self.label:
            try:
                self.root.attributes('-alpha', self.alpha)
                self.label.config(bg=self.bg_color, fg=self.fg_color,
                                  font=(self.font_family, self.font_size))
                logging.info("Tooltip appearance updated from reloaded config.")
            except tk.TclError as e:
                logging.warning(f"Could not update tooltip appearance on reload: {e}")

    def start(self):
        self.thread.start()
        # Wait briefly for Tkinter to initialize to prevent race conditions on early commands
        self._tk_ready.wait(timeout=2.0)
        if not self._tk_ready.is_set():
            logging.warning("Tooltip Tkinter thread did not become ready in time.")

    def stop(self):
        """Signals the Tkinter thread to stop and cleanup."""
        logging.debug("Stop requested for TooltipManager.")
        self._stop_event.set()
        # Put a stop command on the queue to ensure the _check_queue loop wakes up
        # Use put_nowait as the thread might be shutting down anyway
        try:
            self.queue.put_nowait(("stop", None))
        except queue.Full:
            logging.warning("Tooltip queue full when sending stop command.")
        # Do NOT join the thread here - let the daemon thread exit naturally
        # or let the Tkinter thread handle its own cleanup.

    def _run_tkinter(self):
        logging.info("Tooltip thread started.")
        try:
            self.root = tk.Tk()
            self.root.withdraw() # Start hidden
            self.root.overrideredirect(True) # No border, title bar, etc.
            self.root.wm_attributes("-topmost", True) # Keep on top
            # Apply config settings during creation
            self.root.attributes('-alpha', self.alpha)
            self.label = tk.Label(self.root, text="", bg=self.bg_color, fg=self.fg_color,
                                  font=(self.font_family, self.font_size),
                                  justify=tk.LEFT, padx=5, pady=2)
            self.label.pack()

            self._tk_ready.set() # Signal that Tkinter objects are created
            logging.debug("Tooltip Tkinter objects created and ready.")

            # Start the queue checking loop using root.after
            self._check_queue()

            # Run the Tkinter main event loop.
            # This will block until the window is destroyed or tk.quit() is called.
            logging.debug("Starting Tkinter mainloop...")
            self.root.mainloop()
            logging.debug("Tkinter mainloop finished.")

        except Exception as e:
            logging.error(f"Error during Tkinter mainloop/setup in tooltip thread: {e}", exc_info=True)
            self._tk_ready.set() # Set ready even on error to prevent blocking start()
        finally:
            # Cleanup happens automatically when mainloop exits after root is destroyed
            logging.info("Tooltip thread finished.")
            # Ensure stop event is set if mainloop exited unexpectedly
            self._stop_event.set()

    def _check_queue(self):
        """Processes messages from the queue using root.after."""
        # Check stop event first
        if self._stop_event.is_set():
            logging.debug("Stop event set, initiating Tkinter cleanup.")
            self._cleanup_tk()
            return # Stop rescheduling

        try:
            # Process all available messages in the queue
            while not self.queue.empty():
                command, data = self.queue.get_nowait()
                if command == "update":
                    text, x, y = data
                    self._update_tooltip(text, x, y)
                elif command == "show":
                    self._show_tooltip()
                elif command == "hide":
                    self._hide_tooltip()
                elif command == "stop":
                    # This command ensures we wake up and check the _stop_event
                    logging.debug("Received stop command in queue.")
                    self._stop_event.set() # Ensure it's set
                    # We don't break the loop here, let the check at the start handle it
                    # This ensures cleanup happens before returning

        except queue.Empty:
            pass # No messages, just reschedule
        except tk.TclError as e:
            logging.warning(f"Tkinter error during queue processing: {e}. Stopping tooltip.")
            self._stop_event.set()
            self._cleanup_tk()
            return # Stop rescheduling
        except Exception as e:
            logging.error(f"Error processing tooltip queue: {e}", exc_info=True)
            # Consider stopping if there's a persistent error
            # self._stop_event.set()
            # self._cleanup_tk()
            # return

        # Reschedule the check if not stopping
        if not self._stop_event.is_set() and self.root:
             try:
                 self.root.after(50, self._check_queue)
             except tk.TclError:
                 logging.warning("Tooltip root destroyed before rescheduling queue check.")
                 self._stop_event.set()
                 self._cleanup_tk() # Attempt cleanup just in case

    def _cleanup_tk(self):
        """Safely destroys the Tkinter window from the Tkinter thread."""
        logging.debug("Executing _cleanup_tk.")
        if self.root:
            try:
                logging.debug("Destroying tooltip root window...")
                self.root.destroy()
                logging.info("Tkinter root window destroyed successfully.")
                self.root = None # Prevent further access
            except tk.TclError as e:
                logging.warning(f"Error destroying Tkinter root (already destroyed?): {e}")
            except Exception as e:
                logging.error(f"Unexpected error during Tkinter destroy: {e}", exc_info=True)

    def _update_tooltip(self, text, x, y):
        # Check if root exists and stop event isn't set
        if self.root and self.label and not self._stop_event.is_set():
            try:
                self.label.config(text=text)
                offset_x = 15
                offset_y = -30
                self.root.geometry(f"+{x + offset_x}+{y + offset_y}")
            except tk.TclError as e:
                 logging.warning(f"Failed to update tooltip (window likely closed): {e}")
                 self._stop_event.set() # Stop if window is broken

    def _show_tooltip(self):
        if self.root and not self._stop_event.is_set():
            try:
                self.root.deiconify() # Show the window
            except tk.TclError as e:
                 logging.warning(f"Failed to show tooltip (window likely closed): {e}")
                 self._stop_event.set()

    def _hide_tooltip(self):
        # Allow hiding even if stop_event is set, for cleanup purposes? Maybe not.
        # Let's only hide if not stopping.
        if self.root and not self._stop_event.is_set():
            try:
                self.root.withdraw() # Hide the window
            except tk.TclError as e:
                 logging.warning(f"Failed to hide tooltip (window likely closed): {e}")
                 self._stop_event.set()


# --- Placeholder/Handler Functions ---
def simulate_typing(text):
    """Simulates typing the given text."""
    logging.info(f"Simulating type: '{text}'")
    kb_controller.type(text)

def simulate_backspace(count):
    """Simulates pressing backspace multiple times."""
    logging.info(f"Simulating {count} backspaces")
    for _ in range(count):
        kb_controller.press(keyboard.Key.backspace)
        kb_controller.release(keyboard.Key.backspace)
        time.sleep(0.01) # Small delay between key presses

def simulate_key_press_release(key_obj):
    """Simulates pressing and releasing a single key."""
    try:
        kb_controller.press(key_obj)
        kb_controller.release(key_obj)
        logging.debug(f"Simulated press/release: {key_obj}")
        time.sleep(0.02) # Small delay between keys
    except Exception as e:
        logging.error(f"Failed to simulate key {key_obj}: {e}")

def simulate_key_combination(keys):
    """Simulates pressing modifier keys, then a main key, then releasing all."""
    if not keys: return
    modifiers = []
    main_key = None
    try:
        # Separate modifiers from the main key
        for key_obj in keys:
            # Check if key is a modifier using pynput's attributes
            if hasattr(key_obj, 'is_modifier') and key_obj.is_modifier:
                 modifiers.append(key_obj)
            elif main_key is None: # First non-modifier is the main key
                main_key = key_obj
            else:
                 logging.warning(f"Multiple non-modifier keys found in combination: {keys}. Using first: {main_key}")

        if not main_key: # Maybe it was just modifiers? (e.g., "press control") - less common
            if modifiers:
                logging.info(f"Simulating modifier press/release only: {modifiers}")
                for mod in modifiers: kb_controller.press(mod)
                time.sleep(0.05) # Hold briefly
                for mod in reversed(modifiers): kb_controller.release(mod)
            else:
                logging.warning("No main key or modifiers found in combination.")
            return

        # Press modifiers
        logging.info(f"Simulating combo: Modifiers={modifiers}, Key={main_key}")
        for mod in modifiers:
            kb_controller.press(mod)
        time.sleep(0.05) # Brief pause after modifiers

        # Press and release main key
        kb_controller.press(main_key)
        kb_controller.release(main_key)
        time.sleep(0.05) # Brief pause after main key

        # Release modifiers (in reverse order)
        for mod in reversed(modifiers):
            kb_controller.release(mod)

    except Exception as e:
        logging.error(f"Error simulating key combination {keys}: {e}")
        # Attempt to release any potentially stuck keys
        if main_key:
            try: kb_controller.release(main_key)
            except: pass
        for mod in reversed(modifiers):
            try: kb_controller.release(mod)
            except: pass

def handle_dictation_interim(transcript):
    """Handles interim dictation results by displaying them in a temporary tooltip."""
    global last_simulated_text # Keep variable for now, but don't use for typing
    if not transcript:
        return

    # Log the interim transcript
    logging.debug(f"Interim Received (for Tooltip): '{transcript}'")

    # --- Tooltip Update Logic ---
    try:
        # Get current mouse position
        x, y = pyautogui.position()
        # Send update command to the tooltip manager thread
        # Use put_nowait or handle Full exception if queue might fill up,
        # but for this use case, blocking put is likely fine.
        tooltip_queue.put(("update", (transcript, x, y)))
        tooltip_queue.put(("show", None)) # Ensure it's visible
    except pyautogui.FailSafeException:
         logging.warning("PyAutoGUI fail-safe triggered (mouse moved to corner?).")
    except Exception as e:
        logging.error(f"Error getting mouse position or updating tooltip: {e}")

    # --- REMOVED TYPING/BACKSPACE LOGIC ---
    # last_simulated_text = transcript # DO NOT UPDATE STATE HERE

def handle_dictation_final(final_transcript, history):
    """Handles the final dictation transcript segment based on history.
    Calculates target state, determines diff from current state, executes typing, updates history.
    Also hides the interim tooltip.

    Returns:
        tuple: (updated_history_list, final_text_string_typed)
               The final_text_string_typed includes the trailing space if added.
    """
    logging.debug(f"Handling final dictation segment: '{final_transcript}'")

    # --- Hide the interim tooltip ---
    try:
        tooltip_queue.put_nowait(("hide", None))
    except queue.Full:
        logging.warning("Tooltip queue full when trying to hide on final.")

    # --- Step A: Calculate Target Word List ---
    target_words = [entry['text'] for entry in history] # Start with existing words
    logging.debug(f"Initial target_words from history: {target_words}")

    original_words = final_transcript.split()
    punctuation_to_strip = '.,!?;:'

    # Process the new transcript segment against the target list
    for word in original_words:
        if not word: continue
        cleaned_word = word.rstrip(punctuation_to_strip).lower()

        if cleaned_word == "back":
            if target_words:
                removed = target_words.pop()
                logging.info(f"Processing 'back', removed '{removed}' from target_words.")
            else:
                logging.info(f"Processing 'back', but target_words already empty.")
        else:
            target_words.append(word) # Append original word with punctuation

    logging.debug(f"Final target_words after processing segment: {target_words}")

    # --- Step B: Calculate Target Text ---
    target_text = " ".join(target_words) + (' ' if target_words else '') # Add trailing space
    logging.debug(f"Calculated target_text (with space): '{target_text}'")

    # --- Step C: Calculate Current Text on Screen (Estimate from OLD history) ---
    current_text_estimate = " ".join([entry['text'] for entry in history]) + (' ' if history else '')
    logging.debug(f"Estimated current text (from old history, with space): '{current_text_estimate}'")

    # --- Step D: Calculate Diff ---
    common_prefix_len = 0
    min_len = min(len(current_text_estimate), len(target_text))
    while common_prefix_len < min_len and current_text_estimate[common_prefix_len] == target_text[common_prefix_len]:
        common_prefix_len += 1

    backspaces_needed = len(current_text_estimate) - common_prefix_len
    text_to_type = target_text[common_prefix_len:]

    logging.debug(f"Diff Calculation: Prefix={common_prefix_len}, Backspaces={backspaces_needed}, Type='{text_to_type}'")

    # --- Step E: Execute Typing Actions ---
    if backspaces_needed > 0:
        simulate_backspace(backspaces_needed)
    if text_to_type:
        simulate_typing(text_to_type)

    # --- Step F: Update History to Match Target State ---
    new_history = []
    if target_words:
        logging.debug(f"Rebuilding history with: {target_words}")
        for word in target_words:
            if word:
                length_with_space = len(word) + 1
                entry = {"text": word, "length_with_space": length_with_space}
                new_history.append(entry)
    else:
        logging.debug("History cleared as target_words is empty.")

    # Return the updated history AND the final text string *as typed* (including space)
    return new_history, target_text

def handle_command_interim(transcript):
    """Displays interim command transcript (e.g., in a UI)."""
    global current_command_transcript
    logging.info(f"Interim Command: '{transcript}'")
    current_command_transcript = transcript
    # TODO: Update command feedback UI
    pass

def handle_command_final(final_transcript):
    """Stores the final command transcript."""
    global current_command_transcript
    logging.info(f"Final Command Transcript: '{final_transcript}'")
    current_command_transcript = final_transcript
    # Final command execution happens on button release in main loop
    pass

def execute_command(command_text):
    """Interprets and executes the command."""
    global last_command_executed
    logging.info(f"Executing Command: '{command_text}'")
    last_command_executed = None # Reset undo state
    # TODO: Send to AI for interpretation
    # TODO: Map interpretation to action (keyboard sim / script exec)
    # Example: Simple mapping
    if "press enter" in command_text.lower():
        logging.info("Action: Simulating Enter key")
        kb_controller.press(keyboard.Key.enter)
        kb_controller.release(keyboard.Key.enter)
        last_command_executed = ("key_press", keyboard.Key.enter)
    # TODO: Execute action safely
    # TODO: Store action details in last_command_executed for undo
    pass

def undo_last_command():
    """Attempts to undo the last executed command."""
    logging.info(f"Attempting Undo for: {last_command_executed}")
    # TODO: Implement undo logic based on stored action
    pass

# --- UPDATED: Keyboard Input Final Handling ---
async def handle_keyboard_input_final(final_transcript):
    """Handles final transcript for Keyboard Input Mode: Sends to OpenAI, parses keys, simulates."""
    global final_keyboard_input_text, openai_client, OPENAI_MODEL

    logging.info(f"Final Keyboard Input Transcript: '{final_transcript}'")
    final_keyboard_input_text = final_transcript # Store the raw text

    if not final_keyboard_input_text:
        logging.warning("Keyboard Input: Empty transcript received.")
        return

    if not openai_client:
        logging.error("OpenAI client not available. Cannot process keyboard input.")
        return

    # --- Prepare OpenAI Request ---
    # Define valid keys for the prompt context - helps guide the LLM
    # Create a simplified list of key names for the prompt
    valid_key_names_str = ", ".join(list(PYNPUT_KEY_MAP.keys())[:40]) + ", etc. (standard alphanumeric keys also valid)" # Limit length

    system_prompt = f"""
You are an AI assistant that translates natural language commands into keyboard actions.
The user will provide text representing desired keyboard input.
Analyze the input and determine the sequence of keys to press.
Output *only* a JSON list of strings, where each string is either:
1. A special key name exactly as found in this list (lowercase): {valid_key_names_str}
2. A single character to be typed (e.g., "a", "b", "1", "$", "?").
3. A combination represented as a list within the list, e.g., ["ctrl", "c"] or ["shift", "a"].
Modifiers should always come first in combinations. Do NOT output phrases like "press", "type", "key".
Focus on interpreting common keyboard commands like "enter", "delete", "control c", "page down", "type hello", "shift 1".
If the user says "type" followed by text, represent each character as a string in the list. Example: "type abc" -> ["a", "b", "c"].
If the user says "press" followed by a key name, output that key name. Example: "press delete" -> ["delete"].
If the user asks for a combination, output the list. Example: "press control alt delete" -> [["ctrl", "alt", "delete"]].
Be precise. If unsure, return an empty list [].
"""
    user_prompt = f"User input: \"{final_keyboard_input_text}\""

    logging.info(f"Requesting keyboard key interpretation for: '{final_keyboard_input_text}'")

    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL, # Use the configured model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1, # Low temperature for precise interpretation
            response_format={"type": "json_object"}, # Request JSON output
            max_tokens=150 # Adjust as needed
        )

        response_content = response.choices[0].message.content
        logging.debug(f"OpenAI Keyboard Response Raw: {response_content}")

        # --- Parse the JSON response ---
        parsed_keys = []
        try:
            # The response should be a JSON object containing a key, e.g., {"keys": ["enter"]}
            response_data = json.loads(response_content)
            if isinstance(response_data, dict) and "keys" in response_data and isinstance(response_data["keys"], list):
                 parsed_keys = response_data["keys"]
                 logging.info(f"Parsed keys from OpenAI: {parsed_keys}")
            else:
                 logging.error(f"OpenAI response JSON structure incorrect: {response_content}")

        except json.JSONDecodeError:
            logging.error(f"Failed to decode OpenAI JSON response: {response_content}")
        except Exception as e:
            logging.error(f"Error parsing OpenAI response keys: {e}")

        # --- Simulate Key Presses ---
        if not parsed_keys:
            logging.warning("No valid keys parsed from OpenAI response.")
            return

        for item in parsed_keys:
            if isinstance(item, str): # Single key or character
                key_name_lower = item.lower()
                key_obj = PYNPUT_KEY_MAP.get(key_name_lower)
                if key_obj:
                    # It's a known special key or symbol mapped directly
                    if isinstance(key_obj, str): # It's a character mapped from a word (e.g., "dot" -> ".")
                         simulate_typing(key_obj)
                    else: # It's a pynput.keyboard.Key object
                        simulate_key_press_release(key_obj)
                elif len(item) == 1:
                    # Assume it's a single character to type
                    simulate_typing(item)
                else:
                    logging.warning(f"Unknown single key name: '{item}'")

            elif isinstance(item, list): # Key combination
                combo_keys = []
                valid_combo = True
                for key_name in item:
                    if isinstance(key_name, str):
                         key_name_lower = key_name.lower()
                         key_obj = PYNPUT_KEY_MAP.get(key_name_lower)
                         if key_obj:
                             if isinstance(key_obj, str) and len(key_obj) == 1: # Single char from map
                                 combo_keys.append(KeyCode.from_char(key_obj))
                             elif not isinstance(key_obj, str): # pynput Key object
                                 combo_keys.append(key_obj)
                             else: # Mapped to multi-char string? Invalid for combo.
                                 logging.warning(f"Mapped key '{key_name}' -> '{key_obj}' invalid for combination.")
                                 valid_combo = False; break
                         elif len(key_name) == 1: # Single char directly
                             try: combo_keys.append(KeyCode.from_char(key_name))
                             except Exception: logging.warning(f"Could not get KeyCode for char '{key_name}'"); valid_combo = False; break
                         else:
                             logging.warning(f"Unknown key name in combination: '{key_name}'")
                             valid_combo = False; break
                    else:
                         logging.warning(f"Invalid item type in combination list: {key_name}")
                         valid_combo = False; break

                if valid_combo and combo_keys:
                    simulate_key_combination(combo_keys)
                elif not combo_keys:
                     logging.warning(f"Empty key list derived from combination: {item}")

            else:
                logging.warning(f"Unexpected item type in parsed keys list: {item}")

    except Exception as e:
        logging.error(f"Error during OpenAI keyboard interpretation request: {e}", exc_info=True)


# --- Translation Function ---
async def translate_and_type(text_to_translate, source_lang_code, target_lang_code):
    """Translates text using OpenAI and types the result."""
    global openai_client, OPENAI_MODEL # Use configured model
    if not openai_client:
        logging.error("OpenAI client not available. Cannot translate.")
        simulate_typing(" [Translation Error: OpenAI not configured]")
        return
    if not text_to_translate:
        logging.warning("No text provided for translation.")
        return
    if not source_lang_code or not target_lang_code:
        logging.error(f"Missing source ({source_lang_code}) or target ({target_lang_code}) language for translation.")
        simulate_typing(" [Translation Error: Language missing]")
        return
    if source_lang_code == target_lang_code:
         logging.info("Source and target languages are the same, skipping translation call.")
         return # No need to translate if languages match

    # Get full language names for the prompt (optional, but potentially helpful for the model)
    # Using TARGET_LANGUAGE_OPTIONS from systray might be fragile if systray isn't imported/run
    # Let's stick to codes for now, or define a local map if needed.
    source_lang_name = source_lang_code # Or lookup full name
    target_lang_name = target_lang_code # Or lookup full name

    logging.info(f"Requesting translation from '{source_lang_name}' to '{target_lang_name}' for: '{text_to_translate}' using model '{OPENAI_MODEL}'")
    # Indicate translation is starting *after* the source text's trailing space
    simulate_typing("-> ") # Add space after arrow

    try:
        prompt = f"Translate the following text accurately from {source_lang_name} to {target_lang_name}. Output only the translated text:\n\n{text_to_translate}"

        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL, # Use the model from config
            messages=[
                {"role": "system", "content": "You are an expert translation engine."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2, # Lower temperature for more direct translation
            max_tokens=int(len(text_to_translate) * 2.5 + 50) # Generous token estimate
        )

        translated_text = response.choices[0].message.content.strip()
        logging.info(f"Translation received: '{translated_text}'")

        if translated_text:
            # Type the translation, followed by a space for subsequent typing
            simulate_typing(translated_text + " ")
        else:
            logging.warning("OpenAI returned an empty translation.")
            simulate_typing("[Translation Empty] ")

    except Exception as e:
        logging.error(f"Error during OpenAI translation request: {e}", exc_info=True)
        simulate_typing(f"[Translation Error: {type(e).__name__}] ")


# --- Deepgram Event Handlers ---
async def on_open(self, open, **kwargs):
    logging.info("Deepgram connection opened.")
    # --- MODIFIED: Update status indicator to CONNECTED --- >
    try:
        if status_mgr and status_mgr.thread.is_alive():
            # Send "connected" to signify successful connection
            status_mgr.queue.put_nowait(("connection_update", {"status": "connected"}))
    except queue.Full:
        logging.warning("Status queue full sending connection_update=connected on open.")
    except Exception as e:
        logging.error(f"Error sending status update on DG open: {e}")

async def on_message(self, result, **kwargs):
    global typed_word_history, final_source_text, final_keyboard_input_text # Add final_keyboard_input_text
    # --- ADDED GLOBALS ---
    global last_interim_transcript # REMOVED final_processed_this_session
    try:
        transcript = result.channel.alternatives[0].transcript
        # --- NEW: Log the raw transcript with representation ---
        logging.debug(f"Raw transcript received: {transcript!r}") # Use !r to see representation
        if not transcript:
            return

        # Determine action based on the globally set ACTIVE_MODE
        if ACTIVE_MODE == MODE_DICTATION:
            if result.is_final:
                # --- REMOVED CHECK for final processed flag ---
                logging.debug(f"Processing final dictation result: '{transcript}'")
                final_part = transcript
                # Note: handle_dictation_final expects the final segment transcript
                updated_history, text_typed_this_segment = handle_dictation_final(final_part, typed_word_history)
                typed_word_history = updated_history
                final_source_text = " ".join([entry['text'] for entry in typed_word_history])
                logging.debug(f"Dictation final source text updated: '{final_source_text}'")

                # --- REMOVED flag setting and log message ---
                last_interim_transcript = "" # Clear interim after processing a final part
            else:
                # --- Update last interim transcript ---
                last_interim_transcript = transcript
                # --- Call interim handler (as before) ---
                handle_dictation_interim(transcript)

        elif ACTIVE_MODE == MODE_KEYBOARD:
            # Keyboard mode might only care about the final result
            if result.is_final:
                 handle_keyboard_input_final(transcript)
            else:
                 # Optional: Show interim results in tooltip for keyboard mode too?
                 # handle_dictation_interim(transcript) # Reuse tooltip for now
                 pass # Or do nothing for interim in keyboard mode

        # elif ACTIVE_MODE == MODE_COMMAND: # If command mode is added back
        #    if result.is_final: handle_command_final(transcript)
        #    else: handle_command_interim(transcript)

    except (AttributeError, IndexError) as e:
        logging.error(f"Error processing Deepgram message: {e} - Result: {result}")
    except Exception as e: # Catch potential errors from handlers
        logging.error(f"Unhandled error in on_message handler: {e}", exc_info=True)

async def on_metadata(self, metadata, **kwargs):
    logging.debug(f"Deepgram Metadata: {metadata}")

async def on_speech_started(self, speech_started, **kwargs):
    logging.debug("Deepgram Speech Started")

async def on_utterance_end(self, utterance_end, **kwargs):
    logging.debug("Deepgram Utterance Ended")

async def on_error(self, error, **kwargs):
    logging.error(f"Deepgram Handled Error: {error}")
    # --- NEW: Update status indicator to ERROR --- >
    # --- MODIFIED: Update status indicator to ERROR --- >
    try:
        if status_mgr and status_mgr.thread.is_alive():
            status_mgr.queue.put_nowait(("connection_update", {"status": "error"}))
    except queue.Full:
        logging.warning("Status queue full sending connection_update=error on DG error.")
    except Exception as e:
        logging.error(f"Error sending status update on DG error: {e}")

async def on_close(self, close, **kwargs):
    logging.info("Deepgram connection closed.")
    # --- NEW: Optionally set status to idle on close if not error --- >
    # Check current status *before* setting to idle
    # Avoid reverting an 'error' state back to 'idle' on close.
    try:
        if status_mgr and status_mgr.thread.is_alive():
            # Only reset to idle if the status wasn't already error
            # This requires accessing the status indicator's state, which is tricky.
            # Let's simplify: Assume a close means idle unless an error already occurred.
            # The main loop will handle setting error state more reliably.
            # Let's *not* set to idle here, let the main loop manage idle state.
            pass # status_mgr.queue.put_nowait(("connection_update", {"status": "idle"}))
    except Exception as e:
        logging.error(f"Error sending status update on DG close: {e}")

async def on_unhandled(self, unhandled, **kwargs):
    logging.warning(f"Deepgram Unhandled Websocket Message: {unhandled}")

# --- Pynput Listener Callbacks ---
def on_click(x, y, button, pressed):
    # Use transcription_active_event as the main gatekeeper
    global start_time, status_queue, ui_interaction_cancelled, ui_action_queue # ADDED ui_action_queue
    global transcription_active_event # Use this event
    global typed_word_history, final_source_text, final_keyboard_input_text
    global SELECTED_LANGUAGE, TARGET_LANGUAGE, ACTIVE_MODE
    global initial_activation_pos
    global status_mgr
    # --- ADDED GLOBALS ---
    global last_interim_transcript # REMOVED final_processed_this_session

    # Determine trigger based only on DICTATION_TRIGGER_BUTTON for now
    is_primary_trigger = (button == DICTATION_TRIGGER_BUTTON)
    # TODO: Re-add command trigger logic if needed (e.g., check button == COMMAND_TRIGGER_BUTTON and modifiers)

    if not is_primary_trigger:
        return

    # --- Handle Press ---
    if pressed:
        ui_interaction_cancelled = False # Reset flag on new press
        if not transcription_active_event.is_set():
            logging.info(f"Trigger button pressed - starting mode: {ACTIVE_MODE}.")
            # Clear specific state based on ACTIVE_MODE
            if ACTIVE_MODE == MODE_DICTATION:
                typed_word_history.clear()
                final_source_text = ""
                last_interim_transcript = "" # Reset interim transcript
                # final_processed_this_session = False # REMOVED - No longer used
            elif ACTIVE_MODE == MODE_KEYBOARD: final_keyboard_input_text = ""
            # elif ACTIVE_MODE == MODE_COMMAND: current_command_transcript = ""

            # Set general active flag and time/pos
            transcription_active_event.set()
            start_time = time.time()
            initial_activation_pos = (x, y)
            logging.debug(f"Stored initial activation position: {initial_activation_pos}")

            # --- Send START command to main loop's queue --- >
            try:
                ui_action_queue.put_nowait(("start_transcription", None))
                logging.debug("Sent start_transcription command to main loop queue.")
            except queue.Full:
                logging.error("UI Action Queue full! Cannot send start_transcription command.")
                # If queue is full, something is wrong, maybe clear the event?
                transcription_active_event.clear()
                start_time = None
                initial_activation_pos = None
                return

            # --- Send status update to indicator AFTER sending start command ---
            try:
                # Send current ACTIVE_MODE to status indicator
                status_data = {"state": "active", "pos": initial_activation_pos,
                               "mode": ACTIVE_MODE,
                               "source_lang": SELECTED_LANGUAGE, "target_lang": TARGET_LANGUAGE}
                status_queue.put_nowait(("state", status_data))
            except queue.Full: logging.warning("Status queue full showing indicator.")
            except Exception as e: logging.error(f"Error sending initial state to status indicator: {e}")
        else: logging.warning(f"Attempted start {ACTIVE_MODE} while already active.")

    # --- Handle Release ---
    else:
        # Only process release if a transcription was active
        if not transcription_active_event.is_set():
             return

        # --- Check for UI Interaction (Hover Selection) FIRST --- >
        # Check hover state from status manager for potential actions
        hover_mode = None
        hover_lang_type = None
        hover_lang_code = None
        # --- MODIFIED: Use the more precise hovered_data if available --- >
        if status_mgr and hasattr(status_mgr, 'hovered_data') and status_mgr.hovered_data:
            hover_data = status_mgr.hovered_data
            if hover_data.get("type") == "mode":
                hover_mode = hover_data.get("value")
            elif hover_data.get("type") in ["source", "target"]:
                hover_lang_type = hover_data.get("type")
                hover_lang_code = hover_data.get("value")

        # --- Process Hover Selection if Found --- >
        if hover_mode:
            logging.info(f"Trigger release over mode option: {hover_mode}. Selecting mode.")
            try: ui_action_queue.put_nowait(("select_mode", hover_mode))
            except queue.Full: logging.warning(f"Action queue full sending hover mode selection ({hover_mode}).")
            ui_interaction_cancelled = True # Signal cancellation of normal stop flow
            logging.debug("Set ui_interaction_cancelled flag due to mode hover selection.")
            # --- Send selection confirmation to StatusIndicator for blink/hide ---
            try:
                 selection_data = {"type": "mode", "value": hover_mode}
                 status_queue.put_nowait(("selection_made", selection_data))
            except queue.Full: logging.warning(f"Status queue full sending selection confirmation.")
            # --- CRITICAL: Do NOT clear events here, let main loop handle based on flag ---
            # --- ALSO Do NOT hide UI here - let StatusIndicator handle it via 'selection_made'
            # --- ADD: Clear the event here as well to signal stop flow --- >
            transcription_active_event.clear()
            return # Exit callback early

        elif hover_lang_type and (hover_lang_code is not None or (hover_lang_type == 'target' and hover_lang_code is None)):
            logging.info(f"Trigger release over language option: Type={hover_lang_type}, Code={hover_lang_code}. Selecting language.")
            try: ui_action_queue.put_nowait(("select_language", {"type": hover_lang_type, "lang": hover_lang_code}))
            except queue.Full: logging.warning(f"Action queue full sending hover language selection ({hover_lang_type}={hover_lang_code}).")
            ui_interaction_cancelled = True # Signal cancellation of normal stop flow
            logging.debug("Set ui_interaction_cancelled flag due to language hover selection.")
            # --- Send selection confirmation to StatusIndicator for blink/hide ---
            try:
                 selection_data = {"type": "language", "lang_type": hover_lang_type, "value": hover_lang_code}
                 status_queue.put_nowait(("selection_made", selection_data))
            except queue.Full: logging.warning(f"Status queue full sending selection confirmation.")
            # --- CRITICAL: Do NOT clear events here, let main loop handle based on flag ---
            # --- ALSO Do NOT hide UI here - let StatusIndicator handle it via 'selection_made'
            # --- ADD: Clear the event here as well to signal stop flow --- >
            transcription_active_event.clear()
            return # Exit callback early

        # --- NO Hover Selection Detected: Proceed with Normal Stop Flow ---
        else:
            # --- IMMEDIATELY HIDE UI --- >
            # Send hide command regardless of hover or other logic.
            # The main loop stop flow should NOT handle hiding anymore.
            try:
                logging.debug("Button released (no hover selection): Sending immediate hide command to status indicator.")
                hide_data = {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": ""}
                status_queue.put_nowait(("state", hide_data))
                if ACTIVE_MODE == MODE_DICTATION:
                     tooltip_queue.put_nowait(("hide", None))
            except queue.Full: logging.warning("Queue full sending immediate hide on release.")
            except Exception as e: logging.error(f"Error sending immediate hide on release: {e}")
            # --- End Immediate Hide ---

            # --- Signal Transcription Stop (Normal Release - No Hover Selection) ---
            duration = time.time() - start_time if 'start_time' in globals() and start_time else 0
            logging.info(f"Trigger button released (no hover selection, duration: {duration:.2f}s). Signaling backend stop for {ACTIVE_MODE}.")
            # Clear the event to signal the main loop stop flow
            transcription_active_event.clear()
            initial_activation_pos = None
            # --- REMOVED explicit hide message sending - UI is hidden above, main loop handles backend ---

def on_press(key):
    global modifier_keys_pressed, status_queue
    global is_command_active, transcription_active_event
    global modifier_log_buffer, modifier_log_last_time
    # Only log the first press (not repeats)
    if key in PYNPUT_MODIFIER_MAP.values() and key is not None:
        if key not in modifier_keys_pressed:
            modifier_log_buffer.append(f"[{key} pressed]")
            modifier_keys_pressed.add(key)

    try:
        # --- Handle Esc during Command mode ---
        # if is_command_active.is_set() and key == keyboard.Key.esc: # If Command mode is added
        #     logging.info("ESC pressed during command - cancelling.")
        #     is_command_active.clear()
        #     transcription_active_event.clear() # Signal stop
        #     # current_mode = "cancel" # Maybe set a different cancel flag?
        #     ui_interaction_cancelled = True # Use cancel flag for now
        #     # Hide Status Indicator
        #     try:
        #         # Send current mode when hiding for consistency
        #         status_data = {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": ""}
        #         status_queue.put_nowait(("state", status_data))
        #     except queue.Full: logging.warning("Status queue full hiding indicator on ESC cancel.")

        # --- Handle Esc during ANY active mode (Optional: Cancel current action?) ---
        if transcription_active_event.is_set() and key == keyboard.Key.esc:
            logging.info(f"ESC pressed during {ACTIVE_MODE} - cancelling action.")
            ui_interaction_cancelled = True
            transcription_active_event.clear()
            try: tooltip_queue.put_nowait(("hide", None))
            except queue.Full: pass
            try:
                status_data = {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": ""}
                status_queue.put_nowait(("state", status_data))
            except queue.Full: pass


    except AttributeError:
        pass
    except Exception as e:
        logging.error(f"Error in on_press handler: {e}", exc_info=True)

def on_release(key):
    """Callback for key release events."""
    global modifier_keys_pressed, status_queue
    global is_command_active, transcription_active_event
    global modifier_log_buffer, modifier_log_last_time
    # Only log the release if the key was pressed
    if key in modifier_keys_pressed:
        modifier_log_buffer.append(f"[{key} released]")
        modifier_keys_pressed.discard(key)

    # If the command modifier key is released while command mode is active
    # if key == COMMAND_MODIFIER_KEY and is_command_active.is_set(): # If command mode exists
    #      logging.info(f"Command modifier ({key}) released while command mode active. Stopping command.")
    #      is_command_active.clear()
    #      transcription_active_event.clear() # Signal stop
    #      # Hide Status Indicator
    #      try:
    #          status_data = {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": ""}
    #          status_queue.put_nowait(("state", status_data))
    #      except queue.Full: logging.warning("Status queue full hiding indicator on cmd mod release.")

# --- Config Saving Function ---
def save_config_local(cfg_dict):
    """Saves the provided config dictionary back to the JSON file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg_dict, f, indent=2)
        logging.info(f"Configuration saved to {CONFIG_FILE} by vibe_app.")
        # Signal systray to update its display (it reloads file anyway on its interaction)
        # but good practice to signal. Let systray clear the event.
        systray_ui.config_reload_event.set()
    except IOError as e:
        logging.error(f"Error saving config file {CONFIG_FILE}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error saving config: {e}")

# --- Main Application Logic ---
async def main():
    # Remove current_mode from globals
    global tooltip_mgr, status_mgr, buffered_audio_input, deepgram, start_time
    global mouse_controller
    global current_command_transcript, final_source_text, typed_word_history
    global final_keyboard_input_text
    global ui_interaction_cancelled, config, SELECTED_LANGUAGE, TARGET_LANGUAGE, ACTIVE_MODE
    global initial_activation_pos
    global status_mgr

    logging.info("Starting Vibe App...")
    # --- Initialize Systray ---
    systray_thread = threading.Thread(target=systray_ui.run_systray, args=(systray_ui.exit_app_event,), daemon=True)
    systray_thread.start()
    logging.info("Systray UI thread started.")

    # --- Start Tooltip Manager ---
    tooltip_mgr = TooltipManager(tooltip_queue)
    tooltip_mgr.start()
    logging.info("Tooltip Manager started.")

    # --- Start Status Indicator Manager ---
    # Pass the config, full language maps, and available modes
    status_mgr = StatusIndicatorManager(status_queue, ui_action_queue,
                                        config=config,
                                        # --- Pass i18n function --- >
                                        # get_translation_func=_,
                                        # --- Keep language maps for now, UI will use _() later --- >
                                        all_languages=ALL_LANGUAGES,
                                        all_languages_target=ALL_LANGUAGES_TARGET,
                                        available_modes=AVAILABLE_MODES)
    status_mgr.start()
    logging.info("Status Indicator Manager started.")

    # --- Start Buffered Audio Input ---
    buffered_audio_input = BufferedAudioInput(status_queue)
    buffered_audio_input.start()
    logging.info("Buffered Audio Input thread started.")

    # --- Initialize Deepgram Client ---
    try:
        config_dg = DeepgramClientOptions(verbose=logging.WARNING)
        deepgram = DeepgramClient(DEEPGRAM_API_KEY, config_dg)
    except Exception as e:
        logging.error(f"Failed to initialize Deepgram client: {e}")
        systray_ui.exit_app_event.set() # Signal exit if DG fails
        return

    # --- Initialize pynput Controller ---
    mouse_controller = mouse.Controller()

    # --- Start Listeners ---
    mouse_listener = mouse.Listener(on_click=on_click)
    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener.start()
    keyboard_listener.start()
    logging.info("Input listeners started.")

    # --- Loop Variables --- >
    dg_connection = None
    microphone = None
    # active_mode_on_stop = None # Capture this differently
    last_hover_check_time = 0
    hover_check_interval = 0.05 # Check hover every 50ms instead of 100ms
    # --- NEW: Track if we are in the process of stopping --- >
    is_stopping = False
    stop_initiated_time = 0 # Track when stop was first detected
    active_mode_on_stop = None # Store mode when stop begins
    start_time = None # Make sure it's initialized
    stopping_start_time = None # NEW: Store the start_time for the specific stop cycle being processed

    # --- NEW: Connection Retry Constants --- (Simplified Timeout)
    MAX_CONNECT_ATTEMPTS = 3
    CONNECT_RETRY_DELAY_SEC = 0.5
    OVERALL_CONNECT_TIMEOUT_SEC = 5.0 # Total time allowed for all attempts
    # --- Removed individual attempt timeouts ---

    # --- NEW: Event for connection success --- >
    connection_established_event = asyncio.Event()

    try:
        while not systray_ui.exit_app_event.is_set():
            current_time = time.time()
            # perform_stop_flow = False # Remove this flag

            # --- Check if stop is signaled --- >
            stop_detected_this_cycle = False
            if not transcription_active_event.is_set() and start_time is not None and not is_stopping:
                # Event is clear, we were active (start_time is set), and not already stopping
                logging.info("Stop signal detected (transcription_active_event is clear).")
                is_stopping = True
                stop_initiated_time = current_time
                stopping_start_time = start_time # CAPTURE start_time for this stop cycle
                stop_detected_this_cycle = True
                active_mode_on_stop = ACTIVE_MODE # Capture the mode when stop is first detected
                # --- REMOVED: Immediate UI hide logic moved to on_click release handler ---
                # logging.debug(f"Immediately hiding UI for {active_mode_on_stop}.")
                # try: status_mgr.queue.put_nowait(("state", {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": ""}))
                # except queue.Full: logging.error("Status queue full sending immediate hide message.")
                # except Exception as e: logging.error(f"Error sending immediate hide message: {e}")
                # if active_mode_on_stop == MODE_DICTATION:
                #     try: tooltip_mgr.queue.put_nowait(("hide", None))
                #     except queue.Full: logging.error("Tooltip queue full sending immediate hide message.")
                #     except Exception as e: logging.error(f"Error sending immediate tooltip hide message: {e}")

            # --- Handle UI Actions FIRST (Keep this high priority) --- >
            try:
                action_command, action_data = ui_action_queue.get_nowait()
                if action_command == "select_language":
                    lang_type = action_data.get("type"); new_lang = action_data.get("lang")
                    config_key = "selected_language" if lang_type == "source" else "target_language"
                    recent_list_key = "recent_source_languages" if lang_type == "source" else "recent_target_languages"

                    if "general" not in config: config["general"] = {}
                    config["general"][config_key] = new_lang

                    # --- Update Recent Language List --- >
                    recent_list = config["general"].get(recent_list_key, [])
                    if new_lang in recent_list: recent_list.remove(new_lang)
                    recent_list.insert(0, new_lang)
                    MAX_RECENT_LANGS = 10
                    config["general"][recent_list_key] = recent_list[:MAX_RECENT_LANGS]
                    # --- End Update Recent Language List ---

                    logging.info(f"UI selected {lang_type} language: {new_lang}. Updating config.")
                    save_config_local(config); apply_config(config)
                    # --- Crucial: Set cancel flag if interaction happens while stopping --- >
                    if is_stopping: ui_interaction_cancelled = True
                    # Original code had ui_interaction_cancelled = True here always, let's keep it
                    ui_interaction_cancelled = True

                elif action_command == "select_mode":
                    new_mode = action_data
                    if "general" not in config: config["general"] = {}
                    config["general"]["active_mode"] = new_mode
                    logging.info(f"UI selected mode: {new_mode}. Updating config.")
                    save_config_local(config); apply_config(config)
                    # --- Crucial: Set cancel flag if interaction happens while stopping --- >
                    if is_stopping: ui_interaction_cancelled = True
                    # Original code had ui_interaction_cancelled = True here always, let's keep it
                    ui_interaction_cancelled = True

            except queue.Empty: pass
            except Exception as e: logging.error(f"Error processing UI action queue: {e}", exc_info=True)

            # --- Start Transcription Flow --- >
            # Only start if the event is set AND we are not in the process of stopping
            if transcription_active_event.is_set() and not is_stopping and dg_connection is None:
                # --- NEW: Set status to CONNECTING *before* attempting connection --- >
                try:
                    if status_mgr and status_mgr.thread.is_alive():
                        status_mgr.queue.put_nowait(("connection_update", {"status": "connecting"})) # Yellow state
                except queue.Full: logging.warning("Status queue full setting status to CONNECTING before connect attempt.")
                except Exception as e: logging.error(f"Error setting status to CONNECTING before connect attempt: {e}")
                # --- End New --- >

                logging.info(f"Activating transcription in {ACTIVE_MODE} mode...")
                if ACTIVE_MODE == MODE_DICTATION: typed_word_history.clear(); final_source_text = ""
                elif ACTIVE_MODE == MODE_KEYBOARD: final_keyboard_input_text = ""

                # --- Connection Retry Loop --- >
                connection_successful = False
                microphone = None # Reset microphone state here
                connection_established_event.clear() # Clear event before attempts
                start_connect_time = time.time() # Track start of overall connection process

                for attempt in range(MAX_CONNECT_ATTEMPTS):
                    if time.time() - start_connect_time > OVERALL_CONNECT_TIMEOUT_SEC:
                        logging.warning(f"Overall connection timeout ({OVERALL_CONNECT_TIMEOUT_SEC}s) exceeded during attempt {attempt + 1}. Aborting.")
                        break # Exit retry loop

                    logging.info(f"Attempting Deepgram connection (Attempt {attempt + 1}/{MAX_CONNECT_ATTEMPTS})...")
                    # Status should already be 'connecting'

                    try:
                        dg_connection = deepgram.listen.asyncwebsocket.v("1")
                        # Register handlers
                        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
                        # --- MODIFIED: Wrap on_open to set event --- >
                        async def on_open_wrapper(self, open, **kwargs):
                            await on_open(self, open, **kwargs)
                            connection_established_event.set() # Signal success
                        dg_connection.on(LiveTranscriptionEvents.Open, on_open_wrapper)
                        # --- End Modified --- >
                        dg_connection.on(LiveTranscriptionEvents.Close, on_close)
                        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
                        dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)
                        dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
                        dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
                        dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)

                        options = LiveOptions(model="nova-2", language=SELECTED_LANGUAGE, interim_results=True, smart_format=True,
                                              encoding="linear16", channels=1, sample_rate=16000, punctuate=True, numerals=True,
                                              utterance_end_ms="1000", vad_events=True, endpointing=300)

                        # --- Start Connection (No wait_for) --- >
                        await dg_connection.start(options)

                        # --- Wait for connection success event OR timeout --- >
                        remaining_timeout = max(0, OVERALL_CONNECT_TIMEOUT_SEC - (time.time() - start_connect_time))
                        logging.debug(f"Waiting for connection event with remaining timeout: {remaining_timeout:.2f}s")
                        try:
                            await asyncio.wait_for(connection_established_event.wait(), timeout=remaining_timeout)
                            logging.info(f"Connection established (on_open received) on attempt {attempt + 1}.")
                            connection_successful = True
                            # Don't set status here, on_open handler does it.
                            break # Exit retry loop on success
                        except asyncio.TimeoutError:
                             logging.warning(f"Did not receive on_open within timeout on attempt {attempt + 1}.")
                             # Clean up connection before next attempt
                             if dg_connection:
                                 try: await dg_connection.finish() # Try to close gracefully
                                 except Exception: pass
                                 finally: dg_connection = None

                    except Exception as e:
                        logging.error(f"Error during DG connection attempt {attempt + 1}: {e}", exc_info=(attempt == MAX_CONNECT_ATTEMPTS - 1))
                        # Clean up connection before next attempt
                        if dg_connection:
                            try: await dg_connection.finish() # Try to close gracefully
                            except Exception: pass
                            finally: dg_connection = None

                    # --- Retry Logic --- >
                    if not connection_successful and attempt < MAX_CONNECT_ATTEMPTS - 1:
                        logging.info(f"Retrying connection in {CONNECT_RETRY_DELAY_SEC} seconds...")
                        # Status remains 'connecting'
                        await asyncio.sleep(CONNECT_RETRY_DELAY_SEC)
                    elif not connection_successful and attempt == MAX_CONNECT_ATTEMPTS - 1:
                         logging.error("All Deepgram connection attempts failed or timed out.")
                         # --- Set status to ERROR after all retries fail --- >
                         try:
                             if status_mgr and status_mgr.thread.is_alive():
                                 status_mgr.queue.put_nowait(("connection_update", {"status": "error"})) # Red state
                                 logging.info("Set status indicator to ERROR due to connection failure.")
                             else:
                                  logging.warning("Status manager not available to set error state.")
                         except queue.Full: logging.warning("Status queue full setting status to ERROR after failed attempts.")
                         except Exception as status_err: logging.error(f"Error setting status to ERROR after failed attempts: {status_err}")
                # --- End Connection Retry Loop --- >

                # --- Start Mic and Send Buffer ONLY if connection was successful --- >
                if connection_successful and dg_connection:
                    try:
                        # --- Send Buffer --- >
                        pre_activation_buffer = buffered_audio_input.get_buffer()
                        if pre_activation_buffer:
                            total_bytes = sum(len(chunk) for chunk in pre_activation_buffer)
                            logging.info(f"Sending pre-activation buffer: {len(pre_activation_buffer)} chunks, {total_bytes} bytes.")
                            for chunk in pre_activation_buffer:
                                if dg_connection and await dg_connection.is_connected():
                                    try: await dg_connection.send(chunk); await asyncio.sleep(0.001)
                                    except Exception as send_err: logging.warning(f"Error sending buffer chunk: {send_err}"); break
                                else: logging.warning("DG connection lost while sending buffer."); break
                        # --- Microphone Setup --- >
                        original_send = dg_connection.send
                        async def logging_send_wrapper(data):
                            is_conn_connected = False
                            if dg_connection:
                                try: is_conn_connected = await dg_connection.is_connected()
                                except Exception: pass
                            if is_conn_connected:
                                try: await original_send(data)
                                except Exception as mic_send_err: logging.warning(f"Error sending mic data: {mic_send_err}")
                        microphone = Microphone(logging_send_wrapper)
                        microphone.start()
                        logging.info("Deepgram microphone started and streaming.")
                        # Status should already be 'connected' from on_open
                    except Exception as mic_buf_err:
                         logging.error(f"Error sending buffer or starting microphone: {mic_buf_err}", exc_info=True)
                         # Set error state if mic/buffer fails
                         try: status_mgr.queue.put_nowait(("connection_update", {"status": "error"}))
                         except Exception: pass
                         if microphone: microphone.finish(); microphone = None
                         if dg_connection: await dg_connection.finish(); dg_connection = None
                         connection_successful = False # Mark as failed
                # --- End Mic/Buffer Start ---

                # --- IMMEDIATE CHECK AFTER START/RETRY Block --- >
                # If connection was marked successful but stop event is now set
                if connection_successful and not transcription_active_event.is_set():
                    logging.warning("Stop event detected immediately after successful DG/Mic start. Initiating stop.")
                    is_stopping = True
                    stop_initiated_time = time.time()
                    stopping_start_time = start_time
                    active_mode_on_stop = ACTIVE_MODE
                    # Ensure UI hidden (redundant if stop signal handler already did it, but safe)
                    try: status_mgr.queue.put_nowait(("state", {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": ""}))
                    except Exception: pass
                    if ACTIVE_MODE == MODE_DICTATION:
                        try: tooltip_mgr.queue.put_nowait(("hide", None))
                        except Exception: pass

                # --- Handle case where ALL connection attempts failed --- >
                elif not connection_successful:
                     # Ensure mic/connection are None if we exit the loop due to failure
                     microphone = None
                     dg_connection = None
                     # Status should be 'error' from loop exit logic
                     logging.debug("Connection failed, skipping mic/buffer start.")

            # --- Process Stop Flow --- >
            # If stopping has been initiated
            if is_stopping:
                logging.debug(f"Processing stop flow steps for {active_mode_on_stop}...")
                # Stop Mic/Conn (if they exist)
                if microphone:
                     microphone.finish()
                     microphone = None
                     logging.info("DG Mic finished during stop flow.")
                if dg_connection:
                    try:
                        # Add short sleep before finish ONLY if buffer might still be sending? Risky.
                        # Let's just try finishing directly.
                        # await asyncio.sleep(0.05) # Removed sleep
                        await dg_connection.finish()
                        logging.info("DG Conn finished during stop flow.")
                    except Exception as e: logging.warning(f"Error finishing DG conn during stop flow: {e}")
                    finally:
                         dg_connection = None # Ensure this is set to None

                # Check cancellation flag (might have been set by UI action during stop detection)
                perform_action = True
                if ui_interaction_cancelled:
                     perform_action = False
                     ui_interaction_cancelled = False # Reset flag AFTER checking it
                     logging.info("UI interaction cancelled during stop flow.")

                # Calculate duration using the start_time captured for THIS stop cycle
                duration = stop_initiated_time - stopping_start_time if stopping_start_time else 0

                # Post-process / Translate / Execute
                action_task = None
                if perform_action:
                    if duration >= MIN_DURATION_SEC:
                        logging.debug(f"Performing action post-stop for {active_mode_on_stop} (duration: {duration:.2f}s)")
                        if active_mode_on_stop == MODE_DICTATION:
                            if TARGET_LANGUAGE and TARGET_LANGUAGE != SELECTED_LANGUAGE and final_source_text:
                                action_task = asyncio.create_task(translate_and_type(final_source_text.strip(), SELECTED_LANGUAGE, TARGET_LANGUAGE))
                            else: logging.info("Dictation finished post-stop. No translation.")
                        elif active_mode_on_stop == MODE_KEYBOARD:
                             action_task = asyncio.create_task(handle_keyboard_input_final(final_keyboard_input_text))
                    else: # Discard short
                         logging.info(f"Duration < min ({MIN_DURATION_SEC}s), discarding action post-stop for {active_mode_on_stop}.")
                         if active_mode_on_stop == MODE_DICTATION: typed_word_history.clear(); final_source_text = ""
                         elif active_mode_on_stop == MODE_KEYBOARD: final_keyboard_input_text = ""
                else: # Clear state if cancelled
                     logging.info(f"Action cancelled post-stop for {active_mode_on_stop}, clearing state.")
                     if active_mode_on_stop == MODE_DICTATION: typed_word_history.clear(); final_source_text = ""
                     elif active_mode_on_stop == MODE_KEYBOARD: final_keyboard_input_text = ""

                # Await the action task if it was created
                if action_task:
                    try: await action_task
                    except Exception as e: logging.error(f"Error awaiting action task post-stop ({active_mode_on_stop}): {e}", exc_info=True)

                # --- Reset state AFTER processing stop flow --- >
                logging.debug("Stop flow processing complete. Resetting state.")
                is_stopping = False
                # Check if a new activation started *during* this stop flow
                # by comparing the current global start_time with the one we captured
                # when *this* stop flow began.
                if start_time == stopping_start_time:
                    # No new activation interfered, safe to reset
                    logging.debug(f"Current start_time ({start_time}) matches stopping_start_time ({stopping_start_time}). Resetting.")
                    start_time = None
                    initial_activation_pos = None
                else:
                    # A new activation occurred (global start_time was updated), keep it.
                    logging.info(f"New activation detected during stop flow (current start_time {start_time} != stopping_start_time {stopping_start_time}). Keeping current start_time.")

                # Always clear these buffers/states related to the *just completed* stopped action
                final_keyboard_input_text = ""
                active_mode_on_stop = None
                stopping_start_time = None # Clear the temporary variable

            # --- Check Config Reload --- >
            if systray_ui.config_reload_event.is_set():
                logging.info("Detected config reload request.")
                old_source = SELECTED_LANGUAGE
                config = load_config()
                apply_config(config)
                tooltip_mgr.reload_config()
                if status_mgr:
                    status_mgr.config = config # Update config reference
                    # apply_config (called earlier) should handle translation reload if necessary
                    logging.info("Updated StatusIndicatorManager's config reference after reload.")
                systray_ui.config_reload_event.clear()
                # Check if DG needs restart due to language change
                should_restart_dg = False
                if SELECTED_LANGUAGE != old_source:
                    if is_stopping:
                        logging.warning("Config reload changed language during stop flow. Restart will happen naturally if needed.")
                    elif transcription_active_event.is_set() and dg_connection is not None:
                        logging.info("Source language changed while active, initiating stop to restart DG...")
                        transcription_active_event.clear() # Let the stop flow handle restart on next cycle
                        # No need to set is_stopping here, the cleared event will trigger it
                    # else: language changed but not active or stopping, no action needed now

            # --- Thread Health Checks --- >
            if not tooltip_mgr.thread.is_alive() and not tooltip_mgr._stop_event.is_set(): logging.error("Tooltip thread died."); break
            if not status_mgr.thread.is_alive() and not status_mgr._stop_event.is_set(): logging.error("Status Indicator thread died."); break

            # --- At the end of main loop, flush modifier log buffer --- >
            flush_modifier_log(force=True)

            await asyncio.sleep(0.02) # Keep the reduced sleep

    except (asyncio.CancelledError, KeyboardInterrupt): logging.info("Main task cancelled/interrupted.")
    finally:
        logging.info("Stopping Vibe App...")
        # Trigger exit event if not already set, to ensure systray stops cleanly
        if not systray_ui.exit_app_event.is_set():
            systray_ui.exit_app_event.set()

        # --- Stop Audio Input ---
        if 'buffered_audio_input' in locals() and buffered_audio_input: buffered_audio_input.stop()

        # --- Signal GUI Managers to Stop ---
        if 'tooltip_mgr' in locals() and tooltip_mgr: tooltip_mgr.stop()
        if 'status_mgr' in locals() and status_mgr: status_mgr.stop()
        logging.info("GUI Managers stop requested.")

        # --- Systray Stop Logic ---
        # Wait for the systray thread to finish if it's running
        if 'systray_thread' in locals() and systray_thread.is_alive():
            logging.info("Waiting for systray thread to exit...")
            systray_thread.join(timeout=1.0) # Wait up to 1 second
            if systray_thread.is_alive():
                logging.warning("Systray thread did not exit cleanly.")
            else:
                logging.info("Systray thread finished.")

        # Cleanup listeners (pynput listeners are daemons, no need to join)
        if 'mouse_listener' in locals() and mouse_listener.is_alive(): mouse_listener.stop()
        if 'keyboard_listener' in locals() and keyboard_listener.is_alive(): keyboard_listener.stop()
        logging.info("Input listeners stop requested.")

        # --- Ensure Deepgram microphone and connection are stopped ---
        if 'microphone' in locals() and microphone:
            logging.debug("Finishing Deepgram microphone on exit...")
            microphone.finish()
            logging.info("Deepgram microphone finished on exit.")

        if 'dg_connection' in locals() and dg_connection:
            is_conn_connected_final = False
            try: is_conn_connected_final = await dg_connection.is_connected()
            except Exception: pass # Ignore errors checking state during shutdown
            if is_conn_connected_final:
                logging.debug("Finishing Deepgram connection on exit...")
                try:
                    await dg_connection.finish()
                    logging.info("Deepgram connection finished on exit.")
                except asyncio.CancelledError: logging.warning("Deepgram finish cancelled.")
                except Exception as e: logging.error(f"Error during final dg_connection.finish: {e}")
            else: logging.info("Deepgram connection already closed on exit.")
        else: logging.info("No active Deepgram connection to finish on exit.")

        logging.info("Vibe App finished.")

# --- Add Exit Event for Systray Communication ---
systray_ui.exit_app_event = threading.Event() # Create event in main module

# --- Copy Preferred Languages (Temporary Solution) ---
# Ideally, move these to a shared constants/config module later
# --- ADD ALL_LANGUAGES definition --- >
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

PREFERRED_SOURCE_LANGUAGES = {
    "en-US": "English (US)",
    "fr-FR": "French",
}
PREFERRED_TARGET_LANGUAGES = {
    None: "Aucune", # Simpler name for display
    "en-US": "English (US)",
    "fr-FR": "French",
}
# Add more preferred languages here if needed

# --- Derive ALL_LANGUAGES_TARGET --- >
# Create a version of ALL_LANGUAGES suitable for target selection (includes None)
ALL_LANGUAGES_TARGET = {None: "Aucune"} # Start with None
ALL_LANGUAGES_TARGET.update(ALL_LANGUAGES) # Use the ALL_LANGUAGES defined above

# --- Define Available Modes --- >
AVAILABLE_MODES = {
    "Dictation": "Dictation Mode",
    "Keyboard": "Keyboard Input Mode",
    # Add "Command" later if needed
}

if __name__ == "__main__":
    # Ensure logs directory exists
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    # API Key check moved earlier, before main()
    # Check if config loading failed catastrophically (though load_config tries to return defaults)
    if config is None:
         print("CRITICAL: Configuration could not be loaded. Exiting.")
    else:
        try:
            # --- ADD pyautogui failsafe check ---
            pyautogui.FAILSAFE = True # Enable failsafe (move mouse to corner to stop)
            logging.info("PyAutoGUI FAILSAFE enabled.")
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("Application interrupted by user (Ctrl+C).")
        except pyautogui.FailSafeException:
             logging.critical("PyAutoGUI FAILSAFE triggered! Exiting.")
        except Exception as e:
            logging.error(f"An unexpected error occurred in main run: {e}", exc_info=True) 