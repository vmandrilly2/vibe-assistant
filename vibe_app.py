# vibe_app.py

import asyncio
import os
import threading
import logging
import time
from dotenv import load_dotenv

from pynput import mouse, keyboard
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone, # Import Microphone class
)

# --- Configuration ---
load_dotenv() # Load environment variables from .env file
API_KEY = os.getenv("DEEPGRAM_API_KEY")

# Placeholder for trigger configuration (will be loaded from settings later)
DICTATION_TRIGGER_BUTTON = mouse.Button.middle # CHANGED: Use Middle Mouse Button
COMMAND_TRIGGER_BUTTON = None # DISABLED temporarily until new trigger decided
MIN_DURATION_SEC = 0.5 # Minimum recording duration to process
SELECTED_LANGUAGE = "en-US" # Placeholder for language selection
# TODO: Add placeholders for wake words, confirmation/cancel phrases etc. for future features

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Global State ---
is_dictation_active = threading.Event()
is_command_active = threading.Event()
transcription_active_event = threading.Event() # True if either dictation or command is active
current_mode = None # 'dictation' or 'command'

# --- Keyboard Controller (for typing simulation) ---
kb_controller = keyboard.Controller()

# --- State for Dictation Typing Simulation ---
last_simulated_text = "" # Store the transcript corresponding to the last simulation action
typed_word_history = [] # Store history of typed words

# --- State for Command Mode ---
current_command_transcript = "" # Store the transcript for command mode
last_command_executed = None # For potential undo feature

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
    """Handles interim dictation results ONLY by updating the last_typed_interim state.
    It does NOT simulate typing or backspaces anymore."""
    global last_simulated_text # Keep variable for now, but don't use for typing
    if not transcript:
        return

    # Only log, do not simulate keys or update last_simulated_text here
    logging.debug(f"Interim Received (Not Processed): '{transcript}'")
    # last_simulated_text = transcript # DO NOT UPDATE STATE HERE

    # TODO: Optionally display the interim transcript in a temporary UI element here

    # --- REMOVED TYPING/BACKSPACE LOGIC ---

def handle_dictation_final(final_transcript, history):
    """Handles the final dictation transcript based on history and incoming transcript.
    Calculates target state, determines diff from current state, executes, updates history."""
    logging.warning(f"RAW FINAL TRANSCRIPT RECEIVED: '{final_transcript}'")
    
    # --- Step A: Calculate Target Word List --- 
    # Start with words from the current history
    target_words = [entry['text'] for entry in history]
    logging.debug(f"Initial target_words from history: {target_words}")

    original_words = final_transcript.split()
    punctuation_to_strip = '.,!?;:'
    
    # Process the new transcript against the target list
    for word in original_words:
        cleaned_word = word.rstrip(punctuation_to_strip).lower()

        if cleaned_word == "back":
            if target_words:
                removed = target_words.pop()
                logging.info(f"Processing 'back', removed '{removed}' from target_words.")
            else:
                logging.info(f"Processing 'back', but target_words already empty.")
        else:
            target_words.append(word) # Append original word with punctuation
    
    logging.debug(f"Final target_words after processing transcript: {target_words}")

    # --- Step B: Calculate Target Text --- 
    target_text = " ".join(target_words) + ' ' if target_words else ''
    logging.debug(f"Calculated target_text: '{target_text}'")

    # --- Step C: Calculate Current Text on Screen (Estimate from OLD history) --- 
    # We need the text *before* processing the current transcript's 'back' commands
    current_text_estimate = " ".join([entry['text'] for entry in history]) + ' ' if history else ''
    logging.debug(f"Estimated current text (from old history): '{current_text_estimate}'")

    # --- Step D: Calculate Diff --- 
    common_prefix_len = 0
    min_len = min(len(current_text_estimate), len(target_text))
    while common_prefix_len < min_len and current_text_estimate[common_prefix_len] == target_text[common_prefix_len]:
        common_prefix_len += 1
        
    backspaces_needed = len(current_text_estimate) - common_prefix_len
    text_to_type = target_text[common_prefix_len:]
    
    logging.debug(f"Diff Calculation: Prefix={common_prefix_len}, Backspaces={backspaces_needed}, Type='{text_to_type}'")

    # --- Step E: Execute Typing Actions --- 
    # IMPORTANT: Order matters. Backspace first, then type.
    if backspaces_needed > 0:
        simulate_backspace(backspaces_needed)
        
    if text_to_type:
        simulate_typing(text_to_type)

    # --- Step F: Update History to Match Target State --- 
    history.clear() 
    if target_words: # Use the target_words list we built
        logging.debug(f"Rebuilding history with: {target_words}")
        for word in target_words:
            if word: # Should not be necessary if split is good, but safe
                length_with_space = len(word) + 1
                entry = {"text": word, "length_with_space": length_with_space}
                history.append(entry)
    else:
        logging.debug("History cleared as target_words is empty.")

    return history # Return the new history state

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

