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
from background_audio_recorder import BackgroundAudioRecorder
from mic_ui_manager import MicUIManager, DEFAULT_MODES # Import DEFAULT_MODES
from tooltip_manager import TooltipManager
# --- NEW: Import Session Monitor --- >
from session_monitor_ui import SessionMonitor

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
from stt_manager import STTConnectionHandler
from dictation_processor import DictationProcessor

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
    if translation_enabled:
        logging.warning("OPENAI_API_KEY not found in environment variables or .env, but required by enabled Translation module. This feature may fail.")
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
if translation_enabled:
    if OPENAI_API_KEY:
        try:
            openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            openai_manager = OpenAIManager(openai_client) # Instantiate the manager
            logging.info("OpenAI client and manager initialized (needed for Translation module).")
        except Exception as e:
            logging.error(f"Failed to initialize OpenAI client or manager: {e}")
    # Warning about missing key logged earlier
else:
    logging.info("OpenAI client not initialized as Translation module is disabled in config.")

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
# --- NEW: Queue for Session Monitor UI --- >
monitor_queue = queue.Queue()

# --- NEW: Global Stats for Monitor --- >
total_successful_stops = 0
min_stop_duration = float('inf')
max_stop_duration = 0.0
total_stops_final_missed = 0 # NEW

ui_interaction_cancelled = False # Flag specifically for UI hover interactions
initial_activation_pos = None # Position where activation started
start_time = None # Timestamp when activation started

# --- NEW: State for Concurrent STT Sessions ---
MAX_CONCURRENT_SESSIONS = 4
active_stt_sessions = {} # Stores session data keyed by activation_id
# Session Data Structure: { 'handler': STTConnectionHandler, 'processor': DictationProcessor, 'buffered_transcripts': [], 'is_processing_allowed': bool, 'stop_requested': bool, 'processing_complete': bool, 'creation_time': float }
currently_processing_session_id = None # ID of the session currently allowed to process/type
sessions_waiting_for_processing = [] # List of activation_ids waiting their turn
latest_session_id = None # Track the ID of the most recently started session for UI status
typing_in_progress = threading.Event() # Event to signal if keyboard sim is busy - MAYBE USE ASYNCIO EVENT?
# --- NEW: Lock for shared session state ---
session_state_lock = asyncio.Lock()
# --- NEW: Queue for serialized typing output ---
typing_queue = asyncio.Queue()
# --- END NEW ---

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
                g_action_confirmed = False
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
        # Always use Dictation mode since Command mode is disabled
        current_session_mode = MODE_DICTATION

        ui_interaction_cancelled = False # Reset flag on new press
        if not transcription_active_event.is_set():
            logging.info(f"Trigger button pressed - starting mode: {current_session_mode}.")
            # Clear specific state based on the mode being activated
            # Clear dictation state (only mode currently)
            typed_word_history.clear() # Still need to clear this? No, it's per-session now.
            final_source_text = "" # This global is likely obsolete
            last_interim_transcript = ""

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
            if hover_data.get("type") in ["source", "target"]:
                hover_lang_type = hover_data.get("type")
                hover_lang_code = hover_data.get("value")

        # Process Hover Selection if Found
        if hover_lang_type and (hover_lang_code is not None or (hover_lang_type == 'target' and hover_lang_code is None)):
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


async def process_typing_queue():
    """Processes the typing queue one item at a time."""
    global typing_in_progress # Use the global event
    logging.info("Typing queue processor started.")
    while True:
        try:
            text_to_type = await typing_queue.get()
            logging.debug(f"Dequeued typing job: '{text_to_type}'")
            # Using a threading.Event in async code isn't ideal, but keyboard_sim is synchronous.
            # We essentially block this async task while typing happens.
            # A cleaner way might involve run_in_executor if typing is slow,
            # but let's keep it simple for now.
            if typing_in_progress.is_set():
                 logging.warning("Typing processor: Found typing_in_progress already set? Waiting...")
                 # This shouldn't happen with a single consumer queue, but as a safeguard:
                 while typing_in_progress.is_set():
                     await asyncio.sleep(0.05)

            typing_in_progress.set()
            logging.debug(f"Simulating typing: '{text_to_type}'")
            if keyboard_sim:
                # --- Simplified: Only type text, no backspace action --- >
                if isinstance(text_to_type, str):
                    keyboard_sim.simulate_typing(text_to_type)
                else:
                    logging.error(f"Typing processor received non-string data: {type(text_to_type)}")
                # Add a small delay after typing to prevent issues?
                await asyncio.sleep(0.05)
            else:
                 logging.error("Keyboard simulator not available in typing processor!")

            typing_queue.task_done() # Mark task as complete
            typing_in_progress.clear()
            logging.debug("Typing job complete.")

        except asyncio.CancelledError:
            logging.info("Typing queue processor cancelled.")
            break
        except Exception as e:
            logging.error(f"Error in typing queue processor: {e}", exc_info=True)
            # Clear the flag just in case it was set during the error
            typing_in_progress.clear()
            # Avoid tight loop on error
            await asyncio.sleep(0.1)


