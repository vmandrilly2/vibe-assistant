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

# --- NEW: Import ConfigManager ---
from config_manager import ConfigManager

# --- UI Managers ---
from action_confirm_ui import ActionConfirmManager
import systray_ui # Import the run function and the reload event
from audio_buffer import BufferedAudioInput
from status_indicator import StatusIndicatorManager, DEFAULT_MODES # Import DEFAULT_MODES
from tooltip_manager import TooltipManager

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

# --- Core Logic Managers/Processors ---
from keyboard_simulator import KeyboardSimulator
from openai_manager import OpenAIManager
from deepgram_manager import DeepgramManager
from dictation_processor import DictationProcessor
from command_processor import CommandProcessor

# --- Constants ---
from constants import (
    MODE_DICTATION, MODE_COMMAND, AVAILABLE_MODES,
    PYNPUT_BUTTON_MAP, PYNPUT_MODIFIER_MAP, PYNPUT_KEY_MAP,
    ALL_LANGUAGES, ALL_LANGUAGES_TARGET
)

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone, # Import Microphone class
)

# --- NEW: State Variables for Dictation Flow ---
last_interim_transcript = "" # Store the most recent interim result

# --- Initial Configuration Application (REPLACED) ---
# Instantiate ConfigManager early
config_manager = ConfigManager()

# Load environment variables (still needed for API keys)
load_dotenv()
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # --- Load OpenAI Key ---

if not DEEPGRAM_API_KEY:
    logging.critical("DEEPGRAM_API_KEY not found in environment variables or .env file. Exiting.")
    sys.exit(1)
# --- Check for OpenAI Key (warn if missing, needed for translation/commands) ---
if not OPENAI_API_KEY:
    # Check config if modules requiring OpenAI are enabled
    translation_enabled = config_manager.get("modules.translation_enabled", True)
    command_interp_enabled = config_manager.get("modules.command_interpretation_enabled", False)
    if translation_enabled or command_interp_enabled:
        logging.warning("OPENAI_API_KEY not found in environment variables or .env, but required by enabled modules (Translation/Command Interpretation). These features may fail.")
    else:
         logging.info("OPENAI_API_KEY not found, but not required by currently enabled modules.")

# --- Load Initial Translations (Conditional) --- >
initial_language = config_manager.get("general.selected_language", "en-US")
if i18n_enabled:
    load_translations(initial_language)
    logging.info(f"Initial translations loaded for language: {i18n.get_current_language()}")
else:
    logging.info("Skipping initial translation loading as i18n is disabled.")

# --- Initialize OpenAI Client (Conditional based on config) --- >
openai_client = None
openai_manager = None
# Check both API key existence AND config setting
translation_enabled = config_manager.get("modules.translation_enabled", True)
command_interp_enabled = config_manager.get("modules.command_interpretation_enabled", False)
if translation_enabled or command_interp_enabled:
    if OPENAI_API_KEY:
        try:
            openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            openai_manager = OpenAIManager(openai_client) # Instantiate the manager
            logging.info("OpenAI client and manager initialized (needed for Translation and/or Command Interpretation).")
        except Exception as e:
            logging.error(f"Failed to initialize OpenAI client or manager: {e}")
    # Warning about missing key logged earlier
else:
    logging.info("OpenAI client not initialized as Translation and Command Interpretation modules are disabled in config.")

# --- Logging Setup ---
LOG_DIR = "logs" # Define log directory
# Ensure logs directory exists
if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR)
    except OSError as e:
        print(f"Error creating log directory {LOG_DIR}: {e}")

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


# --- Initial Logging of Settings (Using ConfigManager) --- >
logging.info(f"Using Source Language: {config_manager.get('general.selected_language', 'N/A')}")
logging.info(f"Initial Active Mode: {config_manager.get('general.active_mode', 'N/A')}")
target_lang_code = config_manager.get('general.target_language', None)
openai_model_name = config_manager.get('general.openai_model', 'N/A')
if target_lang_code:
    logging.info(f"Translation Enabled: Target Language = {target_lang_code}, Model = {openai_model_name}")
else:
    logging.info("Translation Disabled (Target Language is None)")
dictation_button_name = config_manager.get('triggers.dictation_button', 'middle')
logging.info(f"Dictation Trigger: {dictation_button_name}")
command_button_name = config_manager.get('triggers.command_button', None)
command_mod_name = config_manager.get('triggers.command_modifier', None)
if command_button_name:
    mod_str = f" + {command_mod_name}" if command_mod_name else ""
    logging.info(f"Command Trigger: {command_button_name}{mod_str}")
else:
    logging.info("Command Trigger: Disabled")
# --- End Initial Logging ---


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
is_command_active = threading.Event() # Keep for potential command mode later (DEPRECATED? ACTIVE_MODE replaces?)
transcription_active_event = threading.Event() # True if any trigger is active
current_activation_id = None # <<< ID for the current transcription activation
tooltip_queue = queue.Queue()
status_queue = queue.Queue()
modifier_keys_pressed = set()
ui_action_queue = queue.Queue()
# --- Queue for Action Confirmation UI --- >
action_confirm_queue = queue.Queue()
ui_interaction_cancelled = False # Flag specifically for UI hover interactions
initial_activation_pos = None # Position where activation started
start_time = None # Timestamp when activation started

# --- State for Pending Action Confirmation --- >
g_pending_action = None      # Stores the name of the action detected (e.g., "Enter")
g_action_confirmed = False # Set by the confirmation UI via action_queue

