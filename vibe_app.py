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
DICTATION_TRIGGER_BUTTON = mouse.Button.x1 # Example: Side button 1
COMMAND_TRIGGER_BUTTON = mouse.Button.x2   # Example: Side button 2
MIN_DURATION_SEC = 0.5 # Minimum recording duration to process
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
last_typed_transcript = "" # Store the last typed text to calculate backspaces

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
    """Handles interim dictation results by simulating typing/correction."""
    global last_typed_transcript
    logging.debug(f"Interim Dictation: '{transcript}', Last typed: '{last_typed_transcript}'")
    
    # Basic correction: backspace the previous interim result and type the new one
    # More sophisticated logic needed for word-level correction
    if last_typed_transcript:
        simulate_backspace(len(last_typed_transcript))
    
    simulate_typing(transcript)
    last_typed_transcript = transcript # Store what was just typed

def handle_dictation_final(final_transcript):
    """Handles the final dictation transcript."""
    global last_typed_transcript
    logging.info(f"Final Dictation: '{final_transcript}'")
    
    # Correct the last typed interim result
    if last_typed_transcript:
        simulate_backspace(len(last_typed_transcript))
    
    simulate_typing(final_transcript + ' ') # Type final + space
    last_typed_transcript = "" # Reset for next utterance

    # TODO: Optional AI Correction (if enabled)
    # corrected_text = call_ai_correction(final_transcript)
    # if corrected_text != final_transcript:
    #     simulate_backspace(len(final_transcript) + 1) # Remove final + space
    #     simulate_typing(corrected_text + ' ')
    #     # Handle highlighting/rejection UI
    pass

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
    global last_typed_transcript # Access global for dictation mode
    try:
        transcript = result.channel.alternatives[0].transcript
        if not transcript:
            return

        if current_mode == "dictation":
            if result.is_final:
                # Update last_typed with the final part before calling handler
                final_part = transcript # Assuming final result contains the full utterance part
                handle_dictation_final(final_part)
            else:
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
    global last_typed_transcript
    logging.debug("Deepgram Utterance Ended")
    if current_mode == "dictation":
        # Reset last typed transcript at utterance end for dictation
        last_typed_transcript = "" 

async def on_error(self, error, **kwargs):
    logging.error(f"Deepgram Handled Error: {error}")

async def on_close(self, close, **kwargs):
    logging.info("Deepgram connection closed.")

async def on_unhandled(self, unhandled, **kwargs):
    logging.warning(f"Deepgram Unhandled Websocket Message: {unhandled}")

# --- Pynput Listener Callbacks ---
def on_click(x, y, button, pressed):
    global current_mode, start_time
    
    # Determine which mode is being triggered/stopped
    trigger_mode = None
    active_event = None
    if button == DICTATION_TRIGGER_BUTTON:
        trigger_mode = "dictation"
        active_event = is_dictation_active
    elif button == COMMAND_TRIGGER_BUTTON:
        trigger_mode = "command"
        active_event = is_command_active

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

                    # 3. Define options
                    options = LiveOptions(
                        model="nova-2", language="en-US", smart_format=True,
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
                last_typed_transcript = "" # Reset dictation tracking
                
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