# --- NEW: Helper for Processing Transcripts ---
# Added tooltip_enabled parameter
async def _process_transcript_data(session_id: any, session_data: dict, transcript_data: dict, tooltip_enabled: bool):
    """Processes a single transcript data dict for the given session ID.
       Assumes lock is NOT held when called. Accesses session_data directly.
    """
    # Removed redundant check for session_id existence as it's checked before calling

    processor = session_data.get('processor')
    history = session_data.get('history') # Operate on history within session_data
    session_mode = session_data.get('mode', MODE_DICTATION)

    if processor is None or history is None:
        logging.error(f"_process_transcript_data: Missing processor or history for session {session_id}.")
        return

    msg_type = transcript_data.get("type")
    transcript = transcript_data.get("transcript")
    is_final_dg = transcript_data.get("is_final_dg")

    if msg_type == "interim" and not is_final_dg:
        if session_mode == MODE_DICTATION:
            # Handle interim for tooltip (if enabled and desired)
            # Use the passed tooltip_enabled flag
            if tooltip_mgr and tooltip_enabled:
                try:
                    # Getting position might be slow/problematic here
                    # Consider passing position if available or making tooltip simpler
                    x, y = pyautogui.position() # Potential issue
                    tooltip_queue.put_nowait(("update", (transcript, x, y, session_id)))
                    tooltip_queue.put_nowait(("show", session_id))
                except pyautogui.FailSafeException:
                    logging.warning("PyAutoGUI fail-safe triggered during interim tooltip update.")
                except queue.Full:
                    logging.warning(f"Tooltip queue full sending interim update for session {session_id}.")
                except Exception as e:
                    logging.error(f"Error sending interim update to tooltip queue: {e}")
        # Ignore interim for command mode for now

    elif msg_type == "final" or is_final_dg: # Process Deepgram finals
        if session_mode == MODE_DICTATION:
            logging.debug(f"_process_transcript_data: Processing final dictation for {session_id}...")
            try:
                # Pass the history list directly
                new_history, text_typed, detected_action = processor.handle_final(
                    final_transcript=transcript,
                    history=history, # Pass the current history list
                    activation_id=session_id
                )
                # Update the session's history IN PLACE (since history is a list)
                # This relies on handle_final potentially modifying the list or returning a new one
                # Let's assume handle_final returns the potentially modified list
                session_data['history'][:] = new_history # Replace contents

                # Queue typing job
                if text_typed:
                    try:
                        await typing_queue.put(text_typed)
                        logging.debug(f"Queued text for typing from session {session_id}: '{text_typed}'")
                    except Exception as q_err:
                        logging.error(f"Error queuing text '{text_typed}' for typing: {q_err}")

                # Handle detected action (pending action state is still global - needs review)
                if detected_action:
                    # --- MODIFIED: Only set global pending action if none is already pending ---
                    if g_pending_action is None:
                        logging.info(f"DictationProcessor detected action for {session_id}: '{detected_action}'. Setting as pending.")
                        g_pending_action = detected_action
                        g_action_confirmed = False
                    else:
                        logging.warning(f"DictationProcessor detected action '{detected_action}' for session {session_id}, but another action '{g_pending_action}' is already pending. Ignoring new action.")
                    # --- END MODIFIED ---

                # Hide tooltip after final dictation segment
                # Use the passed tooltip_enabled flag
                if tooltip_mgr and tooltip_enabled and is_final_dg: # Only hide on actual DG final?
                    try:
                        # --- NEW: Mark final transcript as received --- >
                        session_data['final_transcript_received'] = True
                        # --- END NEW ---
                        # --- NEW: Record final result time on actual Deepgram final --- >
                        if 'final_result_time' not in session_data or session_data['final_result_time'] is None:
                           session_data['final_result_time'] = time.monotonic()
                           logging.debug(f"Recorded final result time (Deepgram Final) {session_data['final_result_time']:.3f} for session {session_id}")
                        # --- END NEW ---
                        tooltip_queue.put_nowait(("hide", session_id))
                    except queue.Full:
                        logging.warning(f"Tooltip queue full sending hide on final for ID {session_id}.")
                    except Exception as e:
                        logging.error(f"Error sending hide on final to tooltip queue: {e}")

                # --- NEW: Signal processing finished on final DG message --- >
                if is_final_dg:
                    finish_event = session_data.get('processing_finished_event')
                    # --- NEW: Set flag along with event --- >
                    session_data['final_processing_complete'] = True
                    # --- END NEW ---
                    if finish_event and not finish_event.is_set():
                        finish_event.set()
                        logging.debug(f"Signaled processing_finished_event for session {session_id}")
                # --- END NEW ---

            except Exception as e:
                logging.error(f"Error calling handle_final for session {session_id}: {e}", exc_info=True)

        elif session_mode == MODE_COMMAND:
            session_data['final_command_text'] = transcript # Store final command text
            logging.debug(f"Stored final transcript for Command Mode Session {session_id}: '{transcript}'")
            # TODO: Trigger command processor execution here if needed
            # command_task = asyncio.create_task(command_processor.process_command(transcript))
            # Hide tooltip after final command segment (optional)
             # Use the passed tooltip_enabled flag
            if tooltip_mgr and tooltip_enabled and is_final_dg: # Only hide on actual DG final?
                 try:
                     tooltip_queue.put_nowait(("hide", session_id))
                 except queue.Full:
                     logging.warning(f"Tooltip queue full sending hide on final command for ID {session_id}.")
                 except Exception as e:
                     logging.error(f"Error sending hide on final command to tooltip queue: {e}")
            # --- End hide command --- >

# --- NEW: Handoff Logic Function --- >
async def _handle_session_handoff(completed_session_id: any):
    """Handles the process of activating the next waiting session.
       Assumes the session_state_lock is HELD when called.
       Focuses only on removing the completed session and activating the next.
       Disconnection and stat calculation happen elsewhere.
    """
    global currently_processing_session_id # Need to modify global

    logging.debug(f"Executing simplified session handoff logic (called for completed_session_id: {completed_session_id})")

    # --- 1. Remove the completed session --- >
    completed_session_data = active_stt_sessions.pop(completed_session_id, None) # Remove and get data
    if completed_session_data:
        logging.debug(f"Removed completed session {completed_session_id} from active_stt_sessions.")
        # --- Calculate Stats (Moved here from wait_and_cleanup for simplicity) --- >
        if completed_session_data.get('button_released'): # Only calculate if stop was initiated
            global total_successful_stops, min_stop_duration, max_stop_duration, total_stops_final_missed
            total_successful_stops += 1
            logging.info(f"Session {completed_session_id} marked as successful stop. Total: {total_successful_stops}")
            stop_time = completed_session_data.get('stop_signal_time')
            handoff_time = time.monotonic()
            if stop_time:
                duration = handoff_time - stop_time
                min_stop_duration = min(min_stop_duration, duration)
                max_stop_duration = max(max_stop_duration, duration)
                logging.info(f"Successful stop duration for {completed_session_id}: {duration:.3f}s (Min: {min_stop_duration:.3f}s, Max: {max_stop_duration:.3f}s)")
            else:
                logging.warning(f"Could not calculate stop duration for {completed_session_id}: missing stop_time.")
            # Increment final missed count if applicable
            if not completed_session_data.get('final_transcript_received'):
                total_stops_final_missed += 1
                logging.info(f"Session {completed_session_id} missed final transcript before stop. Total Missed: {total_stops_final_missed}")
        # --- END STATS CALC ---
    else:
        logging.warning(f"_handle_session_handoff: Completed session {completed_session_id} not found in active_stt_sessions during removal.")

    # --- 2. Check if the completed session was the one being processed --- >
    if currently_processing_session_id == completed_session_id:
        logging.debug(f"Session {completed_session_id} was the active processor. Clearing processing slot.")
        currently_processing_session_id = None
    else:
         logging.debug(f"Completed session {completed_session_id} was not the active processor ({currently_processing_session_id}). No change to processing slot needed now.")

    # --- 3. If the processing slot is now empty, find and activate the next waiting session --- >
    session_to_activate_data = None
    next_session_id_to_process = None
    buffered_transcripts_to_process = []

    if currently_processing_session_id is None:
        logging.debug("Processing slot is empty, checking waitlist...")
        while sessions_waiting_for_processing:
            potential_next_id = sessions_waiting_for_processing.pop(0)
            if potential_next_id in active_stt_sessions:
                next_session_id_to_process = potential_next_id
                session_to_activate_data = active_stt_sessions[next_session_id_to_process]
                currently_processing_session_id = next_session_id_to_process
                session_to_activate_data['is_processing_allowed'] = True
                # --- Get buffered transcripts --- >
                buffered_transcripts = session_to_activate_data.get('buffered_transcripts', [])
                if buffered_transcripts:
                    logging.info(f"Activating processing for {next_session_id_to_process}. Preparing to process {len(buffered_transcripts)} buffered transcript(s)...")
                    buffered_transcripts_to_process = list(buffered_transcripts)
                    session_to_activate_data['buffered_transcripts'] = [] # Clear buffer
                else:
                     logging.info(f"Activating processing for next waiting session: {next_session_id_to_process} (no buffered transcripts).")
                break # Exit loop once a valid session is found
            else:
                logging.warning(f"Session {potential_next_id} from waitlist not found in active sessions. Skipping.")
        if not next_session_id_to_process:
             logging.info("No valid sessions waiting for processing.")
    else:
        logging.debug(f"Processing slot is still occupied by {currently_processing_session_id}. No handoff activation needed now.")

    # --- Logic to release lock and process buffers remains the same ---
    # The lock will be released by the calling 'async with' context

    # Process buffered transcripts (if any) outside the lock
    if next_session_id_to_process and session_to_activate_data and buffered_transcripts_to_process:
        # processor_to_run = session_to_activate_data.get('processor') # Already retrieved
        # NOTE: Still need to handle getting tooltip_enabled flag correctly here
        local_tooltip_enabled = config_manager.get("modules.tooltip_enabled", True)
        logging.debug(f"Started processing buffered transcripts for {next_session_id_to_process} outside lock...")
        for buffered_data in buffered_transcripts_to_process:
            try:
                await _process_transcript_data(next_session_id_to_process, session_to_activate_data, buffered_data, local_tooltip_enabled)
            except Exception as e:
                logging.error(f"Error processing buffered transcript for {next_session_id_to_process}: {e}", exc_info=True)
        logging.debug(f"Finished processing buffered transcripts for {next_session_id_to_process}.")

    logging.debug("Finished simplified _handle_session_handoff logic.")

