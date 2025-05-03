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
from action_confirm_ui import ActionConfirmManager

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
from i18n import get_current_language, ALL_DICTATION_REPLACEMENTS # Import replacements and confirmable set

# --- Fallback if i18n is disabled/missing ---
try:
    import i18n
    from i18n import load_translations, _
    from i18n import get_current_language, ALL_DICTATION_REPLACEMENTS
    i18n_enabled = True
except ImportError:
    logging.error("Module i18n non trouvé. L'internationalisation sera désactivée.")
    # Define dummy functions/variables if i18n is missing
    _ = lambda key, default=None, **kwargs: default if default is not None else key
    load_translations = lambda lang_code: None
    get_current_language = lambda: "en" # Default to 'en' or None?
    ALL_DICTATION_REPLACEMENTS = {}
    i18n_enabled = False
# --- End Fallback ---

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode # Import KeyCode

from keyboard_simulator import KeyboardSimulator
from openai_manager import OpenAIManager
from deepgram_manager import DeepgramManager
from dictation_processor import DictationProcessor
from command_processor import CommandProcessor
from tooltip_manager import TooltipManager

# --- NEW: Import Constants ---
from constants import (
    MODE_DICTATION, MODE_COMMAND, AVAILABLE_MODES,
    PYNPUT_BUTTON_MAP, PYNPUT_MODIFIER_MAP, PYNPUT_KEY_MAP,
    ALL_LANGUAGES, ALL_LANGUAGES_TARGET
)
# --- End Import ---

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
  },
  # --- Added modules section ---
  "modules": {
    "tooltip_enabled": True,
    "status_indicator_enabled": True,
    "action_confirm_enabled": True,
    "translation_enabled": True,
    "command_interpretation_enabled": False, # Disabled by default
    "audio_buffer_enabled": True # Added audio buffer toggle
  }
  # --- End added section ---
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
ACTIVE_MODE = MODE_DICTATION # Initialize with default

# --- NEW: State Variables for Dictation Flow ---
last_interim_transcript = "" # Store the most recent interim result
# --- REMOVED final_processed_this_session ---


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
                        # --- Handle potential None value for loaded_config[section] --- >
                        if loaded_config[section] is None:
                           loaded_config[section] = {}
                           logging.warning(f"Config section '{section}' was null, reset to empty dict.")
                        # --- End Handle potential None --- >
                        # --- NEW: Check if the loaded section is actually a dictionary before merging --- >
                        if not isinstance(loaded_config.get(section), dict):
                            logging.warning(f"Config section '{section}' is not a dictionary in the file. Resetting to default.")
                            loaded_config[section] = defaults # Reset the whole section to the default dict
                        else:
                             # Now we know loaded_config[section] IS a dictionary, proceed with merging keys
                             for key, default_value in defaults.items():
                              # --- Use .get() for safer access within section --- >
                              if key not in loaded_config[section]:
                                  loaded_config[section][key] = default_value
                # Ensure recent lists exist even if loading an old config
                if "recent_source_languages" not in loaded_config.get("general", {}):
                    loaded_config.setdefault("general", {})["recent_source_languages"] = []
                if "recent_target_languages" not in loaded_config.get("general", {}):
                    loaded_config.setdefault("general", {})["recent_target_languages"] = []
                # --- Ensure modules section exists and is merged ---
                if "modules" not in loaded_config:
                     loaded_config["modules"] = DEFAULT_CONFIG["modules"]
                else:
                     if loaded_config["modules"] is None:
                          loaded_config["modules"] = {}
                          logging.warning("Config section 'modules' was null, reset to empty dict.")
                     for key, default_value in DEFAULT_CONFIG["modules"].items():
                         if key not in loaded_config.get("modules",{}): # Safely get modules dict
                               loaded_config["modules"][key] = default_value
                # --- End Ensure modules section ---

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

def apply_config(cfg):
    """Applies the loaded configuration to the global variables."""
    global DICTATION_TRIGGER_BUTTON, COMMAND_TRIGGER_BUTTON, COMMAND_MODIFIER_KEY_STR
    global COMMAND_MODIFIER_KEY, MIN_DURATION_SEC, SELECTED_LANGUAGE
    global TOOLTIP_ALPHA, TOOLTIP_BG, TOOLTIP_FG, TOOLTIP_FONT_FAMILY, TOOLTIP_FONT_SIZE
    global TARGET_LANGUAGE, OPENAI_MODEL, ACTIVE_MODE # Add ACTIVE_MODE
        # --- Action Confirmation Globals (NEEDED HERE!) --- >
    global g_pending_action, g_action_confirmed

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
# --- Load Initial Translations (Conditional) --- >
if i18n_enabled:
    load_translations(SELECTED_LANGUAGE)
    logging.info(f"Initial translations loaded for language: {i18n.get_current_language()}")
else:
    logging.info("Skipping initial translation loading as i18n is disabled.")