# --- State for Dictation Typing Simulation ---
# last_simulated_text = "" # REMOVED - No longer directly used this way
typed_word_history = [] # Store history of typed words
final_source_text = "" # Store final source text from dictation *before* potential translation

# --- State for Command Mode ---
# current_command_transcript = "" # REMOVED - Use final_command_text
last_command_executed = None # For potential undo feature
final_command_text = "" # Store the transcript for command mode

# --- REFACTORED: Now calls DictationProcessor --- >
def handle_dictation_interim(dictation_processor: DictationProcessor, transcript, activation_id):
    """Handles interim dictation results by calling the processor."""
    if dictation_processor:
        dictation_processor.handle_interim(transcript, activation_id)
    else:
        logging.error("DictationProcessor instance not available in handle_dictation_interim")

# --- REFACTORED: Now calls DictationProcessor and handles returned values --- >
def handle_dictation_final(dictation_processor: DictationProcessor, final_transcript, history, activation_id):
    """Handles the final dictation transcript segment via DictationProcessor.
       Updates local state (history, pending action) based on processor results.
    """
    global g_pending_action, g_action_confirmed, final_source_text, typed_word_history # Need to update these globals
    logging.debug(f"Handling final dictation segment '{final_transcript}' via processor (Activation ID: {activation_id})")
    if dictation_processor:
        try:
            new_history, text_typed, detected_action = dictation_processor.handle_final(
                final_transcript, history, activation_id
            )
            # --- Update global state based on processor results --- >
            typed_word_history = new_history # Update history tracked in vibe_app
            final_source_text = " ".join([entry['text'] for entry in typed_word_history]) # Recalculate final source text
            if detected_action:
                logging.info(f"DictationProcessor detected action: '{detected_action}'")
                g_pending_action = detected_action # Store pending action
                g_action_confirmed = False # Reset confirmation status
            else:
                # Clear pending action if current segment has none
                if g_pending_action:
                    logging.debug("Clearing previously pending action as new final transcript has no action.")
                    g_pending_action = None
                    g_action_confirmed = False
            # Return values are not explicitly used by caller in current flow, but useful for logging/debug
            return new_history, text_typed
        except Exception as e:
            logging.error(f"Error calling DictationProcessor.handle_final: {e}", exc_info=True)
            return history, "" # Return original history on error
    else:
        logging.error("DictationProcessor instance not available in handle_dictation_final")
        return history, "" # Return original history if processor is missing