# --- NEW: Helper to Send State to Monitor --- >
async def send_state_to_monitor():
    """Safely gathers session state and sends it to the monitor queue."""
    global active_stt_sessions, currently_processing_session_id, sessions_waiting_for_processing, monitor_queue, session_state_lock
    logging.debug("Attempting to gather state for monitor...")
    async with session_state_lock:
        try:
            # Create deep copies to avoid sending references to mutable objects
            # Be mindful of complex objects within session_data if they aren't serializable or needed
            # For now, let's send a simplified snapshot
            current_active_sessions_snapshot = {}
            for act_id, data in active_stt_sessions.items():
                current_active_sessions_snapshot[act_id] = {
                    'is_processing_allowed': data.get('is_processing_allowed'),
                    'stop_requested': data.get('stop_requested'),
                    'buffered_transcripts_count': len(data.get('buffered_transcripts', [])), # Just send count
                    'processing_complete': data.get('processing_complete'),
                    'timeout_count': data.get('timeout_count', 0),
                    'is_successful_stop': data.get('is_successful_stop', False),
                    'final_transcript_received': data.get('final_transcript_received', False),
                    # --- NEW Monitor Flags ---
                    'is_active_processor': act_id == currently_processing_session_id,
                    'is_microphone_active': data.get('handler').is_microphone_active if data.get('handler') else False,
                    # --- END NEW ---
                    # Add other relevant simple fields if needed
                }
                # --- ADD LOGGING --- >
                handler_for_log = data.get('handler')
                mic_active_for_log = handler_for_log.is_microphone_active if handler_for_log else 'N/A'
                logging.debug(f"send_state_to_monitor: Session {act_id}, MicActive Flag = {mic_active_for_log}")
                # --- END LOGGING --- >

            state_snapshot = {
                'active_sessions': current_active_sessions_snapshot,
                'processing_id': currently_processing_session_id,
                'waiting_ids': list(sessions_waiting_for_processing), # Copy list
                # --- NEW: Add Global Stats --- >
                'total_successful_stops': total_successful_stops,
                'min_stop_duration': min_stop_duration if min_stop_duration != float('inf') else None, # Send None if no stops yet
                'max_stop_duration': max_stop_duration if total_successful_stops > 0 else None, # Send None if no stops yet
                'total_stops_final_missed': total_stops_final_missed # NEW
                # --- END NEW ---
            }
            monitor_queue.put_nowait(("update_state", state_snapshot))
            logging.debug("Sent state update to monitor queue.")
        except queue.Full:
            logging.warning("Monitor queue full. Skipping state update.")
        except Exception as e:
            logging.error(f"Error gathering or sending state to monitor: {e}", exc_info=True)
    logging.debug("Finished gathering state for monitor.")
# --- END Monitor Helper ---

# --- NEW: Wait and Cleanup Function ---
async def _wait_and_cleanup(session_id: any, handler: STTConnectionHandler, processing_event: asyncio.Event):
    """Waits for final processing event, disconnects, and cleans up the session."""
    if not handler or not processing_event:
        logging.error(f"_wait_and_cleanup[{session_id}]: Invalid handler or event provided.")
        return

    logging.info(f"_wait_and_cleanup[{session_id}]: Starting wait and cleanup sequence.")
    wait_timeout_sec = 30.0 # Or get from config
    event_received = False
    try:
        logging.debug(f"_wait_and_cleanup[{session_id}]: Waiting up to {wait_timeout_sec}s for final processing event...")
        await asyncio.wait_for(processing_event.wait(), timeout=wait_timeout_sec)
        event_received = True
        logging.info(f"_wait_and_cleanup[{session_id}]: Final processing event received.")
    except asyncio.TimeoutError:
        logging.warning(f"_wait_and_cleanup[{session_id}]: Timeout waiting for final processing event after {wait_timeout_sec}s.")
    except asyncio.CancelledError:
        logging.warning(f"_wait_and_cleanup[{session_id}]: Wait task cancelled.")
        # Decide if cleanup should still proceed if cancelled
    except Exception as e:
        logging.error(f"_wait_and_cleanup[{session_id}]: Error waiting for processing event: {e}", exc_info=True)

    # --- NEW: Wait for Typing Queue --- >
    if event_received: # Only wait for typing if the final transcript event was actually received
        logging.debug(f"_wait_and_cleanup[{session_id}]: Waiting for any associated typing jobs to complete...")
        try:
            # Wait for all tasks currently in the queue to be processed.
            await asyncio.wait_for(typing_queue.join(), timeout=10.0) # Timeout after 10s
            logging.debug(f"_wait_and_cleanup[{session_id}]: Typing queue joined successfully.")
        except asyncio.TimeoutError:
            logging.warning(f"_wait_and_cleanup[{session_id}]: Timeout waiting for typing queue to join.")
        except Exception as e:
            logging.error(f"_wait_and_cleanup[{session_id}]: Error waiting for typing queue join: {e}", exc_info=True)
    # --- END NEW --- 

    # --- Disconnect Handler (includes connection finish) ---
    logging.debug(f"_wait_and_cleanup[{session_id}]: Disconnecting handler...")
    disconnect_task = asyncio.create_task(handler._disconnect(), name=f"CleanupDisconnect_{session_id}")
    try:
        await asyncio.wait_for(disconnect_task, timeout=5.0) # Give disconnect a few secs
        logging.debug(f"_wait_and_cleanup[{session_id}]: Handler disconnect task completed.")
    except asyncio.TimeoutError:
        logging.warning(f"_wait_and_cleanup[{session_id}]: Timeout waiting for handler disconnect task.")
    except Exception as e:
        logging.error(f"_wait_and_cleanup[{session_id}]: Error during handler disconnect task: {e}", exc_info=True)

    # --- Signal Handler State (Internal Flags) --- >
    logging.debug(f"_wait_and_cleanup[{session_id}]: Signaling handler internal state to stop...")
    stop_listen_task = asyncio.create_task(handler.stop_listening(), name=f"CleanupStopListen_{session_id}")
    # Don't necessarily need to await this flag setting task

    # --- Trigger Session Handoff --- >
    logging.debug(f"_wait_and_cleanup[{session_id}]: Triggering session handoff...")
    async with session_state_lock:
        # Check if session still exists before handing off
        if session_id in active_stt_sessions:
            await _handle_session_handoff(session_id)
        else:
            logging.warning(f"_wait_and_cleanup[{session_id}]: Session was already removed before handoff could be triggered.")

    # --- Send Monitor Update --- >
    asyncio.create_task(send_state_to_monitor(), name=f"SendStateMonitor_Cleanup_{session_id}")

    logging.info(f"_wait_and_cleanup[{session_id}]: Cleanup sequence finished (Event Received: {event_received}).")