# --- Initialize OpenAI Client (Conditional based on config) --- >
openai_client = None
# --- NEW: Initialize OpenAI Manager --- >
openai_manager = None
module_settings = config.get("modules", {})
# Check both API key existence AND config setting
if module_settings.get("translation_enabled", True) or module_settings.get("command_interpretation_enabled", False):
    if OPENAI_API_KEY:
        try:
            openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            # --- Instantiate the manager --- >
            openai_manager = OpenAIManager(openai_client)
            logging.info("OpenAI client and manager initialized (needed for Translation and/or Command Interpretation).")
        except Exception as e:
            logging.error(f"Failed to initialize OpenAI client or manager: {e}")
    else:
        logging.warning("OpenAI API Key missing, but required by enabled modules (Translation/Command Interpretation). These features will fail.")
else:
    logging.info("OpenAI client not initialized as Translation and Command Interpretation modules are disabled in config.")

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
current_activation_id = None # <<< NEW: ID for the current transcription activation
tooltip_queue = queue.Queue()
status_queue = queue.Queue()
modifier_keys_pressed = set()
ui_action_queue = queue.Queue()
# --- NEW: Queue for Action Confirmation UI --- >
action_confirm_queue = queue.Queue()
ui_interaction_cancelled = False # Flag specifically for UI hover interactions
initial_activation_pos = None

# --- NEW: State for Pending Action Confirmation --- >
g_pending_action = None      # Stores the name of the action detected (e.g., "Enter")
g_action_confirmed = False # Set by the confirmation UI via action_queue

# --- Keyboard Controller (REMOVED - Handled by KeyboardSimulator) ---
# kb_controller = keyboard.Controller()

# --- State for Dictation Typing Simulation ---
last_simulated_text = "" # Store the transcript corresponding to the last simulation action
typed_word_history = [] # Store history of typed words
final_source_text = "" # Store final source text from dictation *before* potential translation

# --- State for Command Mode ---
current_command_transcript = "" # Store the transcript for command mode
last_command_executed = None # For potential undo feature

# --- State for Keyboard Input Mode ---
final_command_text = "" # Renamed from final_keyboard_input_text

# --- REFACTORED: Now calls DictationProcessor --- >
def handle_dictation_interim(dictation_processor: DictationProcessor, transcript, activation_id):
    """Handles interim dictation results by displaying them in a temporary tooltip
       for a specific activation ID.
    """
    if dictation_processor:
        dictation_processor.handle_interim(transcript, activation_id)
    else:
        logging.error("DictationProcessor instance not available in handle_dictation_interim")

# --- REFACTORED: Now calls DictationProcessor and handles returned values --- >
def handle_dictation_final(dictation_processor: DictationProcessor, final_transcript, history, activation_id):
    """Handles the final dictation transcript segment based on history.
    Calls DictationProcessor to perform calculations, typing, and action detection.
    Updates local state (history, pending action) based on processor results.

    Returns:
        tuple: (updated_history_list, final_text_string_typed)
               The updated history and the text string typed by the processor.
    """
    # --- Action Confirmation Globals (Needed for updating pending action) --- >
    global g_pending_action, g_action_confirmed

    logging.debug(f"Handling final dictation segment '{final_transcript}' via processor (Activation ID: {activation_id})")

    if dictation_processor:
        try:
            # Call the processor to handle the final transcript
            new_history, text_typed, detected_action = dictation_processor.handle_final(
                final_transcript, history, activation_id
            )

            # --- Update global state if an action was detected --- >
            if detected_action:
                logging.info(f"DictationProcessor detected action: '{detected_action}'")
                # The processor should have already triggered the UI via action_confirm_queue
                # We just need to store it locally for potential later use (though UI handles confirmation)
                g_pending_action = detected_action
                g_action_confirmed = False # Reset confirmation status
            else:
                # If no action was detected by the processor, ensure pending action is cleared
                # (in case a previous segment had one that wasn't confirmed/cancelled)
                if g_pending_action:
                    logging.debug("Clearing previously pending action as new final transcript has no action.")
                    g_pending_action = None
                    g_action_confirmed = False

            # Return the results from the processor
            return new_history, text_typed

        except Exception as e:
            logging.error(f"Error calling DictationProcessor.handle_final: {e}", exc_info=True)
            # Return original history and empty typed string on error
            return history, ""
    else:
        logging.error("DictationProcessor instance not available in handle_dictation_final")
        # Return original history and empty typed string if processor is missing
        return history, ""

