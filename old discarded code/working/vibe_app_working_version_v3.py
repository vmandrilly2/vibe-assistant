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
from status_indicator import StatusIndicatorManager

from pynput import mouse, keyboard
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone, # Import Microphone class
)

# --- Configuration Loading ---
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
  "general": {
    "min_duration_sec": 0.5,
    "selected_language": "en-US",
    "target_language": None,
    "openai_model": "gpt-4.1-nano"
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
                # Basic validation/merging with defaults for missing keys could be added here
                # For now, assume the structure is correct if the file loads
                logging.info(f"Loaded configuration from {CONFIG_FILE}")
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

# --- Pynput Mappings --- (Define globally)
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

def apply_config(cfg):
    """Applies the loaded configuration to the global variables."""
    global DICTATION_TRIGGER_BUTTON, COMMAND_TRIGGER_BUTTON, COMMAND_MODIFIER_KEY_STR
    global COMMAND_MODIFIER_KEY, MIN_DURATION_SEC, SELECTED_LANGUAGE
    global TOOLTIP_ALPHA, TOOLTIP_BG, TOOLTIP_FG, TOOLTIP_FONT_FAMILY, TOOLTIP_FONT_SIZE
    global TARGET_LANGUAGE, OPENAI_MODEL

    logging.info("Applying configuration...")
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

        # Tooltip
        TOOLTIP_ALPHA = float(cfg.get("tooltip", {}).get("alpha", 0.85))
        TOOLTIP_BG = str(cfg.get("tooltip", {}).get("bg_color", "lightyellow"))
        TOOLTIP_FG = str(cfg.get("tooltip", {}).get("fg_color", "black"))
        TOOLTIP_FONT_FAMILY = str(cfg.get("tooltip", {}).get("font_family", "Arial"))
        TOOLTIP_FONT_SIZE = int(cfg.get("tooltip", {}).get("font_size", 10))

        target_lang_str = TARGET_LANGUAGE if TARGET_LANGUAGE else "None"
        logging.info(f"Config applied: SourceLang={SELECTED_LANGUAGE}, TargetLang={target_lang_str}, Model={OPENAI_MODEL}, Dictation={triggers_cfg.get('dictation_button', 'middle')}, ...")

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
# Ensure level is set to DEBUG and add FileHandler
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
log_level = logging.DEBUG

# Console Handler
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

# File Handler (logs to vibe_app.log in the same directory)
try:
    file_handler = logging.FileHandler("vibe_app.log", mode='w') # 'w' overwrites log each time
    file_handler.setFormatter(log_formatter)
except Exception as e:
    print(f"Error setting up file logging: {e}") # Use print as logging might fail
    file_handler = None

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(log_level)
root_logger.addHandler(stream_handler)
if file_handler:
    root_logger.addHandler(file_handler)
    logging.info("File logging configured to vibe_app.log") # Log success

# --- Global State ---
is_dictation_active = threading.Event()
is_command_active = threading.Event()
transcription_active_event = threading.Event() # True if either dictation or command is active
current_mode = None # 'dictation' or 'command'
tooltip_queue = queue.Queue() # Queue for communicating with the tooltip thread
status_queue = queue.Queue() # For the microphone status icon (handled by StatusIndicatorManager)
modifier_keys_pressed = set() # Keep track of currently pressed modifier keys
# --- NEW: Queue for actions from UI elements ---
ui_action_queue = queue.Queue()
# --- NEW: Flag for cancellation ---
ui_interaction_cancelled = False
# --- NEW: Store initial position ---
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

# --- NEW/MODIFIED: Translation Function ---
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

async def on_message(self, result, **kwargs):
    global typed_word_history, final_source_text # Keep global here to update the list reference
    try:
        transcript = result.channel.alternatives[0].transcript
        if not transcript:
            return

        if current_mode == "dictation": # Handles both dictation and dictation+translation
            if result.is_final:
                final_part = transcript # Get the final utterance part
                # Pass history in, get potentially modified history back AND the text typed
                updated_history, text_typed_this_segment = handle_dictation_final(final_part, typed_word_history)
                typed_word_history = updated_history # Update history state

                # --- Accumulate the final source text ---
                # We rebuild the full text from the *updated* history after processing the segment
                final_source_text = " ".join([entry['text'] for entry in typed_word_history])
                logging.debug(f"Dictation final source text updated: '{final_source_text}'")
            else:
                # Interim handler updates tooltip
                handle_dictation_interim(transcript)

        elif current_mode == "command":
            if result.is_final:
                handle_command_final(transcript)
            else:
                handle_command_interim(transcript)

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

async def on_close(self, close, **kwargs):
    logging.info("Deepgram connection closed.")

async def on_unhandled(self, unhandled, **kwargs):
    logging.warning(f"Deepgram Unhandled Websocket Message: {unhandled}")

# --- Pynput Listener Callbacks ---
def on_click(x, y, button, pressed):
    # --- Make sure status_mgr is accessible ---
    global current_mode, start_time, status_queue, ui_interaction_cancelled
    global is_dictation_active, is_command_active
    global transcription_active_event
    global typed_word_history, final_source_text
    global SELECTED_LANGUAGE, TARGET_LANGUAGE
    global initial_activation_pos
    global status_mgr # Ensure status_mgr is accessible

    trigger_mode = None
    active_event = None
    required_modifier = None
    modifier_str = None

    # Determine which mode is being triggered, checking modifiers
    if button == DICTATION_TRIGGER_BUTTON:
        is_command_modifier_pressed = (COMMAND_MODIFIER_KEY is not None and COMMAND_MODIFIER_KEY in modifier_keys_pressed)
        if not is_command_modifier_pressed:
            trigger_mode = "dictation"; active_event = is_dictation_active
        else: return
    elif COMMAND_TRIGGER_BUTTON is not None and button == COMMAND_TRIGGER_BUTTON:
        modifier_ok = (COMMAND_MODIFIER_KEY is None or COMMAND_MODIFIER_KEY in modifier_keys_pressed)
        if modifier_ok:
            trigger_mode = "command"; active_event = is_command_active
            required_modifier = COMMAND_MODIFIER_KEY; modifier_str = COMMAND_MODIFIER_KEY_STR
        else: return
    else:
        return

    if trigger_mode:
        if pressed:
            # --- Reset cancellation flag on new press ---
            ui_interaction_cancelled = False
            if not transcription_active_event.is_set():
                mod_log_str = f" with {modifier_str} " if required_modifier else ""
                logging.info(f"{trigger_mode.capitalize()} button pressed{mod_log_str}- starting mode.")
                is_dictation_active.clear(); is_command_active.clear()
                if trigger_mode == "dictation": typed_word_history.clear(); final_source_text = ""
                elif trigger_mode == "command": global current_command_transcript; current_command_transcript = ""
                active_event.set(); transcription_active_event.set()
                current_mode = trigger_mode
                start_time = time.time()
                initial_activation_pos = (x, y)
                logging.debug(f"Stored initial activation position: {initial_activation_pos}")
                try:
                    status_data = {"state": "active", "pos": initial_activation_pos,
                                   "source_lang": SELECTED_LANGUAGE, "target_lang": TARGET_LANGUAGE}
                    status_queue.put_nowait(("state", status_data))
                except queue.Full: logging.warning("Status queue full showing indicator.")
                except Exception as e: logging.error(f"Error sending initial state to status indicator: {e}")
            else: logging.warning(f"Attempted start {trigger_mode} while already active.")
        else: # Button released
            # --- Check for Hover Selection FIRST ---
            hover_lang_type = None
            hover_lang_code = None
            # Check if status_mgr exists and has the hover attributes
            if status_mgr and hasattr(status_mgr, 'hovering_over_lang_type') and hasattr(status_mgr, 'hovering_over_lang_code'):
                hover_lang_type = status_mgr.hovering_over_lang_type
                hover_lang_code = status_mgr.hovering_over_lang_code

            if hover_lang_type and hover_lang_code is not None: # Check if hover was active
                logging.info(f"Trigger release over language option: Type={hover_lang_type}, Code={hover_lang_code}. Selecting language.")
                # Send action to main thread via the action queue
                try:
                    ui_action_queue.put_nowait(("select_language", {"type": hover_lang_type, "lang": hover_lang_code}))
                except queue.Full:
                    logging.warning(f"Action queue full sending hover language selection ({hover_lang_type}={hover_lang_code}).")
                
                # Signal cancellation of normal dictation/command flow
                ui_interaction_cancelled = True
                logging.debug("Set ui_interaction_cancelled flag due to hover selection.")

                # Explicitly hide indicator and clear events (as normal flow is skipped)
                try: status_queue.put_nowait(("state", {"state": "hidden", "source_lang": "", "target_lang": ""}))
                except queue.Full: pass
                if active_event: active_event.clear()
                transcription_active_event.clear()
                current_mode = None # Prevent stop flow from running
                initial_activation_pos = None # Clear initial pos

                # We return here because the language selection handles UI updates implicitly
                return

            # --- If not selecting language via hover, proceed with normal release ---
            elif active_event and active_event.is_set():
                duration = time.time() - start_time if 'start_time' in globals() and start_time else 0
                logging.info(f"{trigger_mode.capitalize()} button released (duration: {duration:.2f}s). Stopping mode normally.")
                active_event.clear()
                transcription_active_event.clear()
                initial_activation_pos = None

def on_press(key):
    global current_mode, modifier_keys_pressed, status_queue
    global is_dictation_active, is_command_active # Added is_dictation_active
    global transcription_active_event

    # Track modifier key presses
    if key in PYNPUT_MODIFIER_MAP.values() and key is not None:
         logging.debug(f"Modifier pressed: {key}")
         modifier_keys_pressed.add(key)

    try:
        # --- Handle Esc during Command mode ---
        if is_command_active.is_set() and key == keyboard.Key.esc:
            logging.info("ESC pressed during command - cancelling.")
            is_command_active.clear()
            transcription_active_event.clear() # Signal stop
            current_mode = "cancel" # Special mode for main loop
            # Hide Status Indicator (send empty langs on hide)
            try:
                status_data = {"state": "hidden", "source_lang": "", "target_lang": ""}
                status_queue.put_nowait(("state", status_data))
            except queue.Full:
                logging.warning("Status queue full hiding indicator on ESC cancel.")
            # TODO: Hide command feedback UI if/when it exists

        # --- Handle Esc during Dictation mode (Optional: Cancel dictation?) ---
        # Example: Uncomment if you want Esc to cancel dictation too
        # if is_dictation_active.is_set() and key == keyboard.Key.esc:
        #     logging.info("ESC pressed during dictation - cancelling.")
        #     is_dictation_active.clear()
        #     transcription_active_event.clear()
        #     current_mode = "cancel"
        #     # Hide Tooltip & Status Indicator
        #     try: tooltip_queue.put_nowait(("hide", None))
        #     except queue.Full: pass
        #     try:
        #         status_data = {"state": "hidden", "source_lang": "", "target_lang": ""}
        #         status_queue.put_nowait(("state", status_data))
        #     except queue.Full: pass

    except AttributeError:
        pass
    except Exception as e: # Catch potential errors
        logging.error(f"Error in on_press handler: {e}", exc_info=True)

def on_release(key):
    """Callback for key release events."""
    global modifier_keys_pressed, status_queue
    global is_command_active, transcription_active_event

    # Track modifier key releases
    if key in modifier_keys_pressed:
        logging.debug(f"Modifier released: {key}")
        modifier_keys_pressed.discard(key)

    # If the command modifier key is released while the command trigger button is still held, stop command mode.
    if key == COMMAND_MODIFIER_KEY and is_command_active.is_set():
         logging.info(f"Command modifier ({key}) released while command mode active. Stopping mode.")
         is_command_active.clear()
         transcription_active_event.clear() # Signal stop
         # Hide Status Indicator (send empty langs on hide)
         try:
             status_data = {"state": "hidden", "source_lang": "", "target_lang": ""}
             status_queue.put_nowait(("state", status_data))
         except queue.Full: logging.warning("Status queue full hiding indicator on cmd mod release.")
         # Post-processing will happen in the main loop's stop flow


# --- Config Saving Function (Moved/Duplicated for now) ---
# TODO: Refactor config loading/saving/applying into a dedicated module
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
    # --- Make managers global or pass them correctly ---
    global tooltip_mgr, status_mgr, buffered_audio_input, deepgram, start_time
    # --- Add mouse_controller to globals if needed, or ensure it's passed ---
    global mouse_controller # Add if needed by other functions
    # --- Declare other necessary globals used/modified within main ---
    global current_mode, current_command_transcript, final_source_text, typed_word_history
    global ui_interaction_cancelled, config, SELECTED_LANGUAGE, TARGET_LANGUAGE
    # --- Ensure initial_activation_pos is accessible if needed, though mainly set/used in on_click ---
    global initial_activation_pos
    global status_mgr # Ensure it's global

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
    status_mgr = StatusIndicatorManager(status_queue, ui_action_queue, PREFERRED_SOURCE_LANGUAGES, PREFERRED_TARGET_LANGUAGES)
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

    # --- Loop Variables (Initialize locals) ---
    dg_connection = None
    microphone = None
    active_mode_on_stop = None # Local: Only used within the stop flow
    last_hover_check_time = 0
    hover_check_interval = 0.1
    # start_time is handled globally

    try:
        while not systray_ui.exit_app_event.is_set(): # Check exit event
            current_time = time.time()

            # --- Handle UI Actions ---
            try:
                action_command, action_data = ui_action_queue.get_nowait()
                # logging.info(f"Received UI action: {action_command}") # Reduce log noise
                if action_command == "select_language":
                    lang_type = action_data.get("type"); new_lang = action_data.get("lang")
                    config_key = "selected_language" if lang_type == "source" else "target_language"
                    if "general" not in config: config["general"] = {}
                    config["general"][config_key] = new_lang
                    logging.info(f"UI selected {lang_type} language: {new_lang}. Updating config.")
                    save_config_local(config); apply_config(config); ui_interaction_cancelled = True
            except queue.Empty: pass
            except Exception as e: logging.error(f"Error processing UI action queue: {e}", exc_info=True)

            # --- Check Hover Position ---
            if transcription_active_event.is_set() and current_time - last_hover_check_time > hover_check_interval:
                last_hover_check_time = current_time
                try:
                    hover_pos = mouse_controller.position
                    if status_mgr and status_mgr.thread.is_alive():
                        # Send current position for hover *checking*
                        status_mgr.queue.put_nowait(("check_hover_position", hover_pos))
                except queue.Full: pass
                except Exception as e:
                    if time.time() % 5 < 0.1: logging.error(f"Error checking/sending hover position: {e}")

            # --- Start Transcription Flow ---
            if transcription_active_event.is_set() and dg_connection is None:
                # Now uses the global current_mode correctly
                logging.info(f"Activating {current_mode} mode...")
                # Clear state...
                if current_mode == "dictation": typed_word_history.clear(); final_source_text = ""
                elif current_mode == "command": current_command_transcript = ""
                try:
                    # Connect, register handlers, set options...
                    dg_connection = deepgram.listen.asyncwebsocket.v("1")
                    # ... handlers ...
                    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message) # Ensure this uses global vars correctly
                    # ... other handlers ...
                    options = LiveOptions(model="nova-2", language=SELECTED_LANGUAGE, interim_results=True, smart_format=True, # etc.
                                          encoding="linear16", channels=1, sample_rate=16000, punctuate=True, numerals=True,
                                          utterance_end_ms="1000", vad_events=True, endpointing=300)
                    await dg_connection.start(options)
                    # Send buffer...
                    pre_activation_buffer = buffered_audio_input.get_buffer()
                    # ... buffer sending logic ...
                    if pre_activation_buffer:
                        # logging.info(...)
                        for chunk in pre_activation_buffer:
                            # ... send chunk ...
                            if dg_connection and await dg_connection.is_connected():
                                try: await dg_connection.send(chunk)
                                except Exception: break
                            else: break
                        # logging.info(...)

                    # Start DG mic...
                    original_send = dg_connection.send
                    async def logging_send_wrapper(data):
                        # ... wrapper logic ...
                        is_conn_connected = False
                        if dg_connection:
                            try: is_conn_connected = await dg_connection.is_connected()
                            except Exception: pass
                        if is_conn_connected:
                            # logging.debug(...) # Reduce noise
                            try: await original_send(data)
                            except Exception: pass

                    microphone = Microphone(logging_send_wrapper)
                    microphone.start()
                    logging.info("Deepgram connection and microphone started.")

                except Exception as e:
                    # ... (Error handling for DG start) ...
                    logging.error(f"Failed to start DG/Mic: {e}", exc_info=True)
                    # ... reset state ...
                    if dg_connection:
                       try: await dg_connection.finish()
                       except Exception: pass
                    is_dictation_active.clear(); is_command_active.clear(); transcription_active_event.clear()
                    current_mode = None; dg_connection = None; microphone = None
                    initial_activation_pos = None # Reset initial pos on error
                    try: status_mgr.queue.put_nowait(("state", {"state": "hidden", "source_lang": "", "target_lang": ""}))
                    except Exception: pass

            # --- Stop Transcription Flow ---
            elif not transcription_active_event.is_set() and dg_connection is not None:
                active_mode_on_stop = current_mode # Capture global mode before reset
                logging.info(f"Deactivating {active_mode_on_stop} mode...")
                duration = current_time - start_time if start_time else 0
                start_time = None # Reset global start_time
                # Hide UI...
                try: status_mgr.queue.put_nowait(("state", {"state": "hidden", "source_lang": "", "target_lang": ""}))
                except Exception: pass
                if active_mode_on_stop == "dictation":
                    try: tooltip_mgr.queue.put_nowait(("hide", None))
                    except Exception: pass
                # Stop Mic/Conn...
                if microphone: microphone.finish(); microphone = None; logging.info("DG Mic finished.")
                if dg_connection:
                    try: await asyncio.sleep(0.1); await dg_connection.finish(); logging.info("DG Conn finished.")
                    except Exception as e: logging.error(f"Error finishing DG conn: {e}")
                    dg_connection = None
                # Check cancellation...
                perform_action = True
                if ui_interaction_cancelled: perform_action = False; ui_interaction_cancelled = False; logging.info("UI interaction cancelled.")
                elif active_mode_on_stop == "cancel": perform_action = False; logging.info("Command cancelled.")
                # Post-process / Translate...
                translation_task = None
                if perform_action:
                    if duration >= MIN_DURATION_SEC:
                        if active_mode_on_stop == "dictation":
                            if TARGET_LANGUAGE and TARGET_LANGUAGE != SELECTED_LANGUAGE and final_source_text:
                                translation_task = asyncio.create_task(translate_and_type(final_source_text.strip(), SELECTED_LANGUAGE, TARGET_LANGUAGE))
                            else: logging.info("Dictation finished. No translation.")
                        elif active_mode_on_stop == "command": execute_command(current_command_transcript)
                    else: # Discard short
                         logging.info(f"Duration < min ({MIN_DURATION_SEC}s), discarding.")
                         if active_mode_on_stop == "dictation": typed_word_history.clear(); final_source_text = ""
                else: # Clear state if cancelled
                     if active_mode_on_stop == "dictation": typed_word_history.clear(); final_source_text = ""
                     elif active_mode_on_stop == "command": current_command_transcript = ""
                # Reset state...
                current_mode = None
                current_command_transcript = ""
                # Await translation...
                if translation_task:
                    try: await translation_task
                    except Exception as e: logging.error(f"Error awaiting translation: {e}", exc_info=True)

            # --- Check Config Reload ---
            if systray_ui.config_reload_event.is_set():
                logging.info("Detected config reload request.")
                old_source = SELECTED_LANGUAGE
                config = load_config(); apply_config(config); tooltip_mgr.reload_config()
                systray_ui.config_reload_event.clear()
                if dg_connection and SELECTED_LANGUAGE != old_source:
                     logging.info("Source language changed, restarting DG...")
                     transcription_active_event.clear() # This will trigger the stop flow above

            # --- Thread Health Checks ---
            if not tooltip_mgr.thread.is_alive() and not tooltip_mgr._stop_event.is_set(): logging.error("Tooltip thread died."); break
            if not status_mgr.thread.is_alive() and not status_mgr._stop_event.is_set(): logging.error("Status Indicator thread died."); break

            await asyncio.sleep(0.05) # Main loop sleep

    except (asyncio.CancelledError, KeyboardInterrupt): logging.info("Main task cancelled/interrupted.")
    except Exception as e: logging.error(f"Unexpected error in main loop: {e}", exc_info=True)
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

if __name__ == "__main__":
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