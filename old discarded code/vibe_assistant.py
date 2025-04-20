# vibe_assistant.py

import asyncio
import os
import threading
import logging
import time
from dotenv import load_dotenv
import pyaudio # Add PyAudio import

from pynput import mouse, keyboard
from deepgram import (
    DeepgramClient, 
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

# --- Configuration ---
load_dotenv() # Load environment variables from .env file
API_KEY = os.getenv("DEEPGRAM_API_KEY")

# Placeholder for trigger configuration (will be loaded from settings later)
DICTATION_TRIGGER_BUTTON = mouse.Button.x1 # Example: Side button 1
COMMAND_TRIGGER_BUTTON = mouse.Button.x2   # Example: Side button 2
MIN_DURATION_SEC = 0.5 # Minimum recording duration to process
# Add placeholders for wake words and other configurable items
DICTATION_WAKE_WORDS = ["hey"] 
COMMAND_WAKE_WORDS = ["command"]
CONFIRMATION_PHRASE = "confirmed"
CANCEL_PHRASE = "cancel"
UNDO_PHRASE = "go back"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Global State (Use with care, consider dedicated state management later) ---
# Using threading.Event for cross-thread communication
is_dictation_active = threading.Event()
is_command_active = threading.Event()
transcription_active_event = threading.Event() # General flag if any transcription is running
current_mode = None # To track if dictation or command is active

# --- Microphone Handling Implementation ---
CHUNK = 512 # Size of audio chunks to read from microphone
FORMAT = pyaudio.paInt16 # Audio format
CHANNELS = 1 # Mono audio
RATE = 16000 # Sample rate (ensure compatible with Deepgram model)

def get_microphone_stream():
    """Initializes PyAudio and opens a microphone stream."""
    try:
        p = pyaudio.PyAudio()
        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
        logging.info("Microphone stream opened successfully.")
        return p, stream
    except Exception as e:
        logging.error(f"Error opening microphone stream: {e}")
        return None, None

def microphone_thread_func(dg_connection, stop_event):
    """Thread function to capture audio and send to Deepgram.
    
    Args:
        dg_connection: The active Deepgram WebSocket connection object.
        stop_event: A threading.Event() to signal when to stop.
    """
    p, stream = get_microphone_stream()
    if not p or not stream:
        logging.error("Microphone thread exiting: Could not open stream.")
        transcription_active_event.clear() # Ensure main loop knows we failed
        is_dictation_active.clear()
        is_command_active.clear()
        return

    logging.info("Microphone thread started, sending audio to Deepgram...")
    start_time = time.time()
    total_bytes_sent = 0

    while not stop_event.is_set():
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            asyncio.run_coroutine_threadsafe(dg_connection.send(data), asyncio.get_event_loop())
            total_bytes_sent += len(data)
            # logging.debug(f"Sent {len(data)} bytes of audio.") # Optional: Verbose logging
        except IOError as e:
            logging.error(f"Microphone read error: {e}")
            break # Exit loop on read error
        except Exception as e:
            logging.error(f"Error sending audio to Deepgram: {e}")
            # Decide if we should break or continue
            break 

    # --- Cleanup --- 
    elapsed_time = time.time() - start_time
    logging.info(f"Microphone thread stopping. Sent {total_bytes_sent} bytes in {elapsed_time:.2f}s.")
    
    try:
        stream.stop_stream()
        stream.close()
        p.terminate()
        logging.info("Microphone stream closed.")
    except Exception as e:
        logging.error(f"Error closing microphone stream: {e}")
    
    # Optional: Send a signal that we're done sending audio if Deepgram needs it?
    # Check Deepgram SDK docs if explicit end-of-stream signal is needed beyond closing connection.
    # await dg_connection.finish() # This is likely called by the main listener task

# --- Pynput Listener Callbacks ---
def on_click(x, y, button, pressed):
    """Handles mouse button presses/releases."""
    global current_mode, mic_thread # Need mic_thread in global scope for start/stop
    
    if button == DICTATION_TRIGGER_BUTTON:
        if pressed:
            if not transcription_active_event.is_set(): 
                logging.info("Dictation button pressed - starting dictation.")
                is_dictation_active.clear() # Ensure clear before setting
                is_command_active.clear()
                is_dictation_active.set()
                transcription_active_event.set()
                current_mode = "dictation"
                # Stop event will be checked by the microphone thread
                mic_stop_event = is_dictation_active # Use the mode event to stop
                # Note: Starting thread here assumes main loop handles dg_listener_task
                # mic_thread = threading.Thread(target=microphone_thread_func, args=(dg_connection, mic_stop_event), daemon=True)
                # mic_thread.start()
                logging.info("Signaled main loop to start Deepgram listener.")
        else:
            if is_dictation_active.is_set():
                duration = time.time() - start_time # Need start_time from press event
                logging.info(f"Dictation button released (duration: {duration:.2f}s). Stopping dictation.")
                is_dictation_active.clear() # Signal microphone/main loop to stop
                # Final transcript handling happens in main loop based on event clear
                current_mode = None 
                # No need to explicitly stop mic_thread, it watches the event
                # No need to explicitly stop dg_listener_task, main loop handles it
                
                # TODO: Implement minimum duration check here
                # if duration < MIN_DURATION_SEC: 
                #     logging.info("Duration too short, discarding.")
                #     # Need logic to tell main loop/DG handler to discard
                # else:
                #     # Signal main loop to handle final transcript
                #     pass 

    elif button == COMMAND_TRIGGER_BUTTON:
        if pressed:
             if not transcription_active_event.is_set():
                logging.info("Command button pressed - starting command mode.")
                is_dictation_active.clear()
                is_command_active.clear() # Ensure clear before setting
                is_command_active.set()
                transcription_active_event.set()
                current_mode = "command"
                mic_stop_event = is_command_active # Use the mode event to stop
                # mic_thread = threading.Thread(target=microphone_thread_func, args=(dg_connection, mic_stop_event), daemon=True)
                # mic_thread.start()
                logging.info("Signaled main loop to start Deepgram listener.")
                # TODO: Show command feedback UI
        else:
            if is_command_active.is_set():
                duration = time.time() - start_time # Need start_time from press event
                logging.info(f"Command button released (duration: {duration:.2f}s). Confirming command.")
                is_command_active.clear() # Signal microphone/main loop to stop
                # Confirmation logic happens in main loop based on event clear
                current_mode = None
                # TODO: Hide command feedback UI
                
                # TODO: Implement minimum duration check here
                # if duration < MIN_DURATION_SEC:
                #     logging.info("Duration too short, discarding command.")
                #     # Need logic to tell main loop/DG handler to discard
                # else:
                #     # Signal main loop to get final command text and execute
                #     pass

def on_press(key):
    """Handles keyboard key presses."""
    try:
        # Check for cancellation keybinds if command mode is active
        if is_command_active.is_set() and key == keyboard.Key.esc:
            logging.info("ESC pressed during command - cancelling.")
            # TODO: Implement cancellation logic (stop mic, DG, hide UI)
            is_command_active.clear()
            transcription_active_event.clear()
            current_mode = None
            
        # TODO: Add check for Undo keybind?
        
    except AttributeError:
        # Handle regular key presses if needed in the future
        pass

# --- Main Application Logic ---
# Need to declare mic_thread in the main scope
mic_thread = None
start_time = None # Track button press start time
final_command_text = None # Store final command text briefly
final_dictation_text = None # Store final dictation text briefly

dg_connection = None # Define dg_connection in the main scope

async def listen_deepgram(dg_connection):
    """Listens for messages from Deepgram and routes them."""
    global final_command_text, final_dictation_text
    final_command_text = "" # Reset on new listen
    final_dictation_text = "" # Reset on new listen
    
    try:
        # Register event handlers with the connection object
        async def on_message(self, result, **kwargs):
            # Handle interim/final results
            try:
                sentence = result.channel.alternatives[0].transcript
                is_final = result.is_final
                if len(sentence) == 0:
                    return
                
                if is_final:
                    logging.info(f"Deepgram Final transcript: \"{sentence}\"")
                    if current_mode == "dictation":
                        final_dictation_text += sentence + " " 
                    elif current_mode == "command":
                        final_command_text += sentence + " " 
                    # Pass the raw result object to the handlers
                    if current_mode == "dictation":
                         handle_dictation_result(result) # Pass full result
                    elif current_mode == "command":
                         handle_command_result(result) # Pass full result
                else:
                    logging.info(f"Deepgram Interim transcript: \"{sentence}\"")
                    # Pass the raw result object to the handlers
                    if current_mode == "dictation":
                        handle_dictation_result(result) # Pass full result
                    elif current_mode == "command":
                        handle_command_result(result) # Pass full result
            except (AttributeError, IndexError) as e:
                 logging.warning(f"Could not process transcript from message: {result} - Error: {e}")

        async def on_speech_started(self, speech_started, **kwargs):
            logging.debug("Deepgram speech started")

        async def on_utterance_end(self, utterance_end, **kwargs):
            logging.debug("Deepgram utterance ended")

        async def on_error(self, error, **kwargs):
            logging.error(f"Deepgram Error: {error}")

        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
        dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)

        # Start the connection
        await dg_connection.start()
        logging.info("Deepgram connection started and listening...")
        
        # Keep the listener alive while transcription is active
        # This task now mainly waits for cancellation from the main loop
        while transcription_active_event.is_set():
            await asyncio.sleep(0.1)
        
        logging.info("Transcription event cleared, requesting Deepgram finish...")

    except asyncio.CancelledError:
        logging.info("Deepgram listener task cancelled.")
    except Exception as e:
        logging.error(f"Error in Deepgram listener/start: {e}", exc_info=True)
    finally:
        if dg_connection and dg_connection.is_connected():
            await dg_connection.finish()
            logging.info("Deepgram connection finished.")
        else:
            logging.info("Deepgram connection already closed or finish called.")