# --- Deepgram Event Handlers ---
async def on_open(self, open, **kwargs):
    logging.info("Deepgram connection opened.")

async def on_message(self, result, **kwargs):
    global typed_word_history # Keep global here to update the list reference
    try:
        transcript = result.channel.alternatives[0].transcript
        if not transcript:
            return

        if current_mode == "dictation":
            if result.is_final:
                final_part = transcript # Get the final utterance part
                # Pass history in, get potentially modified history back
                typed_word_history = handle_dictation_final(final_part, typed_word_history)
            else:
                # Interim handler doesn't need history
                handle_dictation_interim(transcript)
        elif current_mode == "command":
            if result.is_final:
                handle_command_final(transcript)
            else:
                handle_command_interim(transcript)

    except (AttributeError, IndexError) as e:
        logging.error(f"Error processing Deepgram message: {e} - Result: {result}")

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
    global current_mode, start_time
    
    trigger_mode = None
    active_event = None

    # Check for Dictation Trigger
    if button == DICTATION_TRIGGER_BUTTON:
        trigger_mode = "dictation"
        active_event = is_dictation_active
    # Check for Command Trigger (Currently disabled)
    elif COMMAND_TRIGGER_BUTTON is not None and button == COMMAND_TRIGGER_BUTTON:
        trigger_mode = "command"
        active_event = is_command_active
    else:
        return # Ignore other button clicks

    if trigger_mode:
        if pressed:
            if not transcription_active_event.is_set(): # Start only if nothing else is active
                logging.info(f"{trigger_mode.capitalize()} button pressed - starting mode.")
                # Clear any potentially lingering state from other mode
                is_dictation_active.clear()
                is_command_active.clear()
                # Set the active mode
                active_event.set()
                transcription_active_event.set()
                current_mode = trigger_mode
                start_time = time.time() # Record start time for duration check
                if trigger_mode == "command":
                    # TODO: Show command feedback UI
                    pass 
            else:
                logging.warning(f"Attempted to start {trigger_mode} while already active ({current_mode})")
        else: # Button released
            if active_event.is_set():
                duration = time.time() - start_time if start_time else 0
                logging.info(f"{trigger_mode.capitalize()} button released (duration: {duration:.2f}s). Stopping mode.")
                # Clear events to signal stopping
                active_event.clear()
                transcription_active_event.clear() # Signal main loop to stop DG/Mic
                # Post-processing is handled in the main loop after stopping
                if trigger_mode == "command":
                     # TODO: Hide command feedback UI
                     pass
            # Don't reset current_mode here, main loop needs it for final processing

def on_press(key):
    global current_mode
    try:
        if is_command_active.is_set() and key == keyboard.Key.esc:
            logging.info("ESC pressed during command - cancelling.")
            is_command_active.clear()
            transcription_active_event.clear() # Signal stop
            current_mode = "cancel" # Special mode to indicate cancellation
            # TODO: Hide command feedback UI
            
        # TODO: Add keybind for undo_last_command()?
            
    except AttributeError:
        pass