# --- END NEW FUNCTION ---

# --- Main Application Logic ---
async def main():
    global g_pending_action, g_action_confirmed
    global tooltip_mgr, status_mgr, buffered_audio_input, action_confirm_mgr
    global mouse_controller, keyboard_sim, openai_manager
    # --- NEW: Explicitly declare globals used within main --- >
    global currently_processing_session_id, latest_session_id, current_activation_id, active_stt_sessions, sessions_waiting_for_processing
    # --- MODIFIED: Use stt_mgr --- >
    global dictation_processor
    global ui_interaction_cancelled, initial_activation_pos, start_time # Keep start_time global
    # --- NEW: Add lock to globals potentially used in main context (though it's accessed directly) ---
    global session_state_lock
    # --- NEW: Session Monitor instance ---
    global session_monitor

    # --- Instantiate ConfigManager ---
    # Already done globally: config_manager = ConfigManager()
    logging.info("Starting Vibe App...")

    # --- Module Settings from ConfigManager ---
    tooltip_enabled = config_manager.get("modules.tooltip_enabled", True)
    status_indicator_enabled = config_manager.get("modules.status_indicator_enabled", True)
    action_confirm_enabled = config_manager.get("modules.action_confirm_enabled", True)
    audio_buffer_enabled = config_manager.get("modules.audio_buffer_enabled", True)
    # command_interpretation_enabled = config_manager.get("modules.command_interpretation_enabled", False) # REMOVED

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
        status_mgr = MicUIManager(status_queue, ui_action_queue,
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
        buffered_audio_input = BackgroundAudioRecorder(status_queue)
        buffered_audio_input.start()
        logging.info("Background Audio Recorder activé et démarré.")
    else:
        logging.info("Background Audio Recorder désactivé par la configuration.")

    # --- NEW: Start Session Monitor --- >
    session_monitor = SessionMonitor(monitor_queue, MAX_CONCURRENT_SESSIONS)
    session_monitor.start()
    logging.info("Session Monitor started.")
    # --- END NEW ---

    # --- Initialize Keyboard Simulator --- >
    keyboard_sim = KeyboardSimulator()
    if not keyboard_sim.kb_controller:
        logging.critical("Keyboard simulator failed to initialize. Exiting.")
        if systray_ui and systray_ui.exit_app_event:
            systray_ui.exit_app_event.set()
        return

    # --- Initialize Dictation Processor (depends on kb_sim, queues, event) --- >
    dictation_processor = DictationProcessor(
        keyboard_sim=keyboard_sim,
        action_confirm_q=action_confirm_queue,
        transcription_active_event=transcription_active_event
    )

    # --- Initialize Deepgram Client & STT Manager (Conditional) --- >
    deepgram_client = None
    transcript_queue = None # Initialize transcript queue

    try:
        config_dg = DeepgramClientOptions(verbose=logging.WARNING)
        deepgram_client = DeepgramClient(DEEPGRAM_API_KEY, config_dg)
        logging.info("Deepgram client initialized.")

        # --- NEW: Initialize Transcript Queue (needed for handlers) ---
        transcript_queue = queue.Queue()
        logging.info("Transcript queue initialized.")
        # STTConnectionHandler instances will be created on demand

    except Exception as e:
        logging.error(f"Failed to initialize Deepgram client: {e}")
        if systray_ui.exit_app_event: systray_ui.exit_app_event.set()
        # sys.exit(1) # Consider exiting if STT is critical
    # --- End STT Manager Initialization --- >

    # --- Initialize pynput Controller --- >
    mouse_controller = mouse.Controller()

    # --- Start Listeners ---
    mouse_listener = mouse.Listener(on_click=on_click)
    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener.start()
    keyboard_listener.start()
    logging.info("Input listeners started.")

    # --- NEW: Start Typing Queue Processor Task --- >
    typing_task = asyncio.create_task(process_typing_queue(), name="TypingProcessor")
    logging.info("Typing queue processor task created.")
    # --- END NEW ---

    # --- Loop Variables --- >
    is_stopping = False # Track if stop flow is active
    stop_initiated_time = 0 # Track when stop was first detected
    active_mode_on_stop = None # Store mode used when stop begins
    stopping_start_time = None # Store the start_time for the specific stop cycle
    # --- NEW: Store the ID being stopped ---\n    stopping_activation_id = None

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
                stopping_activation_id = current_activation_id # <<< CAPTURE the ID being stopped
                # --- NEW: Record stop signal time --- >
                current_monotonic_time = time.monotonic()
                async with session_state_lock:
                    if stopping_activation_id in active_stt_sessions:
                        active_stt_sessions[stopping_activation_id]['stop_signal_time'] = current_monotonic_time
                        logging.debug(f"Recorded stop signal time {current_monotonic_time:.3f} for session {stopping_activation_id}")
                    else:
                        logging.warning(f"Could not record stop signal time: session {stopping_activation_id} not found.")
                # --- END NEW ---
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

                    # --- Acquire lock before checking/modifying shared state ---
                    async with session_state_lock:
                        # --- Check if we can start a new session ---
                        if len(active_stt_sessions) >= MAX_CONCURRENT_SESSIONS:
                            logging.warning(f"Max concurrent sessions ({MAX_CONCURRENT_SESSIONS}) reached. Ignoring new activation {received_activation_id}.")
                            # Optional: Send some feedback to UI?
                            # Mark transcription_active_event based on received ID matching current?
                            # This part needs care - how do we know which activation failed?
                            # Maybe just log and rely on the user releasing the button
                            # transcription_active_event.clear() # Careful with this
                            # Clean up global state related to this aborted activation attempt
                            # start_time = None # Also careful - might belong to another active session
                            # current_activation_id = None # Careful
                            logging.debug("Ignoring initiation due to max sessions reached. State modifications skipped inside lock.")
                            continue # Continue the main loop

                        # --- Proceed with creating session (still inside lock) ---
                        logging.info(f"Creating new STT session for ID {received_activation_id} (Mode: {current_session_mode}). Total active: {len(active_stt_sessions) + 1}")

                        # Get language/options from config for this session
                        current_source_lang = config_manager.get("general.selected_language", "en-US")
                        current_dg_options = LiveOptions(
                            model="nova-2", language=current_source_lang, interim_results=True, smart_format=True,
                            encoding="linear16", channels=1, sample_rate=16000, punctuate=True, numerals=True,
                            utterance_end_ms="1000", vad_events=True, endpointing=300
                        )

                        # Create Handler & Processor for this session
                        new_handler = STTConnectionHandler(
                            activation_id=received_activation_id,
                            stt_client=deepgram_client,
                            status_q=status_queue, # Still pass status_q for potential UI updates
                            transcript_q=transcript_queue,
                            ui_action_q=ui_action_queue, # <<< PASS ui_action_queue
                            background_recorder=buffered_audio_input,
                            options=current_dg_options
                        )
                        new_processor = DictationProcessor(
                            keyboard_sim=keyboard_sim,
                            action_confirm_q=action_confirm_queue,
                            transcription_active_event=transcription_active_event # Is this event still needed by processor?
                        )
                        new_history = [] # Create a new history list for this processor instance

                        creation_time = time.monotonic()
                        latest_session_id = received_activation_id # Update latest session ID

                        # Determine if this new session can process immediately
                        can_process_now = (currently_processing_session_id is None)

                        session_data = {
                            'handler': new_handler,
                            'processor': new_processor,
                            'history': new_history, # Store history here
                            'mode': current_session_mode,
                            'buffered_transcripts': [],
                            'is_processing_allowed': can_process_now,
                            'stop_requested': False,
                            'processing_complete': False,
                            'creation_time': creation_time,
                            'final_command_text': "", # Add placeholder for command mode text
                            # --- NEW: Monitor Stats ---
                            'timeout_count': 0,
                            'stop_signal_time': None,
                            'final_result_time': None,
                            'is_successful_stop': False,
                            'final_transcript_received': False, # NEW
                            'processing_finished_event': asyncio.Event(), # NEW
                            # --- NEW: State Flags ---
                            'button_released': False,
                            'final_processing_complete': False,
                            # --- END NEW ---
                            # --- NEW: Recording State ---
                            'is_recording': False,
                            # --- END NEW ---
                            # --- END NEW ---
                        }
                        active_stt_sessions[received_activation_id] = session_data

                        # Assign processing slot or add to waitlist
                        if can_process_now:
                            logging.debug(f"Session {received_activation_id} starting and processing immediately.")
                            currently_processing_session_id = received_activation_id
                        else:
                            logging.debug(f"Session {received_activation_id} starting but must wait for {currently_processing_session_id} to finish.")
                            sessions_waiting_for_processing.append(received_activation_id)
                            # Keep sorted by creation time
                            sessions_waiting_for_processing.sort(key=lambda act_id: active_stt_sessions.get(act_id, {}).get('creation_time', float('inf')))

                    # --- Start the STT connection task *outside* the lock --- >
                    # Ensure handler was created before lock was released
                    if received_activation_id in active_stt_sessions:
                        # --- NEW: Send state update AFTER adding session ---
                        asyncio.create_task(send_state_to_monitor(), name=f"SendStateMonitor_{received_activation_id}")
                        # --- END NEW ---
                        handler_to_start = active_stt_sessions[received_activation_id].get('handler')
                        if handler_to_start:
                            asyncio.create_task(handler_to_start.start_listening(), name=f"STTHandler_{received_activation_id}")
                        else:
                            logging.error(f"Failed to start handler for {received_activation_id}: Handler object missing after lock release.")
                    else:
                         logging.warning(f"Session {received_activation_id} disappeared before handler could be started.")


                # --- Handle Action Confirmation Message --- >
                elif action_command == "action_confirmed":
                    # This part doesn't directly modify session state, lock not needed here
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

                # --- NEW: Handle Connection Status Update from Handlers --- >
                elif action_command == "connection_update":
                    status_data = action_data
                    status_activation_id = status_data.get("activation_id")
                    new_status = status_data.get("status", "idle")

                    logging.debug(f"Received connection update: ID={status_activation_id}, Status={new_status}")

                    # --- Forward status to UI ONLY if it's from the latest session (no lock needed for latest_session_id check) ---
                    if status_activation_id == latest_session_id:
                        if status_mgr:
                            try:
                                # Pass the simplified status along
                                ui_status_data = {"status": new_status}
                                status_queue.put_nowait(("connection_update", ui_status_data))
                            except queue.Full:
                                logging.warning(f"Status queue full sending UI update for latest session {latest_session_id}.")
                        else:
                            logging.debug("Status Indicator disabled, not forwarding status.")

                    # --- Handle session completion on disconnect/error --- >
                    if new_status in ["disconnected", "error"]:
                        logging.debug(f"Handling disconnect/error for session {status_activation_id}...")
                        async with session_state_lock:
                            if status_activation_id and status_activation_id in active_stt_sessions:
                                logging.info(f"Detected disconnect/error for session {status_activation_id}. Marking as complete and triggering handoff check.")
                                session_data = active_stt_sessions.get(status_activation_id)
                                if session_data:
                                    session_data['processing_complete'] = True
                                    # Call handoff logic, passing the ID of the session that just completed.
                                    # The handoff function will handle removal and potentially activating the next session.
                                    # It will release the lock itself before processing buffers.
                                    await _handle_session_handoff(status_activation_id)
                                    # --- NEW: Send state update AFTER handoff logic --- >
                                    asyncio.create_task(send_state_to_monitor(), name=f"SendStateMonitor_Handoff_{status_activation_id}")
                                    # --- END NEW ---
                                else:
                                    logging.warning(f"Cannot mark session {status_activation_id} complete or handoff: not found in active_stt_sessions within lock.")
                                    # Lock is released here automatically by 'async with'
                            elif status_activation_id:
                                logging.debug(f"Received disconnect/error for session {status_activation_id}, but it was not found in active_stt_sessions (might have already been handled).")
                                # Lock is released here
                            else:
                                logging.warning("Received disconnect/error status update without a valid activation_id.")
                                # Lock is released here
                        logging.debug(f"Finished handling disconnect/error for session {status_activation_id}.")


                elif action_command == "selection_made":
                    logging.debug(f"StatusIndicator received selection_made: {action_data}")
                    if not action_data:
                        logging.error("Invalid selection_made data received.")
                        return

                    type = action_data.get("type")
                    value = action_data.get("value")
                    activation_id = action_data.get("activation_id")

                    if type == "mode":
                        logging.info(f"UI selected mode: {value}")
                        config_manager.update("general.active_mode", value)
                        config_manager.save()
                        systray_ui.config_reload_event.set()
                        if is_stopping: ui_interaction_cancelled = True
                        ui_interaction_cancelled = True
                    elif type == "language":
                        lang_type = action_data.get("lang_type")
                        lang = action_data.get("lang")
                        logging.info(f"UI selected language: {lang_type} = {lang}")
                        config_manager.update(f"general.{lang_type}_language", lang)
                        config_manager.save()
                        systray_ui.config_reload_event.set()
                        if is_stopping: ui_interaction_cancelled = True
                        ui_interaction_cancelled = True
                    else:
                        logging.error(f"Unknown selection type: {type}")

                # --- NEW: Handle Connection Timeout Message --- >
                elif action_command == "connection_timeout":
                    timeout_activation_id = action_data.get("activation_id")
                    if timeout_activation_id:
                        async with session_state_lock:
                            if timeout_activation_id in active_stt_sessions:
                                active_stt_sessions[timeout_activation_id]['timeout_count'] = active_stt_sessions[timeout_activation_id].get('timeout_count', 0) + 1
                                logging.info(f"Incremented timeout count for session {timeout_activation_id}. New count: {active_stt_sessions[timeout_activation_id]['timeout_count']}")
                                # Trigger state update for monitor
                                asyncio.create_task(send_state_to_monitor(), name=f"SendStateMonitor_Timeout_{timeout_activation_id}")
                            else:
                                logging.warning(f"Received connection_timeout for unknown/inactive session: {timeout_activation_id}")
                    else:
                         logging.warning("Received connection_timeout message without an activation_id.")
                # --- END NEW ---

                # --- NEW: Handle Mic Status Update --- >
                elif action_command == "mic_status_update":
                    mic_activation_id = action_data.get("activation_id")
                    mic_active = action_data.get("mic_active")
                    if mic_activation_id:
                        logging.debug(f"Received mic_status_update for {mic_activation_id}: {mic_active}. Triggering monitor update.")
                        asyncio.create_task(send_state_to_monitor(), name=f"SendStateMonitor_Mic_{mic_activation_id}")
                    else:
                        logging.warning("Received mic_status_update message without an activation_id.")
                # --- END NEW ---

            except queue.Empty: pass
            except Exception as e: logging.error(f"Error processing UI action queue: {e}", exc_info=True)


            # --- Process Stop Flow --- >
            if is_stopping:
                logging.debug(f"Processing stop flow steps for {active_mode_on_stop}...")

                # --- Get handler and event --- >
                handler_to_stop = None
                session_exists_for_stop = False
                processing_finished_event = None
                async with session_state_lock:
                    if stopping_activation_id and stopping_activation_id in active_stt_sessions:
                        session_to_stop = active_stt_sessions[stopping_activation_id]
                        # --- NEW: Set button_released flag --- >
                        session_to_stop['button_released'] = True
                        # --- END NEW ---
                        handler_to_stop = session_to_stop.get('handler')
                        processing_finished_event = session_to_stop.get('processing_finished_event')
                        session_exists_for_stop = True
                    elif stopping_activation_id:
                        logging.warning(f"Stop flow: Session {stopping_activation_id} not found in active_stt_sessions (inside lock).")
                    else:
                        logging.warning("Stop flow: stopping_activation_id was not set.")

                # --- Trigger Cleanup Task --- >
                if session_exists_for_stop and handler_to_stop and processing_finished_event:
                    logging.info(f"Session {stopping_activation_id}: Button released. Stopping Mic, Sending Close, Launching background cleanup...")
                    # --- RE-ADD direct stop calls --- >
                    # 1. Stop microphone immediately (and wait briefly)
                    logging.debug(f"Session {stopping_activation_id}: Stopping microphone...")
                    stop_mic_task = asyncio.create_task(handler_to_stop.stop_microphone(), name=f"StopMic_{stopping_activation_id}")
                    try:
                        await asyncio.wait_for(stop_mic_task, timeout=1.0) # Give mic stop a second
                        logging.debug(f"Session {stopping_activation_id}: Microphone stop task completed.")
                    except asyncio.TimeoutError:
                        logging.warning(f"Session {stopping_activation_id}: Timeout waiting for microphone stop task.")
                    except Exception as e:
                        logging.error(f"Session {stopping_activation_id}: Error waiting for microphone stop task: {e}", exc_info=True)

                    # 2. Send CloseStream (Fire and forget)
                    logging.debug(f"Session {stopping_activation_id}: Sending CloseStream...")
                    asyncio.create_task(handler_to_stop.send_close_stream(), name=f"SendCloseStream_{stopping_activation_id}")
                    await asyncio.sleep(0.05) # Tiny sleep to allow send
                    # --- END RE-ADD direct stop calls ---

                    # 3. Launch background task for waiting and final cleanup
                    logging.debug(f"Session {stopping_activation_id}: Launching background wait-and-cleanup task...") # Adjusted log
                    asyncio.create_task(_wait_and_cleanup(stopping_activation_id, handler_to_stop, processing_finished_event), name=f"WaitCleanup_{stopping_activation_id}")
                elif session_exists_for_stop:
                    logging.warning(f"Session {stopping_activation_id}: Cannot launch cleanup task. Missing handler or event.")

                # --- Reset main loop stop flags immediately --- >
                # The actual session cleanup happens in the background task.
                logging.debug("Main loop stop flow flags reset.")
                is_stopping = False
                # --- NEW: Reset start_time/ID only if no new activation occurred --- >
                if start_time == stopping_start_time: # Check if start_time hasn't changed
                    start_time = None
                    current_activation_id = None
                    # Clear other related state if necessary (e.g., initial_activation_pos?)
                else:
                    logging.info("New activation started during stop flow; not resetting start_time/current_id.")
                # --- END NEW ---
                # Clear context vars for the next cycle
                active_mode_on_stop = None
                stopping_start_time = None
                stopping_activation_id = None

            # --- End Stop Flow --- <

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
            if audio_buffer_enabled and buffered_audio_input and not buffered_audio_input.thread.is_alive() and not buffered_audio_input.running.is_set(): logging.error("Background Audio Recorder thread died."); break
            # --- MODIFIED: Check stt_mgr Task Health --- >
            # --- REMOVED: Old STT Manager Task Health Check ---
            # if stt_mgr and stt_mgr._connection_task and stt_mgr._connection_task.done():
            #     try:
            #         exc = stt_mgr._connection_task.exception()
            #         if exc: logging.error(f"STTManager task ended with exception: {exc}", exc_info=exc); break # Exit on STT task error
            #         elif stt_mgr.is_listening: logging.warning("STTManager task finished unexpectedly."); # Consider restart?
            #     except asyncio.CancelledError: logging.info("STTManager task was cancelled.")
            #     except Exception as e: logging.error(f"Error checking STTManager task state: {e}")
            # --- NEW: Check individual handler tasks? ---
            # TODO: Need logic to iterate through active_stt_sessions and check health of each handler's _connection_task
            # --- End Placeholder ---

            # --- NEW: Check individual handler tasks? ---
            # Check health of individual STT handlers
            try:
                # Iterate over a copy of the keys to allow safe removal within the loop if needed
                active_ids_to_check = []
                async with session_state_lock: # Protect access to keys
                    active_ids_to_check = list(active_stt_sessions.keys())

                completed_by_error = [] # Collect IDs that failed
                for session_id in active_ids_to_check:
                    handler = None
                    task = None
                    # Re-acquire lock briefly to get handler and check if still valid
                    async with session_state_lock:
                        if session_id in active_stt_sessions:
                            session_data = active_stt_sessions[session_id]
                            handler = session_data.get('handler')
                        else:
                            continue # Session removed by other logic

                    if handler and handler._connection_task and handler._connection_task.done():
                        task = handler._connection_task
                        try:
                            exc = task.exception()
                            if exc:
                                logging.error(f"STT Handler task for session {session_id} ended with exception: {exc}", exc_info=exc)
                                completed_by_error.append(session_id)
                            elif handler.is_listening:
                                logging.warning(f"STT Handler task for session {session_id} finished unexpectedly.")
                                completed_by_error.append(session_id) # Treat as error/completion
                        except asyncio.CancelledError:
                            logging.info(f"STT Handler task for session {session_id} was cancelled (detected in health check). Session might be stopping normally.")
                            # If it wasn't marked complete yet, treat it as completed now
                            async with session_state_lock:
                                if session_id in active_stt_sessions and not active_stt_sessions[session_id].get('processing_complete'):
                                    logging.warning(f"Session {session_id} cancelled but not marked complete. Forcing completion.")
                                    completed_by_error.append(session_id)
                        except Exception as e:
                            logging.error(f"Error checking STT Handler task state for session {session_id}: {e}")

                # Process handoffs for sessions that completed due to error/unexpected stop
                if completed_by_error:
                    logging.debug(f"Processing handoffs for {len(completed_by_error)} sessions completed by error/unexpected stop.")
                    async with session_state_lock:
                        for errored_session_id in completed_by_error:
                             if errored_session_id in active_stt_sessions:
                                 session_data = active_stt_sessions[errored_session_id]
                                 if not session_data.get('processing_complete'):
                                      session_data['processing_complete'] = True
                                      if session_data.get('handler'):
                                           session_data['handler'].is_listening = False # Ensure handler state is correct
                                      # Trigger handoff logic
                                      await _handle_session_handoff(errored_session_id)
                                 else:
                                     logging.debug(f"Session {errored_session_id} was already marked complete, skipping handoff trigger in health check.")
                             else:
                                 logging.debug(f"Errored session {errored_session_id} was already removed, skipping handoff trigger in health check.")
                    logging.debug("Finished processing handoffs for errored sessions.")

            except Exception as e:
                 logging.error(f"Error during STT handler health check loop: {e}", exc_info=True)
            # --- End Placeholder ---

            # --- Process Transcript Queue --- >
            if transcript_queue: # Check if queue exists
                try:
                    transcript_data = transcript_queue.get_nowait()
                    msg_type = transcript_data.get("type")
                    transcript = transcript_data.get("transcript")
                    activation_id = transcript_data.get("activation_id")
                    is_final_dg = transcript_data.get("is_final_dg") # Get Deepgram final flag

                    should_process_now = False
                    session_data_for_processing = None
                    buffer_transcript = False

                    async with session_state_lock:
                        if activation_id in active_stt_sessions:
                            session_data = active_stt_sessions[activation_id]
                            if session_data.get('is_processing_allowed'):
                                should_process_now = True
                                session_data_for_processing = session_data # Keep ref for processing outside lock
                            else:
                                # Buffer it if session exists but not allowed to process
                                session_data['buffered_transcripts'].append(transcript_data)
                                buffer_transcript = True
                                logging.debug(f"Buffered transcript ({msg_type}, final_dg={is_final_dg}) for waiting session {activation_id}")
                        else:
                            # Session doesn't exist (already completed/removed?)
                            logging.debug(f"Ignoring transcript ({msg_type}, final_dg={is_final_dg}) for inactive/unknown activation ID: {activation_id}")
                            # No action needed, lock released

                    # --- Process or handle tooltip *outside* the lock ---
                    if should_process_now and session_data_for_processing:
                        logging.debug(f"Processing transcript ({msg_type}, final_dg={is_final_dg}) for active session {activation_id}")
                        # Pass tooltip_enabled flag
                        await _process_transcript_data(activation_id, session_data_for_processing, transcript_data, tooltip_enabled)
                    elif not buffer_transcript and not should_process_now:
                        # This case handles transcripts for sessions that *just* finished and were removed
                        # or interim transcripts for sessions that are not the currently processing one (if we decide to show tooltips only for the active one)
                        # Currently, the logic above handles the \"removed\" case by logging and ignoring.
                        # Let's consider if interim tooltips for non-active sessions are needed.
                        # For now, only the active session calls _process_transcript_data which handles tooltips.
                        pass
                        # Optional: Handle interim tooltips for non-active sessions here if desired

                except queue.Empty: pass
                except Exception as e: logging.error(f"Error processing transcript queue: {e}", exc_info=True)

            flush_modifier_log(force=True) # Flush modifier log buffer
            await asyncio.sleep(0) # Yield control

    except (asyncio.CancelledError, KeyboardInterrupt): logging.info("Main task cancelled/interrupted.")
    finally:
        logging.info("Stopping Vibe App...")
        if not systray_ui.exit_app_event.is_set(): systray_ui.exit_app_event.set()

        # --- NEW: Explicitly disconnect active handlers FIRST --- >
        logging.info("Explicitly disconnecting any remaining STT handlers...")
        disconnect_tasks = []
        # Create a copy of values to avoid issues if dictionary changes during iteration
        handlers_to_disconnect = []
        try:
            # No need for lock here if we just copy values quickly?
            # Let's add lock for safety accessing the dict.
            async with session_state_lock:
                handlers_to_disconnect = list(active_stt_sessions.values()) # Get list of session_data dicts
        except Exception as lock_err:
            logging.error(f"Error acquiring session lock during final disconnect: {lock_err}")

        active_handlers = [sd.get('handler') for sd in handlers_to_disconnect if sd.get('handler')]

        if active_handlers:
            logging.debug(f"Found {len(active_handlers)} handlers to disconnect explicitly.")
            for handler in active_handlers:
                if handler:
                     # Use a relatively short timeout per handler for disconnect
                     disconnect_tasks.append(asyncio.create_task(handler._disconnect(), name=f"FinalDisconnect_{handler.activation_id}"))
            if disconnect_tasks:
                try:
                    await asyncio.wait(disconnect_tasks, timeout=5.0) # Overall timeout
                    logging.info("Explicit handler disconnection attempts finished.")
                except asyncio.TimeoutError:
                     logging.warning("Timeout waiting for explicit handler disconnections.")
                except Exception as e:
                    logging.error(f"Error during explicit handler disconnection wait: {e}", exc_info=True)
        else:
             logging.debug("No active handlers found needing explicit disconnect.")
        active_stt_sessions.clear() # Clear sessions after attempting disconnect
         # --- END Explicit Disconnect --- >

        # --- Cancel Typing Task --- >
        if 'typing_task' in locals() and typing_task and not typing_task.done():
            logging.info("Cancelling typing queue processor task...")
            typing_task.cancel()
            try:
                await asyncio.wait_for(typing_task, timeout=1.0)
                logging.info("Typing queue processor task stopped.")
            except asyncio.TimeoutError:
                logging.warning("Timeout waiting for typing task to cancel.")
            except asyncio.CancelledError:
                logging.info("Typing queue processor task cancelled successfully.")
            except Exception as e:
                 logging.error(f"Error stopping typing task: {e}")
        # --- END NEW ---

        # --- NEW: Stop Session Monitor --- >
        if 'session_monitor' in locals() and session_monitor:
            logging.info("Stopping Session Monitor...")
            session_monitor.stop()
            # Wait briefly for its thread (optional, it's a daemon)
            # session_monitor.thread.join(timeout=0.5)
            # --- END NEW ---

            # --- Stop Input Listeners EARLY --- >
            listeners_to_stop = []
            if 'mouse_listener' in locals() and mouse_listener.is_alive(): listeners_to_stop.append(mouse_listener)
            if 'keyboard_listener' in locals() and keyboard_listener.is_alive(): listeners_to_stop.append(keyboard_listener)

            logging.info("Signaling input listeners to stop early...")
            listener_stop_start_time = time.monotonic() # Track listener stop time
            for listener in listeners_to_stop:
                try:
                    # Use stop() for pynput listeners
                    listener.stop()
                except Exception as e:
                    logging.error(f"Error signaling stop for listener {listener}: {e}")
            # --- END EARLY STOP ---

            # --- MODIFIED: More Robust Stop Sequence ---
            stt_stopped_cleanly = False # Flag to track STT shutdown
            # 1. Stop Async Tasks First (like STT)
            # --- NEW: Stop all active STT Handlers ---
            logging.info("Stopping all active STT Handlers...")
            stop_tasks = []
            # Make sure active_stt_sessions is accessed correctly if it's a global or passed variable
            # Assuming active_stt_sessions is accessible here
            for session_id, session_data in list(active_stt_sessions.items()): # Use list copy for safe iteration
                handler = session_data.get('handler')
                if handler:
                    logging.debug(f"Requesting stop for handler {session_id}...")
                    # Use default timeout (3s) defined in handler's stop_listening
                    stop_tasks.append(asyncio.create_task(handler.stop_listening(), name=f"StopHandler_{session_id}"))
                else:
                    logging.warning(f"No handler found for session {session_id} during shutdown.")

            if stop_tasks:
                logging.info(f"Waiting for {len(stop_tasks)} STT handler(s) to stop...")
                # Wait for all stop tasks to complete (no individual timeout here, handled in stop_listening)
                done, pending = await asyncio.wait(stop_tasks, timeout=5.0) # Overall timeout for all handlers
                if pending:
                    logging.warning(f"{len(pending)} STT handler stop tasks timed out after 5s.")
                    for task in pending:
                        task.cancel() # Attempt to cancel timed out tasks
                logging.info("Finished waiting for STT handler stop tasks.")
            else:
                logging.info("No active STT handlers to stop.")
            active_stt_sessions.clear() # Clear sessions after attempting stop
            # --- END NEW ---

            # 2. Stop Thread-Based Managers (Signal them first)
            managers_to_stop = []
            if config_manager.get("modules.audio_buffer_enabled") and 'buffered_audio_input' in locals() and buffered_audio_input: managers_to_stop.append(buffered_audio_input)
            if config_manager.get("modules.tooltip_enabled") and 'tooltip_mgr' in locals() and tooltip_mgr: managers_to_stop.append(tooltip_mgr)
            if config_manager.get("modules.status_indicator_enabled") and 'status_mgr' in locals() and status_mgr: managers_to_stop.append(status_mgr)
            if config_manager.get("modules.action_confirm_enabled") and 'action_confirm_mgr' in locals() and action_confirm_mgr: managers_to_stop.append(action_confirm_mgr)

            logging.info("Signaling component managers to stop...")
            for manager in managers_to_stop:
                try:
                    manager.stop()
                except Exception as e:
                    logging.error(f"Error signaling stop for {type(manager).__name__}: {e}")

            # 3. Stop Input Listeners (Redundant with EARLY stop? Keep for now, ensure idempotency or remove EARLY)
            # listeners_to_stop = [] # Defined in EARLY block
            # if 'mouse_listener' in locals() and mouse_listener.is_alive(): listeners_to_stop.append(mouse_listener)
            # if 'keyboard_listener' in locals() and keyboard_listener.is_alive(): listeners_to_stop.append(keyboard_listener)

            # logging.info("Signaling input listeners to stop (second time?)...") # Adjust log message if needed
            # listener_stop_start_time = time.monotonic() # Track listener stop time
            # for listener in listeners_to_stop:
            #     try:
            #         listener.stop()
            #     except Exception as e:
            #         logging.error(f"Error signaling stop for listener {listener}: {e}")

            # 4. Explicitly Cancel Remaining Asyncio Tasks (Replaces Stop/Close)
            # Moved BEFORE waiting for threads/listeners to potentially free up loop
            logging.info("Cancelling any remaining asyncio tasks...")
            tasks_cancelled_cleanly = False
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    tasks = asyncio.all_tasks(loop)
                    current_task = asyncio.current_task(loop)
                    tasks_to_cancel = [task for task in tasks if task is not current_task and not task.done()]

                    if tasks_to_cancel:
                        logging.debug(f"Found {len(tasks_to_cancel)} tasks to cancel: {[t.get_name() for t in tasks_to_cancel]}")
                        for task in tasks_to_cancel:
                            task.cancel()

                            # Give cancelled tasks a chance to run, with a timeout
                            # Gather results to see exceptions during cancellation
                            results = await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                            logging.debug(f"Gathered cancelled tasks results (Exceptions indicate issues): {results}")
                            # Check if all results are either None (clean cancel) or CancelledError
                            tasks_cancelled_cleanly = all(res is None or isinstance(res, asyncio.CancelledError) for res in results)
                            if not tasks_cancelled_cleanly:
                                logging.warning("Some asyncio tasks did not cancel cleanly.")
                        else:
                            logging.debug("No remaining tasks needed cancellation.")
                            tasks_cancelled_cleanly = True
                    else:
                        logging.debug("No remaining tasks needed cancellation.")
                        tasks_cancelled_cleanly = True
                else:
                    logging.debug("Asyncio event loop was not running during task cancellation check.")
                    tasks_cancelled_cleanly = True # Consider it clean if loop wasn't running

            except RuntimeError as e:
                if "no current event loop" in str(e).lower():
                    logging.debug("No running asyncio event loop found to cancel tasks.")
                else:
                    logging.error(f"RuntimeError during task cancellation: {e}", exc_info=True)
            except Exception as e:
                logging.error(f"Unexpected error during task cancellation: {e}", exc_info=True)
            logging.info(f"Asyncio task cancellation finished (Cleanly: {tasks_cancelled_cleanly}).")

            # 5. Wait for Manager Threads
            logging.info("Waiting for component manager threads to join...")
            for manager in managers_to_stop:
                try:
                    if hasattr(manager, 'thread') and manager.thread and manager.thread.is_alive():
                        manager.thread.join(timeout=1.0)
                        if manager.thread.is_alive():
                            logging.warning(f"{type(manager).__name__} thread did not join cleanly.")
                except Exception as e:
                    logging.error(f"Error joining thread for {type(manager).__name__}: {e}")
            logging.info("Component manager threads joined.")

            # 6. Wait for Input Listeners (Join only, stop was signaled earlier)
            logging.info("Waiting for input listener threads to join...")
            for listener in listeners_to_stop:
                try:
                    # Use join() to wait for the listener *thread* to finish
                    # Ensure listener stop time covers the whole period
                    # We have listener_stop_start_time from the EARLY block
                    listener.join(timeout=1.0)
                    if listener.is_alive():
                        logging.warning(f"Listener {listener} thread did not join cleanly.")
                except Exception as e:
                    logging.error(f"Error joining listener thread {listener}: {e}")
            logging.info("Input listener threads joined.")
            # Ensure listener_stop_start_time exists before calculating duration
            if 'listener_stop_start_time' in locals():
                listener_stop_duration = time.monotonic() - listener_stop_start_time # Measure full duration
                logging.info(f"Listener stop signal & join took: {listener_stop_duration:.3f}s")
            else:
                logging.warning("Could not measure listener stop duration (start time missing).")


            # 7. Wait for Systray Thread
            if 'systray_thread' in locals() and systray_thread.is_alive():
                logging.info("Waiting for systray thread to exit...")
                systray_thread.join(timeout=2.0) # Increased timeout slightly
                if systray_thread.is_alive(): logging.warning("Systray thread did not exit cleanly.")
                else: logging.info("Systray thread finished.")

            logging.info("Vibe App finished.")

# --- Add Exit Event for Systray Communication ---
systray_ui.exit_app_event = threading.Event() # Create event in main module
# Ensure correct global indentation for this line and below

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
        # --- Ensure exit event is set on KeyboardInterrupt --- >
        if 'systray_ui' in globals() and hasattr(systray_ui, 'exit_app_event') and not systray_ui.exit_app_event.is_set():
            logging.debug("Setting exit_app_event due to KeyboardInterrupt.")
            systray_ui.exit_app_event.set()
        # --- End Ensure --- >
    except pyautogui.FailSafeException:
         logging.critical("PyAutoGUI FAILSAFE triggered! Exiting.")
    except Exception as e:
        logging.error(f"An unexpected error occurred in main run: {e}", exc_info=True)

# --- NEW: Typing Queue Processor Task --- >