async def main():
    global mic_thread, start_time, dg_connection # Refer to global dg_connection
    logging.info("Starting Vibe Assistant...")

    # Initial Deepgram setup (only configures client)
    _, deepgram = setup_deepgram_connection()
    if not deepgram:
        logging.error("Failed to set up Deepgram client. Exiting.")
        return

    # Start pynput listeners in separate threads
    mouse_listener = mouse.Listener(on_click=on_click)
    keyboard_listener = keyboard.Listener(on_press=on_press)
    
    mouse_listener.start()
    keyboard_listener.start()
    logging.info("Input listeners started.")

    dg_listener_task = None
    current_connection = None # Track the active connection object

    try:
        while True:
            if transcription_active_event.is_set() and dg_listener_task is None:
                logging.info("Transcription activated, starting Deepgram connection and microphone...")
                start_time = time.time() # Record start time
                
                # Configure and establish the actual WebSocket connection
                options: LiveOptions = LiveOptions(
                    model="nova-2", language="en-US", smart_format=True, 
                    interim_results=True, endpointing=300
                )
                try:
                    # Use the recommended asyncwebsocket path
                    current_connection = deepgram.listen.asyncwebsocket.v("1").websocket(options=options)
                    logging.info("Deepgram WebSocket configured.")

                    dg_listener_task = asyncio.create_task(listen_deepgram(current_connection))
                    logging.info("Deepgram listener task created.")

                    # Determine the stop event based on the current mode
                    mic_stop_event = is_dictation_active if current_mode == "dictation" else is_command_active

                    mic_thread = threading.Thread(target=microphone_thread_func, args=(current_connection, mic_stop_event), daemon=True)
                    mic_thread.start() # Start microphone capture
                    logging.info("Microphone thread started.")

                except Exception as e:
                    logging.error(f"Failed to start Deepgram connection or mic thread: {e}")
                    transcription_active_event.clear() # Reset state on failure
                    is_dictation_active.clear()
                    is_command_active.clear()
                    current_mode = None
                    dg_listener_task = None
                    current_connection = None
            
            elif not transcription_active_event.is_set() and dg_listener_task is not None:
                logging.info("Transcription deactivated, stopping Deepgram listener...")
                duration = time.time() - start_time if start_time else 0
                start_time = None # Reset start time

                # Signal microphone thread to stop (already done by clearing events in on_click)
                # Cancel the Deepgram listener task
                dg_listener_task.cancel()
                try:
                    await dg_listener_task # Wait for cancellation and finish()
                except asyncio.CancelledError:
                    logging.info("Deepgram listener task successfully cancelled.")
                except Exception as e:
                    logging.error(f"Error awaiting cancelled listener task: {e}")
                
                dg_listener_task = None
                current_connection = None
                mic_thread = None # Thread should exit on its own
                logging.info("Deepgram listener processing stopped.")
                
                # --- Post-Transcription Handling ---
                if duration >= MIN_DURATION_SEC:
                    if final_dictation_text:
                        handle_final_dictation(final_dictation_text.strip())
                    elif final_command_text:
                        handle_final_command(final_command_text.strip())
                    else:
                        logging.warning("Transcription ended but no final text captured.")
                else:
                    logging.info(f"Transcription duration ({duration:.2f}s) less than minimum ({MIN_DURATION_SEC}s), discarding.")
                
                # Reset final text buffers
                final_dictation_text = None
                final_command_text = None
                current_mode = None # Ensure mode is cleared
                
            await asyncio.sleep(0.1) # Prevent busy-waiting
            
    except asyncio.CancelledError:
        logging.info("Main task cancelled.")
    finally:
        logging.info("Stopping listeners...")
        mouse_listener.stop()
        keyboard_listener.stop()
        mouse_listener.join()
        keyboard_listener.join()
        logging.info("Listeners stopped.")
        if dg_listener_task and not dg_listener_task.done():
            dg_listener_task.cancel()
            try: await dg_listener_task
            except asyncio.CancelledError: pass
            logging.info("Ensured Deepgram listener task is stopped on exit.")