# --- Main Application Logic ---
async def main():
    global start_time, current_mode, current_command_transcript # Access global state
    
    logging.info("Starting Vibe App...")

    # Initialize Deepgram Client with reduced verbosity
    try:
        config = DeepgramClientOptions(verbose=logging.WARNING)
        deepgram: DeepgramClient = DeepgramClient(API_KEY, config)
    except Exception as e:
        logging.error(f"Failed to initialize Deepgram client: {e}")
        return

    # Start pynput listeners
    mouse_listener = mouse.Listener(on_click=on_click)
    keyboard_listener = keyboard.Listener(on_press=on_press)
    mouse_listener.start()
    keyboard_listener.start()
    logging.info("Input listeners started.")

    dg_connection = None
    microphone = None
    active_mode_on_stop = None # Store the mode when stopping

    try:
        while True:
            # Start Transcription Flow
            if transcription_active_event.is_set() and dg_connection is None:
                logging.info(f"Activating {current_mode} mode...")
                try:
                    # 1. Get connection object
                    dg_connection = deepgram.listen.asyncwebsocket.v("1")

                    # 2. Register handlers
                    dg_connection.on(LiveTranscriptionEvents.Open, on_open)
                    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
                    dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
                    dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
                    dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
                    dg_connection.on(LiveTranscriptionEvents.Error, on_error)
                    dg_connection.on(LiveTranscriptionEvents.Close, on_close)
                    dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)

                    # 3. Define options using configured language
                    options = LiveOptions(
                        model="nova-2", 
                        language=SELECTED_LANGUAGE, # Use variable here
                        smart_format=True,
                        interim_results=True, utterance_end_ms="1000", 
                        vad_events=True, endpointing=300, 
                        encoding="linear16", channels=1, sample_rate=16000
                    )
                    
                    # 4. Start connection
                    await dg_connection.start(options)

                    # 5. Initialize and start Microphone with logging wrapper
                    original_send = dg_connection.send
                    async def logging_send_wrapper(data):
                        # Check if connection is still valid before sending
                        if dg_connection and dg_connection.is_connected():
                            logging.debug(f"Mic sending {len(data)} bytes...")
                            try:
                                await original_send(data)
                            except Exception as send_e:
                                logging.error(f"Error during dg_connection.send: {send_e}")
                        else:
                            logging.warning("Mic attempted to send data but connection is closed.")

                    microphone = Microphone(logging_send_wrapper) # Use the wrapper
                    microphone.start()
                    logging.info("Deepgram connection and microphone started.")

                except Exception as e:
                    logging.error(f"Failed to start Deepgram/Microphone: {e}")
                    # Reset state fully on failure
                    if dg_connection:
                       await dg_connection.finish() # Attempt to clean up connection
                    is_dictation_active.clear()
                    is_command_active.clear()
                    transcription_active_event.clear()
                    current_mode = None
                    dg_connection = None
                    microphone = None
            
            # Stop Transcription Flow
            elif not transcription_active_event.is_set() and dg_connection is not None:
                active_mode_on_stop = current_mode # Capture mode before clearing
                logging.info(f"Deactivating {active_mode_on_stop} mode...")
                duration = time.time() - start_time if start_time else 0
                start_time = None

                # 1. Stop microphone
                if microphone:
                    microphone.finish()
                    microphone = None
                    logging.info("Microphone finished.")
                
                # 2. Stop Deepgram connection
                if dg_connection:
                    await dg_connection.finish()
                    dg_connection = None # Clear connection reference
                    logging.info("Deepgram connection finished.")

                # 3. Post-processing (check duration, handle final transcripts/commands)
                if active_mode_on_stop == "cancel":
                     logging.info("Command cancelled by user.")
                elif duration >= MIN_DURATION_SEC:
                    if active_mode_on_stop == "dictation":
                        # Final processing was handled by on_message with is_final
                        # handle_final_dictation(final_dictation_text) # If we need separate final call
                        pass 
                    elif active_mode_on_stop == "command":
                        execute_command(current_command_transcript)
                else:
                    logging.info(f"Transcription duration ({duration:.2f}s) less than minimum ({MIN_DURATION_SEC}s), discarding.")

                # 4. Reset state
                current_mode = None
                current_command_transcript = ""
                last_simulated_text = "" # Reset dictation tracking
                
            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        logging.info("Main task cancelled.")
    finally:
        logging.info("Stopping Vibe App...")
        # Cleanup listeners
        mouse_listener.stop()
        keyboard_listener.stop()
        mouse_listener.join()
        keyboard_listener.join()
        logging.info("Input listeners stopped.")
        # Ensure microphone and connection are stopped on exit
        if microphone:
            microphone.finish()
        if dg_connection:
            await dg_connection.finish()
        logging.info("Vibe App finished.")


if __name__ == "__main__":
    if not API_KEY:
        print("Error: DEEPGRAM_API_KEY environment variable not set.")
        print("Please create a .env file with DEEPGRAM_API_KEY=YOUR_KEY")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("Application interrupted by user (Ctrl+C).")
        except Exception as e:
            logging.error(f"An unexpected error occurred in main run: {e}", exc_info=True) 