# --- Translation Function ---
async def translate_and_type(text_to_translate, source_lang_code, target_lang_code):
    """Translates text using OpenAI and types the result."""
    # Access the global manager instance
    global openai_manager, keyboard_sim, OPENAI_MODEL, module_settings

    # --- Check if openai_manager is available --- >
    if not openai_manager:
        logging.error("OpenAI Manager not available. Cannot translate.")
        if keyboard_sim: # Check if keyboard sim is available too
             keyboard_sim.simulate_typing(" [Translation Error: OpenAI Manager not initialized]")
        return # This return is inside the async function
    # --- End Check --- >

    if not text_to_translate:
        logging.warning("No text provided for translation.")
        return
    if not source_lang_code or not target_lang_code:
        logging.error(f"Missing source ({source_lang_code}) or target ({target_lang_code}) language for translation.")
        # Use keyboard_simulator instance
        keyboard_sim.simulate_typing(" [Translation Error: Language missing]")
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
    # Use keyboard_simulator instance
    keyboard_sim.simulate_typing("-> ") # Add space after arrow

    try:
        prompt = f"Translate the following text accurately from {source_lang_name} to {target_lang_name}. Output only the translated text:\n\n{text_to_translate}"

        # --- Call the generic method in OpenAIManager --- >
        translated_text = await openai_manager.get_openai_completion(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert translation engine."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=int(len(text_to_translate) * 2.5 + 50)
            # No response_format needed for translation
        )
        # --- End Call --- >

        # --- Check if response is None (indicating an API error) --- >
        if translated_text is None:
            logging.error("Failed to get translation from OpenAI.")
            keyboard_sim.simulate_typing(f"[Translation Error: API Call Failed]")
            return
        # --- End Check --- >

        logging.info(f"Translation received: '{translated_text}'")

        if translated_text:
            # Type the translation, followed by a space for subsequent typing
            # Use keyboard_simulator instance
            keyboard_sim.simulate_typing(translated_text + " ")
        else:
            logging.warning("OpenAI returned an empty translation.")
            # Use keyboard_simulator instance
            keyboard_sim.simulate_typing("[Translation Empty] ")

    except Exception as e:
        logging.error(f"Error during OpenAI translation request: {e}", exc_info=True)
        # Use keyboard_simulator instance
        keyboard_sim.simulate_typing(f"[Translation Error: {type(e).__name__}] ")


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
    global typed_word_history, final_source_text, final_command_text # Renamed final_keyboard_input_text
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
                logging.debug(f"Processing final dictation result: '{transcript}'")
                # --- Apply replacements BEFORE handling final --- >
                # Comment out the premature replacement call
                # processed_transcript = apply_dictation_replacements(transcript, SELECTED_LANGUAGE)
                # Pass the ORIGINAL transcript and activation ID instead of the processed one
                updated_history, text_typed_this_segment = handle_dictation_final(transcript, typed_word_history, current_activation_id)
                typed_word_history = updated_history
                # Update final_source_text based on the potentially modified history
                final_source_text = " ".join([entry['text'] for entry in typed_word_history])
                logging.debug(f"Dictation final source text updated (from history): '{final_source_text}'")
                # --- End Apply replacements --- >

                last_interim_transcript = "" # Clear interim after processing a final part
            else:
                # --- Update last interim transcript ---
                last_interim_transcript = transcript
                # --- Call interim handler (as before) ---
                # Pass the current activation ID
                handle_dictation_interim(transcript, current_activation_id)

        elif ACTIVE_MODE == MODE_COMMAND: # Renamed from MODE_KEYBOARD
            # Command mode might only care about the final result
            if result.is_final:
                 # --- Call renamed handler ---
                 # The task creation will happen in the stop flow, just store it here
                 final_command_text = transcript # Store for later processing
                 logging.debug(f"Stored final transcript for Command Mode: '{final_command_text}'")
            else:
                 # Optional: Show interim results in tooltip for command mode too?
                 handle_dictation_interim(transcript, current_activation_id) # Reuse tooltip for now
                 # pass # Or do nothing for interim in command mode

        # elif ACTIVE_MODE == MODE_COMMAND: # This logic block is now MODE_COMMAND
        #    if result.is_final: handle_command_final(transcript) # handle_command_final is now the main one
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
        # --- Add check: Only send if status_mgr exists --- >
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
    global typed_word_history, final_source_text, final_command_text # Renamed
    global SELECTED_LANGUAGE, TARGET_LANGUAGE, ACTIVE_MODE
    global initial_activation_pos
    global status_mgr
    # --- Action Confirmation Globals --- >
    global g_pending_action, g_action_confirmed, action_confirm_queue

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
            elif ACTIVE_MODE == MODE_COMMAND: final_command_text = "" # Renamed mode and variable
            # elif ACTIVE_MODE == MODE_COMMAND: current_command_transcript = "" # Already handled above

            # Set general active flag and time/pos
            transcription_active_event.set()
            start_time = time.time()
            current_activation_id = time.monotonic() # <<< NEW: Generate unique ID
            initial_activation_pos = (x, y)
            logging.debug(f"Stored initial activation position: {initial_activation_pos} with ID: {current_activation_id}")

            # --- Send START command to main loop's queue --- >
            # --- MODIFIED: Send specific command to initiate connection --- >
            try:
                # ui_action_queue.put_nowait(("start_transcription", None))
                ui_action_queue.put_nowait(("initiate_dg_connection", {"activation_id": current_activation_id}))
                # logging.debug("Sent start_transcription command to main loop queue.")
                logging.debug(f"Sent initiate_dg_connection command for ID {current_activation_id} to main loop queue.")
            except queue.Full:
                logging.error("UI Action Queue full! Cannot send initiate_dg_connection command.")
                # If queue is full, something is wrong, maybe clear the event?
                transcription_active_event.clear()

            # --- Send status update to indicator AFTER sending start command ---
            try:
                # Send current ACTIVE_MODE to status indicator
                status_data = {"state": "active", "pos": initial_activation_pos,
                               "mode": ACTIVE_MODE,
                               "source_lang": SELECTED_LANGUAGE, "target_lang": TARGET_LANGUAGE,
                               "connection_status": "connecting"} # <-- ADD initial connecting status
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
                # --- Add check: Only send if status_mgr exists --- >
                if status_mgr:
                    hide_data = {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": "",
                                 "connection_status": "idle"} # <-- Reset connection status
                    status_queue.put_nowait(("state", hide_data))
                # --- End Add check --- >
                # --- Add check: Only send if tooltip_mgr exists --- >
                if tooltip_mgr and ACTIVE_MODE == MODE_DICTATION:
                    tooltip_queue.put_nowait(("hide", None)) # Use None for general hide
                # --- End Add check --- >
            except queue.Full: logging.warning("Queue full sending immediate hide on release.")
            except Exception as e: logging.error(f"Error sending immediate hide on release: {e}")
            # --- End Immediate Hide ---

            # --- Normal Stop Flow Signal --- >
            # We still clear the event, but the main loop handles backend stop
            # only if duration is sufficient AND no action was just confirmed/executed.
            # The action execution check is now in the main loop.
            duration = time.time() - start_time if 'start_time' in globals() and start_time else 0
            logging.info(f"Trigger button released (no hover selection, duration: {duration:.2f}s). Signaling backend stop for {ACTIVE_MODE}. Pending Action: {g_pending_action}")

            # Always clear the event to signal the main loop stop flow might be needed
            transcription_active_event.clear()
            initial_activation_pos = None
            # --- REMOVED explicit hide message sending - UI is hidden above, main loop handles backend --- 