# --- Placeholder Functions / Implementations ---

def setup_deepgram_connection():
    """Sets up the Deepgram client object."""
    if not API_KEY:
        logging.error("DEEPGRAM_API_KEY not found in environment variables.")
        return None, None

    try:
        # STEP 1: Create a Deepgram client using the API key
        config: DeepgramClientOptions = DeepgramClientOptions(
            verbose=logging.DEBUG # Or logging.INFO for less verbosity
        )
        deepgram: DeepgramClient = DeepgramClient(API_KEY, config)
        logging.info("Deepgram client configured.")
        # We return the client, connection options are handled in main loop now
        return None, deepgram # Return None for connection initially, just the client
    except Exception as e:
        logging.error(f"Could not configure Deepgram client: {e}")
        return None, None

def handle_dictation_result(result):
    """Processes transcription results in Dictation mode."""
    try:
        transcript = result.channel.alternatives[0].transcript
        if transcript:
            logging.info(f"Dictation Result: {transcript}")
            # TODO: Implement typing simulation (interim and final)
            # TODO: Implement backspace/correction logic
    except (AttributeError, IndexError) as e:
        logging.warning(f"Could not extract transcript from dictation result: {result} - Error: {e}")
    pass

def handle_final_dictation(full_transcript):
    # ... (definition remains the same)
    pass

def handle_command_result(result):
    """Processes transcription results in Command mode."""
    try:
        transcript = result.channel.alternatives[0].transcript
        if transcript:
            logging.info(f"Command Result: {transcript}")
            # TODO: Update command feedback UI (show interim/final command text)
            # TODO: Check for CANCEL_PHRASE
    except (AttributeError, IndexError) as e:
        logging.warning(f"Could not extract transcript from command result: {result} - Error: {e}")
    pass

# ... (handle_final_command, undo_last_command definitions) ...

if __name__ == "__main__":
    logging.info(f"Vibe Assistant starting up...")
    # Ensure API key is present
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
    logging.info(f"Vibe Assistant finished.")
 