# --- Translation Function (Modified to accept config_manager) ---
async def translate_and_type(text_to_translate, source_lang_code, target_lang_code, config_mgr: ConfigManager, kb_sim: KeyboardSimulator, openai_mgr: OpenAIManager):
    """Translates text using OpenAI and types the result."""
    if not openai_mgr:
        logging.error("OpenAI Manager not available. Cannot translate.")
        if kb_sim:
            kb_sim.simulate_typing(" [Translation Error: OpenAI Manager not initialized]")
        return
    if not kb_sim:
        logging.error("KeyboardSimulator not available. Cannot type translation.")
        return
    if not config_mgr:
        logging.error("ConfigManager not available. Cannot get translation settings.")
        return

    if not text_to_translate:
        logging.warning("No text provided for translation.")
        return
    if not source_lang_code or not target_lang_code:
        logging.error(f"Missing source ({source_lang_code}) or target ({target_lang_code}) language for translation.")
        kb_sim.simulate_typing(" [Translation Error: Language missing]")
        return
    if source_lang_code == target_lang_code:
         logging.info("Source and target languages are the same, skipping translation call.")
         return

    openai_model_name = config_mgr.get("general.openai_model", "gpt-4o-mini") # Get model from config

    source_lang_name = source_lang_code # Use code for prompt simplicity
    target_lang_name = target_lang_code

    logging.info(f"Requesting translation from '{source_lang_name}' to '{target_lang_name}' for: '{text_to_translate}' using model '{openai_model_name}'")
    kb_sim.simulate_typing("-> ") # Add space after arrow

    try:
        prompt = f"Translate the following text accurately from {source_lang_name} to {target_lang_name}. Output only the translated text:\n\n{text_to_translate}"
        translated_text = await openai_mgr.get_openai_completion(
            model=openai_model_name,
            messages=[
                {"role": "system", "content": "You are an expert translation engine."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=int(len(text_to_translate) * 2.5 + 50)
        )

        if translated_text is None:
            logging.error("Failed to get translation from OpenAI.")
            kb_sim.simulate_typing(f"[Translation Error: API Call Failed]")
            return

        logging.info(f"Translation received: '{translated_text}'")
        if translated_text:
            kb_sim.simulate_typing(translated_text + " ")
        else:
            logging.warning("OpenAI returned an empty translation.")
            kb_sim.simulate_typing("[Translation Empty] ")
    except Exception as e:
        logging.error(f"Error during OpenAI translation request: {e}", exc_info=True)
        kb_sim.simulate_typing(f"[Translation Error: {type(e).__name__}] ")


# --- Pynput Listener Callbacks ---
# Keep global config_manager accessible to callbacks
def on_click(x, y, button, pressed):
    global start_time, status_queue, ui_interaction_cancelled, ui_action_queue
    global transcription_active_event, typed_word_history, final_source_text, final_command_text
    global initial_activation_pos, status_mgr # Need status_mgr for hover checks
    global g_pending_action, g_action_confirmed, action_confirm_queue
    global last_interim_transcript, current_activation_id # Need current_activation_id

    # --- Get current mode and trigger buttons from ConfigManager --- >
    active_mode = config_manager.get("general.active_mode", MODE_DICTATION)
    dictation_button_name = config_manager.get("triggers.dictation_button", "middle")
    command_button_name = config_manager.get("triggers.command_button", None)
    command_mod_name = config_manager.get("triggers.command_modifier", None)
    dictation_trigger_button = PYNPUT_BUTTON_MAP.get(dictation_button_name)
    command_trigger_button = PYNPUT_BUTTON_MAP.get(command_button_name)
    command_mod_key = PYNPUT_MODIFIER_MAP.get(command_mod_name)

    # --- Determine if this click is a valid trigger --- >
    is_trigger = False
    trigger_mode = None
    # Check dictation trigger
    if button == dictation_trigger_button:
        is_trigger = True
        trigger_mode = MODE_DICTATION
    # Check command trigger (only if different from dictation trigger)
    elif button == command_trigger_button and command_trigger_button is not None and command_trigger_button != dictation_trigger_button:
        # Check modifier if required
        if command_mod_key:
            if all(m in modifier_keys_pressed for m in ([command_mod_key] if not isinstance(command_mod_key, list) else command_mod_key)):
                 is_trigger = True
                 trigger_mode = MODE_COMMAND
        else: # No modifier required for command button
            is_trigger = True
            trigger_mode = MODE_COMMAND

    if not is_trigger:
        return # Not a relevant click event

    # --- Handle Press ---
    if pressed:
        # --- Set the *actual* active mode based on which trigger was pressed --- >
        # This overrides the config setting if both triggers are configured and command is pressed
        # TODO: Revisit this logic - should pressing command trigger *change* the mode in config?
        # For now, let's respect the trigger used for the *current* activation, but keep config mode separate.
        current_session_mode = trigger_mode # Use the mode determined by the trigger for this session

        ui_interaction_cancelled = False # Reset flag on new press
        if not transcription_active_event.is_set():
            logging.info(f"Trigger button pressed - starting mode: {current_session_mode}.")
            # Clear specific state based on the mode being activated
            if current_session_mode == MODE_DICTATION:
                typed_word_history.clear()
                final_source_text = ""
                last_interim_transcript = ""
            elif current_session_mode == MODE_COMMAND:
                final_command_text = ""

            # Set general active flag and time/pos
            transcription_active_event.set()
            start_time = time.time()
            current_activation_id = time.monotonic() # Generate unique ID for this activation
            initial_activation_pos = (x, y)
            logging.debug(f"Stored initial activation position: {initial_activation_pos} with ID: {current_activation_id}")

            # --- Send command to main loop's queue to initiate connection --- >
            try:
                ui_action_queue.put_nowait(("initiate_dg_connection", {"activation_id": current_activation_id, "mode": current_session_mode}))
                logging.debug(f"Sent initiate_dg_connection command for ID {current_activation_id} (Mode: {current_session_mode}) to main loop queue.")
            except queue.Full:
                logging.error("UI Action Queue full! Cannot send initiate_dg_connection command.")
                transcription_active_event.clear() # Cancel if queue is full

            # --- Send status update to indicator --- >
            try:
                # Send current ACTIVE_MODE (from config) and language config to status indicator
                current_source_lang = config_manager.get("general.selected_language", "en-US")
                current_target_lang = config_manager.get("general.target_language", None)
                status_data = {"state": "active", "pos": initial_activation_pos,
                               "mode": active_mode, # Display mode from config
                               "source_lang": current_source_lang,
                               "target_lang": current_target_lang,
                               "connection_status": "connecting"} # Initial connecting status
                status_queue.put_nowait(("state", status_data))
            except queue.Full: logging.warning("Status queue full showing indicator.")
            except Exception as e: logging.error(f"Error sending initial state to status indicator: {e}")
        else:
            logging.warning(f"Attempted start {current_session_mode} while already active.")

    # --- Handle Release ---
    else:
        if not transcription_active_event.is_set():
             return # Ignore release if not active

        # Check for UI Interaction (Hover Selection) FIRST
        hover_mode = None
        hover_lang_type = None
        hover_lang_code = None
        if status_mgr and hasattr(status_mgr, 'hovered_data') and status_mgr.hovered_data:
            hover_data = status_mgr.hovered_data
            if hover_data.get("type") == "mode":
                hover_mode = hover_data.get("value")
            elif hover_data.get("type") in ["source", "target"]:
                hover_lang_type = hover_data.get("type")
                hover_lang_code = hover_data.get("value")

        # Process Hover Selection if Found
        if hover_mode:
            logging.info(f"Trigger release over mode option: {hover_mode}. Selecting mode.")
            try: ui_action_queue.put_nowait(("select_mode", hover_mode))
            except queue.Full: logging.warning(f"Action queue full sending hover mode selection ({hover_mode}).")
            ui_interaction_cancelled = True
            logging.debug("Set ui_interaction_cancelled flag due to mode hover selection.")
            try:
                 selection_data = {"type": "mode", "value": hover_mode}
                 status_queue.put_nowait(("selection_made", selection_data))
            except queue.Full: logging.warning(f"Status queue full sending selection confirmation.")
            transcription_active_event.clear() # Clear event to signal stop
            return

        elif hover_lang_type and (hover_lang_code is not None or (hover_lang_type == 'target' and hover_lang_code is None)):
            logging.info(f"Trigger release over language option: Type={hover_lang_type}, Code={hover_lang_code}. Selecting language.")
            try: ui_action_queue.put_nowait(("select_language", {"type": hover_lang_type, "lang": hover_lang_code}))
            except queue.Full: logging.warning(f"Action queue full sending hover language selection.")
            ui_interaction_cancelled = True
            logging.debug("Set ui_interaction_cancelled flag due to language hover selection.")
            try:
                 selection_data = {"type": "language", "lang_type": hover_lang_type, "value": hover_lang_code}
                 status_queue.put_nowait(("selection_made", selection_data))
            except queue.Full: logging.warning(f"Status queue full sending selection confirmation.")
            transcription_active_event.clear() # Clear event to signal stop
            return

        # NO Hover Selection: Proceed with Normal Stop Flow
        else:
            # Immediately hide UI elements
            active_mode = config_manager.get("general.active_mode", MODE_DICTATION) # Get current mode for hide logic
            try:
                logging.debug("Button released (no hover selection): Sending immediate hide command.")
                if status_mgr:
                    hide_data = {"state": "hidden", "mode": active_mode, "source_lang": "", "target_lang": "", "connection_status": "idle"}
                    status_queue.put_nowait(("state", hide_data))
                if tooltip_mgr and active_mode == MODE_DICTATION: # Only hide tooltip in dictation mode
                    tooltip_queue.put_nowait(("hide", current_activation_id)) # Hide specific tooltip
            except queue.Full: logging.warning("Queue full sending immediate hide on release.")
            except Exception as e: logging.error(f"Error sending immediate hide on release: {e}")

            # Signal backend stop flow
            duration = time.time() - start_time if start_time else 0
            logging.info(f"Trigger button released (no hover selection, duration: {duration:.2f}s). Signaling backend stop. Pending Action: {g_pending_action}")
            transcription_active_event.clear() # Signal main loop stop flow is needed
            # initial_activation_pos = None # Keep pos until main loop processes stop? Or clear here? Let's clear in main loop.

def on_press(key):
    global modifier_keys_pressed, status_queue, ui_interaction_cancelled
    global transcription_active_event
    global modifier_log_buffer, modifier_log_last_time
    global g_pending_action, g_action_confirmed, action_confirm_queue

    # Log modifiers
    if key in PYNPUT_MODIFIER_MAP.values() and key is not None:
        if key not in modifier_keys_pressed:
            modifier_log_buffer.append(f"[{key} pressed]")
            modifier_keys_pressed.add(key)
            logging.debug(f"Modifier pressed: {key}. Currently pressed: {modifier_keys_pressed}")


    try:
        # Handle Esc during ANY active mode
        if transcription_active_event.is_set() and key == keyboard.Key.esc:
            active_mode = config_manager.get("general.active_mode", MODE_DICTATION) # Get current mode for logging/hiding
            logging.info(f"ESC pressed during {active_mode} - cancelling action.")
            ui_interaction_cancelled = True
            transcription_active_event.clear()
            # Hide Confirmation UI if pending
            if g_pending_action:
                try: action_confirm_queue.put_nowait(("hide", None))
                except queue.Full: pass
                g_pending_action = None
                g_action_confirmed = False
            # Hide Tooltip
            if tooltip_mgr:
                try: tooltip_queue.put_nowait(("hide", None))
                except queue.Full: pass
            # Hide Status Indicator
            if status_mgr:
                try:
                    status_data = {"state": "hidden", "mode": active_mode, "source_lang": "", "target_lang": "", "connection_status": "idle"}
                    status_queue.put_nowait(("state", status_data))
                except queue.Full: pass
    except AttributeError:
        pass
    except Exception as e:
        logging.error(f"Error in on_press handler: {e}", exc_info=True)

def on_release(key):
    global modifier_keys_pressed, modifier_log_buffer
    if key in modifier_keys_pressed:
        modifier_log_buffer.append(f"[{key} released]")
        modifier_keys_pressed.discard(key)
        logging.debug(f"Modifier released: {key}. Currently pressed: {modifier_keys_pressed}")


# --- Main Application Logic ---
async def main():
    global g_pending_action, g_action_confirmed
    global tooltip_mgr, status_mgr, buffered_audio_input, action_confirm_mgr
    global mouse_controller, keyboard_sim, openai_manager, deepgram_mgr
    global dictation_processor, command_processor # <<< CORRECTED
    global typed_word_history, final_source_text, final_command_text
    global ui_interaction_cancelled, initial_activation_pos, start_time # Keep start_time global

    # --- Instantiate ConfigManager ---
    # Already done globally: config_manager = ConfigManager()
    logging.info("Starting Vibe App...")

    # --- Module Settings from ConfigManager ---
    tooltip_enabled = config_manager.get("modules.tooltip_enabled", True)
    status_indicator_enabled = config_manager.get("modules.status_indicator_enabled", True)
    action_confirm_enabled = config_manager.get("modules.action_confirm_enabled", True)
    audio_buffer_enabled = config_manager.get("modules.audio_buffer_enabled", True)
    command_interpretation_enabled = config_manager.get("modules.command_interpretation_enabled", False)

    # --- Initialize Systray --- >
    # Pass config_manager to systray
    systray_thread = threading.Thread(target=systray_ui.run_systray, args=(systray_ui.exit_app_event, config_manager), daemon=True)
    systray_thread.start()
    logging.info("Systray UI thread started.")

    # --- Start Tooltip Manager (Conditional) --- >
    tooltip_mgr = None
    if tooltip_enabled:
        # Pass config_manager instead of initial_config dict
        tooltip_mgr = TooltipManager(tooltip_queue, transcription_active_event, config_manager) # Pass manager
        tooltip_mgr.start()
        logging.info("Tooltip Manager activé et démarré.")
    else:
        logging.info("Tooltip Manager désactivé par la configuration.")


    # --- Start Status Indicator Manager (Conditional) ---
    status_mgr = None
    if status_indicator_enabled:
        # Pass config_manager instead of config dict
        status_mgr = StatusIndicatorManager(status_queue, ui_action_queue,
                                            config_manager=config_manager, # Pass manager
                                            all_languages=ALL_LANGUAGES,
                                            all_languages_target=ALL_LANGUAGES_TARGET,
                                            available_modes=AVAILABLE_MODES)
        status_mgr.start()
        logging.info("Status Indicator Manager started.")
    else:
        logging.info("Status Indicator Manager désactivé par la configuration.")

    # --- Start Action Confirmation UI Manager (Conditional) --- >
    action_confirm_mgr = None
    if action_confirm_enabled:
        action_confirm_mgr = ActionConfirmManager(action_confirm_queue, ui_action_queue)
        action_confirm_mgr.start()
        logging.info("Action Confirmation UI Manager activé et démarré.")
    else:
        logging.info("Action Confirmation UI désactivé par la configuration.")

    # --- Start Buffered Audio Input (Conditional) --- >
    buffered_audio_input = None
    if audio_buffer_enabled:
        buffered_audio_input = BufferedAudioInput(status_queue)
        buffered_audio_input.start()
        logging.info("Buffered Audio Input activé et démarré.")
    else:
        logging.info("Buffered Audio Input désactivé par la configuration.")

    # --- Initialize Keyboard Simulator --- >
    keyboard_sim = KeyboardSimulator()
    if not keyboard_sim.kb_controller:
        logging.critical("Keyboard simulator failed to initialize. Exiting.")
        if systray_ui and systray_ui.exit_app_event:
            systray_ui.exit_app_event.set()
        return

    # --- Initialize Dictation Processor (depends on kb_sim, queues, event) --- >
    dictation_processor = DictationProcessor(
        tooltip_q=tooltip_queue,
        keyboard_sim=keyboard_sim,
        action_confirm_q=action_confirm_queue,
        transcription_active_event=transcription_active_event
    )

    # --- Initialize Command Processor (Conditional) --- >
    command_processor = None
    if command_interpretation_enabled:
        if openai_manager and keyboard_sim:
             # Pass config_manager instead of config dict
            command_processor = CommandProcessor(
                openai_manager=openai_manager,
                keyboard_sim=keyboard_sim,
                config_manager=config_manager # Pass manager
            )
        else:
            logging.warning("Command Interpretation enabled, but dependencies (OpenAI Manager or Keyboard Sim) missing. CommandProcessor not initialized.")
    else:
        logging.info("Command Interpretation disabled. CommandProcessor not initialized.")

    # --- Initialize Deepgram Client --- >
    deepgram_client = None
    try:
        # Configure Deepgram logging level if needed (e.g., logging.WARNING)
        config_dg = DeepgramClientOptions(verbose=logging.WARNING)
        deepgram_client = DeepgramClient(DEEPGRAM_API_KEY, config_dg)
        logging.info("Deepgram client initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize Deepgram client: {e}")
        if systray_ui.exit_app_event: systray_ui.exit_app_event.set()
        return

    # --- Initialize Deepgram Manager (Conditional) --- >
    deepgram_mgr = None
    if buffered_audio_input and deepgram_client: # Depends on audio buffer
         transcript_queue = queue.Queue() # Create transcript queue here
         deepgram_mgr = DeepgramManager(
             deepgram_client=deepgram_client,
             status_q=status_queue,
             transcript_q=transcript_queue,
             buffered_audio=buffered_audio_input
         )
    else:
        logging.warning("Deepgram Manager cannot be initialized (Audio Buffer or DG Client missing/disabled).")
        transcript_queue = None # Ensure queue is None if manager isn't created


    # --- Initialize pynput Controller --- >
    mouse_controller = mouse.Controller()

    # --- Start Listeners ---
    mouse_listener = mouse.Listener(on_click=on_click)
    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener.start()
    keyboard_listener.start()
    logging.info("Input listeners started.")

    # --- Loop Variables --- >
    is_stopping = False # Track if stop flow is active
    stop_initiated_time = 0 # Track when stop was first detected
    active_mode_on_stop = None # Store mode used when stop begins
    stopping_start_time = None # Store the start_time for the specific stop cycle

    # --- NEW: Store active mode for the current session --- >
    current_session_mode = None # Set when 'initiate_dg_connection' is received

    try:
        while not systray_ui.exit_app_event.is_set():
            current_time = time.time()
            stop_detected_this_cycle = False

            # --- Check if stop is signaled --- >
            if not transcription_active_event.is_set() and start_time is not None and not is_stopping:
                logging.info("Stop signal detected (transcription_active_event is clear).")
                is_stopping = True
                stop_initiated_time = current_time
                stopping_start_time = start_time # CAPTURE start_time for this stop cycle
                stop_detected_this_cycle = True
                active_mode_on_stop = current_session_mode # Use the mode from the current session for stop flow
                if not active_mode_on_stop:
                    logging.warning("Stop detected, but current_session_mode was not set! Falling back to config mode.")
                    active_mode_on_stop = config_manager.get("general.active_mode", MODE_DICTATION)
                # UI hiding moved to on_click release handler

            # --- Handle UI Actions --- >
            try:
                action_command, action_data = ui_action_queue.get_nowait()

                # --- Process Language/Mode Selection --- >
                if action_command == "select_language":
                    lang_type = action_data.get("type"); new_lang = action_data.get("lang")
                    config_key = "general.selected_language" if lang_type == "source" else "general.target_language"
                    recent_list_key = "general.recent_source_languages" if lang_type == "source" else "general.recent_target_languages"

                    config_manager.update(config_key, new_lang) # Update in memory

                    # --- Update Recent Language List using ConfigManager --- >
                    recent_list = config_manager.get(recent_list_key, [])
                    if new_lang in recent_list: recent_list.remove(new_lang)
                    recent_list.insert(0, new_lang)
                    MAX_RECENT_LANGS = 10
                    config_manager.update(recent_list_key, recent_list[:MAX_RECENT_LANGS]) # Update in memory
                    # --- End Update Recent List ---

                    logging.info(f"UI selected {lang_type} language: {new_lang}. Updating config.")
                    config_manager.save() # Save changes to file
                    systray_ui.config_reload_event.set() # Signal systray to update its menu display
                    # Reload i18n if source language changed
                    if lang_type == "source":
                         load_translations(new_lang)
                    # --- Set cancel flag --- >
                    if is_stopping: ui_interaction_cancelled = True
                    ui_interaction_cancelled = True # Keep original logic

                elif action_command == "select_mode":
                    new_mode = action_data
                    config_manager.update("general.active_mode", new_mode) # Update in memory
                    logging.info(f"UI selected mode: {new_mode}. Updating config.")
                    config_manager.save() # Save changes to file
                    systray_ui.config_reload_event.set() # Signal systray
                    # --- Set cancel flag --- >
                    if is_stopping: ui_interaction_cancelled = True
                    ui_interaction_cancelled = True # Keep original logic

                # --- Handle DG Connection Initiation --- >
                elif action_command == "initiate_dg_connection":
                    received_activation_id = action_data.get("activation_id")
                    current_session_mode = action_data.get("mode", MODE_DICTATION) # <<< STORE session mode
                    # Double check if still active and manager exists
                    if transcription_active_event.is_set() and not is_stopping and deepgram_mgr:
                        logging.info(f"Received initiate_dg_connection for ID {received_activation_id}. Starting DG manager listening.")
                        current_activation_id = received_activation_id # Set the global ID
                        # Get language from config
                        current_source_lang = config_manager.get("general.selected_language", "en-US")
                        current_dg_options = LiveOptions(
                            model="nova-2", language=current_source_lang, interim_results=True, smart_format=True,
                            encoding="linear16", channels=1, sample_rate=16000, punctuate=True, numerals=True,
                            utterance_end_ms="1000", vad_events=True, endpointing=300
                        )
                        await deepgram_mgr.start_listening(current_dg_options, received_activation_id)
                    else:
                         logging.warning(f"Ignoring initiate_dg_connection command because transcription is no longer active or stopping (ID: {received_activation_id}).")

                # --- Handle Action Confirmation Message --- >
                elif action_command == "action_confirmed":
                    confirmed_action = action_data
                    if confirmed_action == g_pending_action:
                        logging.info(f"Executing confirmed action immediately: {g_pending_action}")
                        # Execute Directly (move execution logic here or call helper)
                        if keyboard_sim:
                            if g_pending_action == "Enter":
                                keyboard_sim.simulate_key_press_release(Key.enter)
                            elif g_pending_action == "Escape":
                                keyboard_sim.simulate_key_press_release(Key.esc)
                            elif isinstance(g_pending_action, str) and len(g_pending_action) == 1:
                                keyboard_sim.simulate_typing(g_pending_action)
                            else:
                                logging.warning(f"Unhandled confirmed action type: {g_pending_action}")
                        else:
                            logging.error("Cannot execute confirmed action: KeyboardSimulator missing.")
                        # Hide UI Immediately
                        if action_confirm_mgr:
                            try: action_confirm_queue.put_nowait(("hide", None))
                            except queue.Full: logging.warning("Action confirm queue full sending hide after immediate execution.")
                        # Reset State Immediately
                        g_pending_action = None
                        g_action_confirmed = False
                    else:
                         logging.warning(f"Received confirmation for '{confirmed_action}' but '{g_pending_action}' was pending/reset.")

            except queue.Empty: pass
            except Exception as e: logging.error(f"Error processing UI action queue: {e}", exc_info=True)


            # --- Process Stop Flow --- >
            if is_stopping:
                logging.debug(f"Processing stop flow steps for {active_mode_on_stop}...")
                action_executed_this_stop = False # Initialize here to ensure it always exists
                # --- Stop Deepgram Listening --- >
                if deepgram_mgr and deepgram_mgr.is_listening:
                    logging.info("Signaling DeepgramManager to stop listening...")
                    stop_task = asyncio.create_task(deepgram_mgr.stop_listening())
                    try:
                        await asyncio.wait_for(stop_task, timeout=2.0)
                    except asyncio.TimeoutError: logging.warning("Timeout waiting for DeepgramManager to stop.")
                    except Exception as e: logging.error(f"Error during DeepgramManager stop: {e}")

                # --- Hide Tooltip (if dictation mode) --- >
                if tooltip_mgr and active_mode_on_stop == MODE_DICTATION:
                    try:
                        # Use the activation ID from the start of *this* stop cycle
                        hide_id = current_activation_id if stopping_start_time == start_time else None
                        tooltip_queue.put_nowait(("hide", hide_id))
                        logging.debug(f"Sent hide command to tooltip queue during stop flow (ID: {hide_id}).")
                    except queue.Full: logging.error("Tooltip queue full sending hide message during stop flow.")
                    except Exception as e: logging.error(f"Error sending tooltip hide message during stop flow: {e}")

                # --- Check Cancellation Flag --- >
                perform_action = True
                if ui_interaction_cancelled:
                     perform_action = False
                     ui_interaction_cancelled = False # Reset flag
                     logging.info("UI interaction cancelled during stop flow.")

                # --- Calculate Duration --- >
                duration = stop_initiated_time - stopping_start_time if stopping_start_time else 0
                min_duration = float(config_manager.get("general.min_duration_sec", 0.5))

                # --- Post-process / Translate / Execute --- >
                action_task = None
                if perform_action:
                    if duration >= min_duration:
                        logging.debug(f"Performing action post-stop for {active_mode_on_stop} (duration: {duration:.2f}s)")
                        # --- Check for Confirmed Action Execution --- >
                        action_executed_this_stop = False
                        if g_pending_action and g_action_confirmed:
                            logging.info(f"Executing confirmed action from main loop: {g_pending_action}")
                            # Logic moved to action_confirmed queue handler above
                            action_executed_this_stop = True # Mark as executed

                        # --- Normal Post-Processing (Only if no action was confirmed/executed) --- >
                        if not action_executed_this_stop:
                            if active_mode_on_stop == MODE_DICTATION:
                                current_source_lang = config_manager.get("general.selected_language")
                                current_target_lang = config_manager.get("general.target_language")
                                translation_mod_enabled = config_manager.get("modules.translation_enabled")
                                # Check if translation is needed and enabled
                                if final_source_text and current_target_lang and current_target_lang != current_source_lang and translation_mod_enabled:
                                    logging.info(f"Requesting translation post-stop for: '{final_source_text.strip()}'")
                                    action_task = asyncio.create_task(translate_and_type(
                                        final_source_text.strip(),
                                        current_source_lang,
                                        current_target_lang,
                                        config_manager, # Pass manager
                                        keyboard_sim,   # Pass simulator
                                        openai_manager  # Pass manager
                                    ))
                                elif final_source_text:
                                    logging.info("Dictation finished post-stop. No translation needed/enabled.")
                                else:
                                     logging.debug("Stop flow action check: No final_source_text for Dictation.")
                            elif active_mode_on_stop == MODE_COMMAND:
                                command_interp_mod_enabled = config_manager.get("modules.command_interpretation_enabled")
                                if command_processor and final_command_text and command_interp_mod_enabled:
                                     logging.info(f"Processing command input post-stop via CommandProcessor: '{final_command_text}'")
                                     action_task = asyncio.create_task(command_processor.process_command(final_command_text))
                                elif not command_processor: logging.error("CommandProcessor not initialized, cannot process command.")
                                elif not command_interp_mod_enabled: logging.info("Command interpretation module disabled, skipping command processing.")
                                else: logging.debug("Stop flow action check: No final_command_text for Command mode.")
                    else:
                         logging.info(f"Duration < min ({min_duration}s), discarding action post-stop for {active_mode_on_stop}.")

                # --- Clear pending action state if not executed --- >
                if g_pending_action and not action_executed_this_stop:
                     logging.debug(f"Clearing unexecuted pending action state ({g_pending_action}, confirmed={g_action_confirmed}) after stop flow.")
                     if action_confirm_mgr: # Hide UI if it was shown but action wasn't confirmed/executed
                          try: action_confirm_queue.put_nowait(("hide", None))
                          except queue.Full: pass
                     g_pending_action = None
                     g_action_confirmed = False
                elif action_executed_this_stop:
                      # Ensure state is clear even if executed via queue handler (belt and braces)
                      g_pending_action = None
                      g_action_confirmed = False


                # --- Clear state relevant to the *just completed* action --- >
                if active_mode_on_stop == MODE_DICTATION: typed_word_history.clear(); final_source_text = ""
                elif active_mode_on_stop == MODE_COMMAND: final_command_text = ""
                current_session_mode = None # Reset session mode after stop

                # --- Reset global state AFTER processing stop flow --- >
                logging.debug("Stop flow processing complete. Resetting state.")
                new_activation_occurred = (start_time != stopping_start_time)
                if new_activation_occurred:
                    logging.info(f"New activation detected during stop flow. Keeping current start_time.")
                    # Don't reset start_time, the new activation is already using it
                else:
                    logging.debug(f"Resetting start_time.")
                    start_time = None
                    initial_activation_pos = None # Clear position if no new activation
                    current_activation_id = None # Clear activation ID

                active_mode_on_stop = None
                stopping_start_time = None
                is_stopping = False # Mark stopping as complete

            # --- Check Config Reload --- >
            if systray_ui.config_reload_event.is_set():
                logging.info("Detected config reload request.")
                old_source_lang = config_manager.get("general.selected_language")
                config_manager.reload() # Reload config using the manager
                new_source_lang = config_manager.get("general.selected_language")
                # Reload translations if language changed
                if new_source_lang != old_source_lang:
                    load_translations(new_source_lang)
                    logging.info(f"Translations reloaded for {new_source_lang} due to config change.")
                # --- Signal managers to potentially update their internal state ---
                # (Currently they query config_manager when needed, but explicit reload hooks could be added)
                if tooltip_mgr: tooltip_mgr.reload_config(config_manager) # Pass manager
                if status_mgr: status_mgr.config_manager = config_manager # Update manager reference
                # CommandProcessor accesses config_manager directly
                logging.info("ConfigManager reloaded. Managers notified/updated.")
                systray_ui.config_reload_event.clear() # Clear the event

            # --- Thread Health Checks --- >
            if tooltip_enabled and tooltip_mgr and not tooltip_mgr.thread.is_alive() and not tooltip_mgr._stop_event.is_set(): logging.error("Tooltip thread died."); break
            if status_indicator_enabled and status_mgr and not status_mgr.thread.is_alive() and not status_mgr._stop_event.is_set(): logging.error("Status Indicator thread died."); break
            if action_confirm_enabled and action_confirm_mgr and not action_confirm_mgr.thread.is_alive() and not action_confirm_mgr._stop_event.is_set(): logging.error("Action Confirmation thread died."); break
            if audio_buffer_enabled and buffered_audio_input and not buffered_audio_input.thread.is_alive() and not buffered_audio_input.running.is_set(): logging.error("Buffered Audio Input thread died."); break
            # Check Deepgram Manager Task Health
            if deepgram_mgr and deepgram_mgr._connection_task and deepgram_mgr._connection_task.done():
                try:
                    exc = deepgram_mgr._connection_task.exception()
                    if exc: logging.error(f"DeepgramManager task ended with exception: {exc}", exc_info=exc); break # Exit on DG task error
                    elif deepgram_mgr.is_listening: logging.warning("DeepgramManager task finished unexpectedly."); # Consider restart?
                except asyncio.CancelledError: logging.info("DeepgramManager task was cancelled.")
                except Exception as e: logging.error(f"Error checking DeepgramManager task state: {e}")

            # --- Process Transcript Queue --- >
            if transcript_queue: # Check if queue exists
                try:
                    transcript_data = transcript_queue.get_nowait()
                    msg_type = transcript_data.get("type")
                    transcript = transcript_data.get("transcript")
                    activation_id = transcript_data.get("activation_id")

                    # Ensure we only process transcripts for the *current* activation
                    if activation_id == current_activation_id:
                        active_mode = current_session_mode # Use mode from current session
                        if msg_type == "interim":
                            if active_mode == MODE_DICTATION or active_mode == MODE_COMMAND: # Show interim for both for now
                                handle_dictation_interim(dictation_processor, transcript, activation_id)
                        elif msg_type == "final":
                            if active_mode == MODE_DICTATION:
                                 handle_dictation_final(dictation_processor, transcript, typed_word_history, activation_id)
                                 last_interim_transcript = "" # Clear interim after final
                            elif active_mode == MODE_COMMAND:
                                 final_command_text = transcript # Store final command text
                                 logging.debug(f"Stored final transcript for Command Mode: '{final_command_text}'")
                                 if tooltip_mgr: # Hide interim tooltip
                                     tooltip_queue.put_nowait(("hide", activation_id))
                    else:
                        logging.debug(f"Ignoring transcript for activation {activation_id} (current is {current_activation_id})")

                except queue.Empty: pass
                except Exception as e: logging.error(f"Error processing transcript queue: {e}", exc_info=True)

            flush_modifier_log(force=True) # Flush modifier log buffer
            await asyncio.sleep(0) # Yield control

    except (asyncio.CancelledError, KeyboardInterrupt): logging.info("Main task cancelled/interrupted.")
    finally:
        logging.info("Stopping Vibe App...")
        if not systray_ui.exit_app_event.is_set(): systray_ui.exit_app_event.set()

        # Stop Modules Conditionally
        if audio_buffer_enabled and buffered_audio_input: buffered_audio_input.stop()
        if tooltip_enabled and tooltip_mgr: tooltip_mgr.stop()
        if status_indicator_enabled and status_mgr: status_mgr.stop()
        if action_confirm_enabled and action_confirm_mgr: action_confirm_mgr.stop()
        logging.info("Component managers stop requested.")

        # Stop Deepgram Manager if it exists
        if deepgram_mgr:
             logging.info("Stopping Deepgram Manager...")
             # Ensure listening is stopped cleanly
             if deepgram_mgr.is_listening or (deepgram_mgr._connection_task and not deepgram_mgr._connection_task.done()):
                 await deepgram_mgr.stop_listening()
             logging.info("Deepgram Manager stopped.")


        # Wait for Systray
        if 'systray_thread' in locals() and systray_thread.is_alive():
            logging.info("Waiting for systray thread to exit...")
            systray_thread.join(timeout=1.0)
            if systray_thread.is_alive(): logging.warning("Systray thread did not exit cleanly.")
            else: logging.info("Systray thread finished.")

        # Stop Listeners
        if 'mouse_listener' in locals() and mouse_listener.is_alive(): mouse_listener.stop()
        if 'keyboard_listener' in locals() and keyboard_listener.is_alive(): keyboard_listener.stop()
        logging.info("Input listeners stop requested.")

        logging.info("Vibe App finished.")

# --- Add Exit Event for Systray Communication ---
systray_ui.exit_app_event = threading.Event() # Create event in main module

# --- Ensure global ConfigManager is available if needed outside main ---
# (Though ideally it should be passed around)
if 'config_manager' not in globals():
     config_manager = ConfigManager() # Ensure instance exists

# --- Copy Preferred Languages (REMOVED - Defined in constants.py) ---
# --- Define Available Modes (REMOVED - Defined in constants.py) ---

if __name__ == "__main__":
    # API Key check moved earlier
    try:
        pyautogui.FAILSAFE = True # Enable failsafe
        logging.info("PyAutoGUI FAILSAFE enabled.")
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Application interrupted by user (Ctrl+C).")
    except pyautogui.FailSafeException:
         logging.critical("PyAutoGUI FAILSAFE triggered! Exiting.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in main run: {e}", exc_info=True)