def on_press(key):
    global modifier_keys_pressed, status_queue
    global is_command_active, transcription_active_event
    global modifier_log_buffer, modifier_log_last_time
    # --- Action Confirmation Globals --- >
    global g_pending_action, g_action_confirmed, action_confirm_queue

    # Only log the first press (not repeats)
    if key in PYNPUT_MODIFIER_MAP.values() and key is not None:
        if key not in modifier_keys_pressed:
            modifier_log_buffer.append(f"[{key} pressed]")
            modifier_keys_pressed.add(key)

    try:

        # --- Handle Esc during ANY active mode (Optional: Cancel current action?) ---
        if transcription_active_event.is_set() and key == keyboard.Key.esc:
            logging.info(f"ESC pressed during {ACTIVE_MODE} - cancelling action.")
            ui_interaction_cancelled = True
            transcription_active_event.clear() 
            # --- Hide Confirmation UI if pending --- >
            if g_pending_action:
                try: action_confirm_queue.put_nowait(("hide", None))
                except queue.Full: pass
                g_pending_action = None
                g_action_confirmed = False
            # --- Hide Tooltip --- >
            try: tooltip_queue.put_nowait(("hide", None))
            except queue.Full: pass
            # --- Hide Status Indicator --- >
            try:
                status_data = {"state": "hidden", "mode": ACTIVE_MODE, "source_lang": "", "target_lang": "",
                               "connection_status": "idle"} # <-- Reset connection status
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
    # --- Action Confirmation Globals (NEEDED HERE!) --- >
    global g_pending_action, g_action_confirmed
    # Remove current_mode from globals
    global tooltip_mgr, status_mgr, buffered_audio_input, deepgram, start_time
    global mouse_controller
    # --- Add Action Confirm Manager --- >
    global action_confirm_mgr
    global current_command_transcript, final_source_text, typed_word_history
    global final_command_text # Renamed
    global ui_interaction_cancelled, config, SELECTED_LANGUAGE, TARGET_LANGUAGE, ACTIVE_MODE
    global initial_activation_pos
    global status_mgr
    # --- ADDED GLOBALS --- >
    global module_settings # Ensure module_settings is accessible if needed elsewhere, though likely read locally
    # --- Add Keyboard Simulator instance --- >
    global keyboard_sim
    # --- Add OpenAI Manager instance --- >
    global openai_manager
    # --- Add Deepgram Manager instance --- >
    global deepgram_mgr
    # --- Add Manager/Processor Instances --- >
    global keyboard_sim, dictation_processor, command_processor # <<< CORRECTED

    logging.info("Starting Vibe App...")
    # --- Read module settings early in main --- >
    module_settings = config.get("modules", {})
    logging.debug(f"Module settings read at start of main: {module_settings}")
    tooltip_setting_value = module_settings.get("tooltip_enabled", "KEY_MISSING") # Default to indicate missing key
    logging.debug(f"Value of 'tooltip_enabled' read in main: {tooltip_setting_value}")
    # --- End Read --- >

    # --- Initialize Systray --- >
    systray_thread = threading.Thread(target=systray_ui.run_systray, args=(systray_ui.exit_app_event,), daemon=True)
    systray_thread.start()
    logging.info("Systray UI thread started.")

    # --- Start Tooltip Manager (Conditional) --- >
    tooltip_mgr = None  # Initialize as None
    # Use a distinct variable name for the check
    is_tooltip_enabled = module_settings.get("tooltip_enabled", True)
    logging.debug(f"Re-checked tooltip_enabled value for IF condition: {is_tooltip_enabled}") # Add another check
    if is_tooltip_enabled:
        # --- MODIFIED: Pass initial config --- >
        tooltip_mgr = TooltipManager(tooltip_queue, transcription_active_event, config)
        tooltip_mgr.start()
        logging.info("Tooltip Manager activé et démarré.")

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

    # --- Start Action Confirmation UI Manager (Conditional) --- >
    action_confirm_mgr = None # Initialize as None
    if module_settings.get("action_confirm_enabled", True):
        action_confirm_mgr = ActionConfirmManager(action_confirm_queue, ui_action_queue)
        action_confirm_mgr.start()
        logging.info("Action Confirmation UI Manager activé et démarré.")
    else:
        logging.info("Action Confirmation UI désactivé par la configuration.")

    # --- Start Buffered Audio Input (Conditional) --- >
    buffered_audio_input = None # Initialize as None
    if module_settings.get("audio_buffer_enabled", True):
        buffered_audio_input = BufferedAudioInput(status_queue)
        buffered_audio_input.start()
        logging.info("Buffered Audio Input activé et démarré.")
    else:
        logging.info("Buffered Audio Input désactivé par la configuration. La connexion Deepgram sera désactivée.")

    # --- Initialize Keyboard Simulator --- >
    keyboard_sim = KeyboardSimulator()
    if not keyboard_sim.kb_controller:
        logging.critical("Keyboard simulator failed to initialize. Exiting.")
        # Optionally signal systray to exit?
        if systray_ui and systray_ui.exit_app_event:
            systray_ui.exit_app_event.set()
        return # Stop main function

    # --- NEW: Initialize Dictation Processor (depends on kb_sim) --- >
    dictation_processor = DictationProcessor(
        tooltip_q=tooltip_queue,
        keyboard_sim=keyboard_sim,
        action_confirm_q=action_confirm_queue,
        transcription_active_event=transcription_active_event
    )
    # --- End Init --- >

    # --- NEW: Initialize Command Processor (depends on openai_mgr, kb_sim, config) --- >
    command_processor = None # Initialize as None
    # Only initialize if the module is potentially needed and dependencies exist
    if module_settings.get("command_interpretation_enabled", False):
        if openai_manager and keyboard_sim:
            command_processor = CommandProcessor(
                openai_manager=openai_manager,
                keyboard_sim=keyboard_sim,
                config=config # Pass the whole config dict
            )
        else:
            logging.warning("Command Interpretation enabled, but dependencies (OpenAI Manager or Keyboard Sim) missing. CommandProcessor not initialized.")
    else:
        logging.info("Command Interpretation disabled. CommandProcessor not initialized.")
    # --- End Init --- >

    # --- Initialize Deepgram Client --- >
    deepgram_client = None
    try:
        config_dg = DeepgramClientOptions(verbose=logging.WARNING)
        deepgram_client = DeepgramClient(DEEPGRAM_API_KEY, config_dg)
        logging.info("Deepgram client initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize Deepgram client: {e}")
        systray_ui.exit_app_event.set() # Signal exit if DG fails
        return

    # --- NEW: Create Transcript Queue --- >
    transcript_queue = queue.Queue()

    # --- NEW: Initialize Deepgram Manager --- >
    deepgram_mgr = None
    # Only initialize if audio buffer is enabled (dependency)
    if buffered_audio_input and deepgram_client:
         deepgram_mgr = DeepgramManager(
             deepgram_client=deepgram_client,
             status_q=status_queue, # Send status updates here
             transcript_q=transcript_queue, # Send transcripts here
             buffered_audio=buffered_audio_input # Pass buffer instance
         )
    else:
        logging.warning("Deepgram Manager cannot be initialized (Audio Buffer or DG Client missing/disabled).")

    # --- Initialize pynput Controller --- >
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
                
                # --- Process Language/Mode Selection FIRST --- >
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
                
                # --- NEW: Handle DG Connection Initiation --- >
                elif action_command == "initiate_dg_connection":
                    received_activation_id = action_data.get("activation_id")
                    # Double check if still active and manager exists
                    if transcription_active_event.is_set() and not is_stopping and deepgram_mgr:
                        # --- Removed check against global ID --- >
                        logging.info(f"Received initiate_dg_connection for ID {received_activation_id}. Starting DG manager listening.")
                        # --- Set the global ID NOW, before starting the task --- >
                        current_activation_id = received_activation_id
                        # Prepare options (same as before)
                        current_dg_options = LiveOptions(
                            model="nova-2", language=SELECTED_LANGUAGE, interim_results=True, smart_format=True,
                            encoding="linear16", channels=1, sample_rate=16000, punctuate=True, numerals=True,
                            utterance_end_ms="1000", vad_events=True, endpointing=300
                        )
                        # Start the listening task using the received ID
                        await deepgram_mgr.start_listening(current_dg_options, received_activation_id)
                        # --- End changes --- >
                    else:
                         logging.warning(f"Ignoring initiate_dg_connection command because transcription is no longer active or stopping (ID: {received_activation_id}).")
                # --- End Handle DG Initiation --- >

                # --- THEN Check for Action Confirmation Message --- >
                elif action_command == "action_confirmed":
                    confirmed_action = action_data
                    if confirmed_action == g_pending_action: # Check if it matches the currently pending action
                        logging.info(f"Executing confirmed action immediately: {g_pending_action}")
                        # --- Execute Immediately --- >
                        if g_pending_action == "Enter":
                            # Use keyboard_simulator instance
                            keyboard_sim.simulate_key_press_release(Key.enter)
                        elif g_pending_action == "Escape":
                            # Use keyboard_simulator instance
                            keyboard_sim.simulate_key_press_release(Key.esc)
                        # --- NEW: Handle single characters (punctuation, etc.) --- >
                        elif isinstance(g_pending_action, str) and len(g_pending_action) == 1:
                             logging.info(f"Simulating typing for confirmed character: '{g_pending_action}'")
                             # Use keyboard_simulator instance
                             keyboard_sim.simulate_typing(g_pending_action)
                        else:
                            logging.warning(f"Unhandled confirmed action type: {g_pending_action}")
                        # --- End Handle Character --- >

                        # --- Hide UI Immediately --- >
                        try: action_confirm_queue.put_nowait(("hide", None))
                        except queue.Full: logging.warning("Action confirm queue full sending hide after immediate execution.")
                        # --- Reset State Immediately --- >
                        g_pending_action = None
                        g_action_confirmed = False # Reset confirmation flag too
                    else:
                         logging.warning(f"Received confirmation for '{confirmed_action}' but '{g_pending_action}' was pending (or already executed/reset).")
                         # Maybe hide here too if state is inconsistent?
                         # try: action_confirm_queue.put_nowait(("hide", None))
                         # except queue.Full: pass
                # --- End Action Confirmation Check --- >

            except queue.Empty: pass
            except Exception as e: logging.error(f"Error processing UI action queue: {e}", exc_info=True)

            # --- Start Transcription Flow (REMOVED - Handled by ui_action_queue 'initiate_dg_connection') --- >
            # The block below is now removed:
            # if transcription_active_event.is_set() and not is_stopping and deepgram_mgr and not deepgram_mgr.is_listening:
            #     ...
            # await deepgram_mgr.start_listening(current_dg_options, received_activation_id) # <-- REMOVED STRAY LINE
 
             # --- Process Stop Flow --- >
             # If stopping has been initiated
            if is_stopping:
                logging.debug(f"Processing stop flow steps for {active_mode_on_stop}...")
                # --- Stop Deepgram Listening via Manager --- >
                if deepgram_mgr and deepgram_mgr.is_listening:
                    logging.info("Signaling DeepgramManager to stop listening...")
                    # Run stop_listening as a task to allow it to finish gracefully
                    stop_task = asyncio.create_task(deepgram_mgr.stop_listening())
                    try:
                        await asyncio.wait_for(stop_task, timeout=2.0) # Wait max 2s for DG to close
                    except asyncio.TimeoutError:
                        logging.warning("Timeout waiting for DeepgramManager to stop.")
                    except Exception as e:
                        logging.error(f"Error during DeepgramManager stop: {e}")
                # --- End Stop DG --- >

                # --- REMOVE Manual Mic/Conn finish (Handled by DeepgramManager) --- >
                # if microphone: ...
                # await asyncio.sleep(0.15)
                # if dg_connection: ...
                # --- End Remove --- >

                # --- Hide Tooltip (Keep, managed locally) --- >
                if tooltip_mgr and active_mode_on_stop == MODE_DICTATION:
                    try:
                        hide_id = current_activation_id if stopping_start_time == start_time else None
                        tooltip_queue.put_nowait(("hide", hide_id))
                        logging.debug(f"Sent hide command to tooltip queue during stop flow (ID: {hide_id}).")
                    except queue.Full:
                        logging.error("Tooltip queue full sending hide message during stop flow.")
                    except Exception as e:
                        logging.error(f"Error sending tooltip hide message during stop flow: {e}")
                # --- End Hide Tooltip --- >

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
                        
                        # --- NEW: Check for Confirmed Action Execution --- >
                        action_executed_this_stop = False
                        if g_pending_action and g_action_confirmed:
                            logging.info(f"Executing confirmed action from main loop: {g_pending_action}")
                            if g_pending_action == "Enter":
                                # Use keyboard_simulator instance
                                keyboard_sim.simulate_key_press_release(Key.enter)
                            elif g_pending_action == "Escape":
                                # Use keyboard_simulator instance
                                keyboard_sim.simulate_key_press_release(Key.esc)
                            # Add other actions here if needed
                            action_executed_this_stop = True
                        # --- End Confirmed Action Execution Check --- >
                        
                        # --- Normal Post-Processing (Only if no action was executed) --- >
                        if not action_executed_this_stop:
                            if active_mode_on_stop == MODE_DICTATION:
                                 # Check if translation is needed
                                # --- MODIFIED: Check final_source_text before proceeding ---
                                if final_source_text and TARGET_LANGUAGE and TARGET_LANGUAGE != SELECTED_LANGUAGE:
                                    logging.info(f"Requesting translation post-stop for: '{final_source_text.strip()}'")
                                    action_task = asyncio.create_task(translate_and_type(final_source_text.strip(), SELECTED_LANGUAGE, TARGET_LANGUAGE))
                                # --- MODIFIED: Check final_source_text here too ---
                                elif final_source_text:
                                    logging.info("Dictation finished post-stop. No translation needed. Typing handled by on_message/handle_dictation_final.")
                                    # action_task remains None
                                else:
                                     logging.debug("Stop flow action check: No final_source_text for Dictation, likely connection/stop issue.")
                            elif active_mode_on_stop == MODE_COMMAND: # Renamed mode
                                 # --- REFACTORED: Call CommandProcessor --- >
                                if command_processor and final_command_text:
                                     logging.info(f"Processing command input post-stop via CommandProcessor: '{final_command_text}'")
                                     action_task = asyncio.create_task(command_processor.process_command(final_command_text))
                                elif not command_processor:
                                     logging.error("CommandProcessor not initialized, cannot process command.")
                                else:
                                     logging.debug("Stop flow action check: No final_command_text for Command mode.")
                            # --- REMOVED incorrect else block that duplicated dictation logic ---

                    else: # Discard short
                         logging.info(f"Duration < min ({MIN_DURATION_SEC}s), discarding action post-stop for {active_mode_on_stop}.")
                         # State cleared below

                # # --- Clear state relevant to the *just completed* action regardless --- >
                # # --- MODIFIED: Always clear pending action state here --- >
                # if g_pending_action:
                #      logging.debug(f"Clearing pending action state ({g_pending_action}, confirmed={g_action_confirmed}) after stop flow.")
                #      g_pending_action = None
                #      g_action_confirmed = False
                # # --- End Clear pending action state --- >
                
                if active_mode_on_stop == MODE_DICTATION: typed_word_history.clear(); final_source_text = ""
                elif active_mode_on_stop == MODE_COMMAND: final_command_text = "" 

                # --- Reset state AFTER processing stop flow --- >
                logging.debug("Stop flow processing complete. Resetting state.")

                # Check if a new activation occurred during this stop flow
                new_activation_occurred = (start_time != stopping_start_time)
                if new_activation_occurred:
                    logging.info(f"New activation detected during stop flow (current start_time {start_time} != stopping_start_time {stopping_start_time}). Keeping current start_time.")
                else:
                    # No new activation interfered, safe to reset start_time
                    logging.debug(f"Resetting start_time ({start_time}).")
                    start_time = None
                    initial_activation_pos = None

                # Always clear these state variables related to the completed stop flow
                active_mode_on_stop = None
                stopping_start_time = None

                # Finally, mark stopping as complete
                is_stopping = False

            # --- Check Config Reload --- >
            if systray_ui.config_reload_event.is_set():
                logging.info("Detected config reload request.")
                old_source = SELECTED_LANGUAGE
                config = load_config()
                apply_config(config)
                # --- MODIFIED: Pass reloaded config to tooltip manager --- >
                if tooltip_mgr:
                    tooltip_mgr.reload_config(config)
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
                    elif transcription_active_event.is_set() and deepgram_mgr and deepgram_mgr.is_listening:
                        logging.info("Source language changed while active, initiating stop to restart DG...")
                        transcription_active_event.clear() # Let the stop flow handle restart on next cycle
                        # No need to set is_stopping here, the cleared event will trigger it
                    # else: language changed but not active or stopping, no action needed now

            # --- Thread Health Checks (Conditional) --- >
            if tooltip_mgr and not tooltip_mgr.thread.is_alive() and not tooltip_mgr._stop_event.is_set(): logging.error("Tooltip thread died."); break
            if status_mgr and not status_mgr.thread.is_alive() and not status_mgr._stop_event.is_set(): logging.error("Status Indicator thread died."); break
            if action_confirm_mgr and not action_confirm_mgr.thread.is_alive() and not action_confirm_mgr._stop_event.is_set(): logging.error("Action Confirmation thread died."); break
            # --- Check Deepgram Manager Task Health (if applicable) --- >
            if deepgram_mgr and deepgram_mgr._connection_task and deepgram_mgr._connection_task.done():
                try:
                    exc = deepgram_mgr._connection_task.exception()
                    if exc:
                         logging.error(f"DeepgramManager task ended with exception: {exc}", exc_info=exc)
                         # Decide how to handle this - attempt restart? Exit?
                         break # Exit for now
                    else:
                         # Task finished without exception, but shouldn't if is_listening was true?
                         if deepgram_mgr.is_listening:
                              logging.warning("DeepgramManager task finished unexpectedly while listening was intended.")
                              # Consider attempting restart here
                              pass
                         # else: task finished after stop_listening was called, which is normal.
                except asyncio.CancelledError:
                     logging.info("DeepgramManager task was cancelled (checked in main loop).")
                except Exception as e:
                     logging.error(f"Error checking DeepgramManager task state: {e}")
            # --- End Check DG Task --- >
            if buffered_audio_input and not buffered_audio_input.thread.is_alive() and not buffered_audio_input.running.is_set(): logging.error("Buffered Audio Input thread died."); break

            # --- Process Transcript Queue --- >
            try:
                transcript_data = transcript_queue.get_nowait()
                msg_type = transcript_data.get("type")
                transcript = transcript_data.get("transcript")
                activation_id = transcript_data.get("activation_id")

                # Ensure we only process transcripts for the *current* activation
                if activation_id == current_activation_id:
                    if msg_type == "interim":
                        # --- FIXED: Pass dictation_processor first --- >
                        if ACTIVE_MODE == MODE_DICTATION or ACTIVE_MODE == MODE_COMMAND:
                            handle_dictation_interim(dictation_processor, transcript, activation_id)
                    elif msg_type == "final":
                        if ACTIVE_MODE == MODE_DICTATION:
                             updated_history, text_typed = handle_dictation_final(
                                 dictation_processor, transcript, typed_word_history, activation_id
                             )
                             typed_word_history = updated_history
                             final_source_text = " ".join([entry['text'] for entry in typed_word_history])
                             last_interim_transcript = "" # Clear interim after final
                        elif ACTIVE_MODE == MODE_COMMAND:
                             final_command_text = transcript # Store final command text
                             # Actual execution moved to stop flow
                             logging.debug(f"Stored final transcript for Command Mode: '{final_command_text}'")
                             # Maybe hide interim tooltip here if shown?
                             if tooltip_mgr:
                                 tooltip_queue.put_nowait(("hide", activation_id))
                else:
                    logging.debug(f"Ignoring transcript for activation {activation_id} (current is {current_activation_id})")

            except queue.Empty:
                pass # No transcripts in queue
            except Exception as e:
                logging.error(f"Error processing transcript queue: {e}", exc_info=True)
            # --- End Process Transcript Queue --- >

            # --- At the end of main loop, flush modifier log buffer --- >
            flush_modifier_log(force=True)

            # --- Explicitly yield control to allow background tasks --- >
            await asyncio.sleep(0)

    except (asyncio.CancelledError, KeyboardInterrupt): logging.info("Main task cancelled/interrupted.")
    finally:
        logging.info("Stopping Vibe App...")
        # Trigger exit event if not already set, to ensure systray stops cleanly
        if not systray_ui.exit_app_event.is_set():
            systray_ui.exit_app_event.set()

        # --- Stop Audio Input (Conditional) --- >
        if 'buffered_audio_input' in locals() and buffered_audio_input: buffered_audio_input.stop()

        # --- Signal GUI Managers to Stop (Conditional) --- >
        if 'tooltip_mgr' in locals() and tooltip_mgr: tooltip_mgr.stop()
        if 'status_mgr' in locals() and status_mgr: status_mgr.stop()
        if 'action_confirm_mgr' in locals() and action_confirm_mgr: action_confirm_mgr.stop()
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

        if 'deepgram_client' in locals() and deepgram_client:
            is_conn_connected_final = False
            try: is_conn_connected_final = await deepgram_client.is_connected()
            except Exception: pass # Ignore errors checking state during shutdown
            if is_conn_connected_final:
                logging.debug("Finishing Deepgram connection on exit...")
                try:
                    await deepgram_client.finish()
                    logging.info("Deepgram connection finished on exit.")
                except asyncio.CancelledError: logging.warning("Deepgram finish cancelled.")
                except Exception as e: logging.error(f"Error during final deepgram_client.finish: {e}")
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
    "Command": "Command Mode", # Renamed from Keyboard
    # Add "Command" later if needed # Comment updated
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