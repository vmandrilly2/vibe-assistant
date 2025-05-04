import asyncio
import logging
import queue
import time
import json
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone,
    # StreamSources, # Removed
    # --- MODIFIED: Import response types from specific client path ---
    # LiveTranscriptionResponse, # Added for type hinting
    # MetadataResponse, # Added for type hinting
    # SpeechStartedResponse, # Added for type hinting
    # UtteranceEndResponse, # Added for type hinting
    # ErrorResponse, # Added for type hinting
    # CloseResponse # Added for type hinting
    # Attempting specific imports based on likely structure
)
# --- MODIFIED: Import response types directly from v1 package as indicated by its __init__.py ---
# from deepgram.clients.live.v1.async_client import (
#    LiveTranscriptionResponse,
#    MetadataResponse,
#    SpeechStartedResponse,
#    UtteranceEndResponse,
#    ErrorResponse,
#    CloseResponse
# )
# --- REMOVED Imports for Response Types (causing errors) ---
# from deepgram.clients.live.v1.client import (
#     LiveTranscriptionResponse,
#     MetadataResponse,
#     SpeechStartedResponse,
#     UtteranceEndResponse,
#     ErrorResponse,
#     CloseResponse,
#     OpenResponse # Also import OpenResponse used in _on_open type hint
# )
# --- END REMOVED ---
from background_audio_recorder import BackgroundAudioRecorder

# --- Constants (Consider moving to a shared config or passing via options) --- >
MAX_CONNECT_ATTEMPTS = 3
# --- NEW: Escalating Timeouts and Specific Delays ---
ATTEMPT_TIMEOUTS_SEC = [1.0, 2.0, 3.0] # Timeout for each attempt
RETRY_DELAYS_SEC = [0.5, 0.2]         # Delay *before* attempt 2 and attempt 3
# --- END NEW ---

class STTConnectionHandler:
    """Manages a single connection and transcription lifecycle with the STT service (Deepgram)."""

    MAX_CONNECT_ATTEMPTS = 3 # Class variable for default

    def __init__(self,
                 activation_id: any, # Unique identifier for this session
                 stt_client: DeepgramClient,
                 status_q: queue.Queue,
                 transcript_q: queue.Queue,
                 ui_action_q: queue.Queue,
                 background_recorder: BackgroundAudioRecorder,
                 options: LiveOptions):
        """
        Args:
            activation_id: The unique ID for this specific connection instance.
            stt_client: An initialized DeepgramClient instance.
            status_q: Queue to send status updates (tagged with activation_id).
            transcript_q: Queue to send received transcripts (tagged with activation_id).
            ui_action_q: Queue to send internal state/connection updates to the main loop.
            background_recorder: The BackgroundAudioRecorder instance.
            options: The LiveOptions for this specific connection.
        """
        self.activation_id = activation_id
        self.client = stt_client
        self.status_queue = status_q
        self.transcript_queue = transcript_q
        self.ui_action_queue = ui_action_q
        self.background_recorder = background_recorder
        self.options = options # Store the specific options for this instance

        self.dg_connection = None
        self.microphone = None
        self.is_listening = False # Flag indicating if we intend to be listening for this instance
        self._connect_lock = asyncio.Lock() # Prevent concurrent start/stop for *this instance*
        self._connection_established_event = asyncio.Event()
        self._connection_task = None # Task managing the connection loop for this instance
        self._explicitly_stopped = False # Flag to distinguish intentional stop from errors/disconnects

        logging.info(f"STTConnectionHandler initialized for ID: {self.activation_id}")

    def _send_status(self, status: str):
        """Helper to send status updates tagged with the activation ID."""
        try:
            status_data = {"status": status, "activation_id": self.activation_id}
            self.ui_action_queue.put_nowait(("connection_update", status_data))
            logging.debug(f"STTHandler[{self.activation_id}]: Sent status to main loop: {status}")
        except queue.Full:
            logging.warning(f"UI Action queue full when sending STTHandler[{self.activation_id}] status: {status}")
        except Exception as e:
            logging.error(f"Error sending STTHandler[{self.activation_id}] status update to UI Action Queue: {e}")

    # --- Internal STT Callbacks (Now methods of the class) ---

    async def _on_open(self, sender, open, **kwargs):
        logging.debug(f"STTHandler[{self.activation_id}] _on_open received: {open}")
        logging.info(f"STT connection opened for ID: {self.activation_id}.")
        self._send_status("connected")
        self._connection_established_event.set()

    async def _on_message(self, sender, result, **kwargs):
        logging.debug(f"STTHandler[{self.activation_id}] _on_message received.")
        if not hasattr(result, 'channel') or not hasattr(result.channel, 'alternatives') or not result.channel.alternatives:
             logging.error(f"STTHandler[{self.activation_id}] _on_message: Invalid result structure: {result}")
             return
        try:
            transcript = result.channel.alternatives[0].transcript
            if transcript:
                message_type = "final" if result.is_final else "interim"
                transcript_data = {
                    "type": message_type,
                    "transcript": transcript,
                    "activation_id": self.activation_id,
                    "is_final_dg": result.is_final # Pass Deepgram's final flag
                }
                # logging.debug(f"STTHandler[{self.activation_id}] sending transcript ({message_type}): {transcript!r}")
                self.transcript_queue.put_nowait(transcript_data)
        except queue.Full:
            logging.warning(f"Transcript queue full for STTHandler[{self.activation_id}]. Discarding {message_type} transcript.")
        except (AttributeError, IndexError) as e:
            logging.error(f"Error processing STT message in STTHandler[{self.activation_id}]: {e} - Result: {result}")
        except Exception as e:
            logging.error(f"Unhandled error in STTHandler[{self.activation_id}] _on_message: {e}", exc_info=True)

    async def _on_metadata(self, sender, metadata, **kwargs):
        logging.debug(f"STTHandler[{self.activation_id}] _on_metadata received: {metadata}")

    async def _on_speech_started(self, sender, speech_started, **kwargs):
        logging.debug(f"STTHandler[{self.activation_id}] _on_speech_started received: {speech_started}")

    async def _on_utterance_end(self, sender, utterance_end, **kwargs):
        logging.debug(f"STTHandler[{self.activation_id}] _on_utterance_end received: {utterance_end}")

    async def _on_error(self, sender, error, **kwargs):
        logging.error(f"STT Handled Error for ID {self.activation_id}: {error}")
        self._send_status("error")
        # Consider setting is_listening = False here or rely on connection loop to handle?
        # Let connection loop handle disconnect/retry logic based on this error trigger.

    async def _on_close(self, sender, close, **kwargs):
        logging.debug(f"STTHandler[{self.activation_id}] _on_close received: {close}")
        # Only log INFO if it wasn't an explicit stop initiated by our code
        if not self._explicitly_stopped:
            logging.info(f"STT connection closed unexpectedly for ID: {self.activation_id}.")
        else:
            logging.info(f"STT connection closed cleanly for ID: {self.activation_id}.")

        self._send_status("disconnected")
        # Clear the established event in case of unexpected closure
        self._connection_established_event.clear()
        # Don't set is_listening=False here, the connection_loop handles retry logic

    async def _on_unhandled(self, unhandled, **kwargs):
        logging.warning(f"STT Unhandled Websocket Message for ID {self.activation_id}: {unhandled}")

    # --- Connection Management ---

    async def start_listening(self):
        """Initiates the connection and listening process for this instance."""
        async with self._connect_lock:
            if self.is_listening:
                logging.warning(f"STTHandler[{self.activation_id}]: start_listening called while already listening.")
                return
            logging.info(f"STTHandler[{self.activation_id}]: Starting listening process...")
            self.is_listening = True
            self._explicitly_stopped = False # Reset flag on start

            if self._connection_task and not self._connection_task.done():
                logging.debug(f"STTHandler[{self.activation_id}]: Cancelling previous connection task.")
                self._connection_task.cancel()
                try:
                    await self._connection_task
                except asyncio.CancelledError:
                    logging.debug(f"STTHandler[{self.activation_id}]: Previous connection task cancelled successfully.")
                except Exception as e:
                    logging.warning(f"STTHandler[{self.activation_id}]: Error awaiting previous task cancellation: {e}")

            self._connection_task = asyncio.create_task(self._connection_loop())
            logging.debug(f"STTHandler[{self.activation_id}]: Connection task created.")

    async def stop_listening(self, timeout=3.0):
        """Stops the listening process and closes the connection for this instance."""
        async with self._connect_lock:
            if not self.is_listening and (not self._connection_task or self._connection_task.done()):
                logging.warning(f"STTHandler[{self.activation_id}]: stop_listening called but not actively listening or task already done.")
                # Attempt cleanup just in case
                await self._disconnect()
                self.is_listening = False # Ensure state is correct
                return

            logging.info(f"STTHandler[{self.activation_id}]: Stopping listening process (timeout={timeout}s)...")
            self.is_listening = False # Signal loop to stop retrying/connecting
            self._explicitly_stopped = True # Mark as intentional stop

            try:
                # This method only sets flags now. Actual stopping happens elsewhere.
                logging.debug(f"STTHandler[{self.activation_id}]: stop_listening called. Internal state flags set.")
            except Exception as e:
                # Should not happen as we are just setting flags
                logging.error(f"STTHandler[{self.activation_id}]: Unexpected error in stop_listening: {e}", exc_info=True)

    async def _disconnect(self):
        """Safely disconnects the microphone and websocket connection for this instance."""
        logging.debug(f"STTHandler[{self.activation_id}]: Disconnecting...")
        # Ensure is_listening is False to prevent connection loop from restarting

        if self.microphone:
            logging.debug(f"STTHandler[{self.activation_id}]: Finishing microphone...")
            try:
                self.microphone.finish()
            except Exception as e:
                 logging.warning(f"STTHandler[{self.activation_id}]: Error finishing microphone: {e}")
            finally:
                 self.microphone = None
            logging.debug(f"STTHandler[{self.activation_id}]: Microphone finished.")

        if self.dg_connection:
            logging.debug(f"STTHandler[{self.activation_id}]: Finishing STT connection...")
            try:
                # DG SDK's finish() handles closing the websocket
                await self.dg_connection.finish()
                logging.debug(f"STTHandler[{self.activation_id}]: STT connection finish called.")
            except asyncio.CancelledError:
                 logging.warning(f"STTHandler[{self.activation_id}]: STT finish cancelled during disconnect.")
            except Exception as e:
                # Log errors, e.g., if connection was already closed
                logging.warning(f"STTHandler[{self.activation_id}]: Error during STT connection finish: {e}")
            finally:
                self.dg_connection = None # Clear reference

        self._connection_established_event.clear()
        logging.debug(f"STTHandler[{self.activation_id}]: Disconnect process complete.")

    async def stop_microphone(self):
        """Stops the microphone if it's running."""
        if self.microphone:
            logging.debug(f"STTHandler[{self.activation_id}]: Finishing microphone...")
            try:
                self.microphone.finish()
                self.microphone = None # Clear reference after stopping
                logging.debug(f"STTHandler[{self.activation_id}]: Microphone finished.")
            except Exception as e:
                 logging.warning(f"STTHandler[{self.activation_id}]: Error finishing microphone: {e}")
        else:
             logging.debug(f"STTHandler[{self.activation_id}]: Microphone object not found, cannot stop.")

    async def send_close_stream(self):
        """Sends the CloseStream message without waiting or disconnecting."""
        if self.dg_connection and await self.dg_connection.is_connected():
            try:
                logging.debug(f"STTHandler[{self.activation_id}]: Sending CloseStream message...")
                close_payload = { 'type': 'CloseStream' }
                await self.dg_connection.send(json.dumps(close_payload))
            except Exception as e:
                logging.warning(f"STTHandler[{self.activation_id}]: Error sending CloseStream: {e}")
        else:
             logging.debug(f"STTHandler[{self.activation_id}]: Cannot send CloseStream, connection not active.")

    async def _connection_loop(self):
        """Manages the connection attempts for this specific instance."""
        attempts = 0
        connect_start_time = time.monotonic() # Track start for overall timeout? Maybe not needed if per-attempt works

        while self.is_listening:
            if attempts >= self.MAX_CONNECT_ATTEMPTS:
                logging.error(f"STTHandler[{self.activation_id}]: Maximum connection attempts ({self.MAX_CONNECT_ATTEMPTS}) reached.")
                self._send_status("error")
                self.is_listening = False # Stop trying
                break

            attempts += 1
            current_attempt_timeout = ATTEMPT_TIMEOUTS_SEC[attempts - 1]

            logging.info(f"STTHandler[{self.activation_id}]: Attempting connection (Attempt {attempts}/{self.MAX_CONNECT_ATTEMPTS}, Timeout: {current_attempt_timeout}s)...")
            self._send_status("connecting")
            self._connection_established_event.clear()

            try:
                connection_successful = await asyncio.wait_for(
                    self._connect_and_stream(),
                    timeout=current_attempt_timeout
                )

                if connection_successful:
                    logging.info(f"STTHandler[{self.activation_id}]: Connection successful. Monitoring.")
                    attempts = 0 # Reset attempts on success

                    # Monitor loop
                    while self.is_listening and self.dg_connection and await self.dg_connection.is_connected():
                         await asyncio.sleep(0.2) # Check connection periodically

                    # --- NEW: Check if loop exited due to intentional stop --- >
                    if not self.is_listening:
                        logging.info(f"STTHandler[{self.activation_id}]: Intentional stop detected after monitoring loop exit. Bypassing retry.")
                        await self._disconnect() # Ensure clean disconnect
                        break # Exit the main while loop
                    # --- END NEW --- >

                    # --- Connection Lost Handling --- >
                    # Original check remains, but the NEW check above should catch most intentional stops
                    if self.is_listening: # If loop exited but we weren't told to stop explicitly
                         logging.warning(f"STTHandler[{self.activation_id}]: Connection lost. Cleaning up and will retry if attempts remain.")
                         await self._disconnect() # Clean up before retry
                         # Delay is applied at the end of the outer loop before next attempt
                    # else: # is_listening became False - handled by the NEW check above
                    #     logging.info(f"STTHandler[{self.activation_id}]: Stop requested while connected. Exiting loop.")
                    #     await self._disconnect() # Ensure clean disconnect
                    #     break # Exit loop cleanly

                else: # _connect_and_stream returned False (internal error)
                    logging.warning(f"STTHandler[{self.activation_id}]: Connection attempt {attempts} failed internally.")
                    # _disconnect() should have been called within _connect_and_stream
                    # Apply delay before next attempt if applicable


            except asyncio.TimeoutError:
                logging.warning(f"STTHandler[{self.activation_id}]: Connection attempt {attempts} timed out after {current_attempt_timeout}s.")
                # await self._disconnect() # Ensure cleanup on timeout -- REMOVE THIS
                # Allow loop to continue to retry logic
                # --- NEW: Report timeout ---
                try:
                    self.ui_action_queue.put_nowait(("connection_timeout", {"activation_id": self.activation_id}))
                except queue.Full:
                    logging.warning(f"STTHandler[{self.activation_id}]: UI Action queue full reporting connection timeout.")
                # --- END NEW ---

            except asyncio.CancelledError:
                 logging.info(f"STTHandler[{self.activation_id}]: Connection loop cancelled.")
                 await self._disconnect() # Still cleanup if cancelled explicitly
                 break # Exit loop

            except Exception as e:
                logging.error(f"STTHandler[{self.activation_id}]: Unexpected error in connection loop (Attempt {attempts}): {e}", exc_info=True)
                # await self._disconnect() # Ensure cleanup -- REMOVE THIS
                self._send_status("error") # Send error status on unexpected exception
                # Allow loop to continue to retry logic

            # --- Apply Retry Delay ---
            if self.is_listening and attempts < self.MAX_CONNECT_ATTEMPTS:
                 retry_delay_index = min(attempts - 1, len(RETRY_DELAYS_SEC) - 1)
                 delay = RETRY_DELAYS_SEC[retry_delay_index]
                 logging.info(f"STTHandler[{self.activation_id}]: Waiting {delay}s before retry.")
                 await asyncio.sleep(delay)

        # --- Loop End ---
        # --- NEW: Final Status Check --- >
        if self.is_listening:
             # If the loop finished but we were supposedly still listening, something went wrong.
             logging.error(f"STTHandler[{self.activation_id}]: Connection loop finished unexpectedly while is_listening was True!")
             self._send_status("error") # Send error if loop ended abnormally
             self.is_listening = False # Ensure state is correct
        else:
             logging.info(f"STTHandler[{self.activation_id}]: Connection loop finished normally (is_listening=False).")
            # Optional: Send disconnected status here if not already sent by _on_close?
            # It might be redundant if _on_close always fires.
        # --- END NEW ---
        await self._disconnect() # Final cleanup attempt


    async def _connect_and_stream(self) -> bool:
        """Attempts a single connection, sends buffer, starts microphone for this instance."""
        start_connect_monotonic = time.monotonic()
        connection_established_monotonic = None
        try:
            # --- Create connection instance ---
            self.dg_connection = self.client.listen.asynclive.v("1") # Use asynclive

            # --- Register handlers ---
            self.dg_connection.on(LiveTranscriptionEvents.Open, self._on_open)
            self.dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_message)
            self.dg_connection.on(LiveTranscriptionEvents.Metadata, self._on_metadata)
            self.dg_connection.on(LiveTranscriptionEvents.SpeechStarted, self._on_speech_started)
            self.dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)
            self.dg_connection.on(LiveTranscriptionEvents.Error, self._on_error)
            self.dg_connection.on(LiveTranscriptionEvents.Close, self._on_close)
            self.dg_connection.on(LiveTranscriptionEvents.Unhandled, self._on_unhandled)

            # --- Start the connection ---
            logging.debug(f"STTHandler[{self.activation_id}]: Attempting dg_connection.start...")
            start_success = await self.dg_connection.start(self.options)
            logging.debug(f"STTHandler[{self.activation_id}]: dg_connection.start completed. Success: {start_success}")
            if not start_success:
                 logging.error(f"STTHandler[{self.activation_id}]: Failed to start Deepgram connection.")
                 await self._disconnect() # Clean up attempt
                 return False

            # --- Wait for Open event ---
            try:
                 logging.debug(f"STTHandler[{self.activation_id}]: Waiting for connection established event...")
                 await asyncio.wait_for(self._connection_established_event.wait(), timeout=ATTEMPT_TIMEOUTS_SEC[0]/2 or 0.5) # Short wait for Open
                 logging.debug(f"STTHandler[{self.activation_id}]: Connection established event received.")
            except asyncio.TimeoutError:
                 logging.error(f"STTHandler[{self.activation_id}]: Timeout waiting for connection Open event.")
                 await self._disconnect()
                 return False

            connection_established_monotonic = time.monotonic()

            # --- Send Buffer ---
            if self.background_recorder:
                 connection_duration_sec = max(0, connection_established_monotonic - start_connect_monotonic)
                 duration_to_send_sec = connection_duration_sec
                 logging.info(f"STTHandler[{self.activation_id}]: Connection took {connection_duration_sec:.2f}s. Sending buffer for last {duration_to_send_sec:.2f}s.")
                 logging.debug(f"STTHandler[{self.activation_id}]: Getting buffer from recorder...")
                 pre_activation_buffer = self.background_recorder.get_buffer_last_n_seconds(duration_to_send_sec, connection_established_monotonic)
                 logging.debug(f"STTHandler[{self.activation_id}]: Buffer retrieved (size: {len(pre_activation_buffer) if pre_activation_buffer else 0} chunks). Sending...")

                 if pre_activation_buffer:
                     total_bytes = sum(len(chunk) for chunk in pre_activation_buffer)
                     logging.info(f"STTHandler[{self.activation_id}]: Sending pre-activation buffer: {len(pre_activation_buffer)} chunks, {total_bytes} bytes.")
                     for chunk in pre_activation_buffer:
                         if self.dg_connection and await self.dg_connection.is_connected():
                             try: await self.dg_connection.send(chunk); await asyncio.sleep(0.001) # Small yield
                             except Exception as send_err: logging.warning(f"STTHandler[{self.activation_id}]: Error sending buffer chunk: {send_err}"); break
                         else: logging.warning(f"STTHandler[{self.activation_id}]: Connection closed while sending buffer."); break
                 else:
                     logging.info(f"STTHandler[{self.activation_id}]: No pre-activation buffer to send.")
                 logging.debug(f"STTHandler[{self.activation_id}]: Finished sending buffer.")
            else:
                 logging.warning(f"STTHandler[{self.activation_id}]: BackgroundAudioRecorder not available, cannot send buffer.")

            # --- Microphone Setup ---
            # Ensure microphone is stopped if somehow existed before
            if self.microphone: self.microphone.finish()

            # Wrapper for sending mic data
            async def microphone_callback(data):
                 if self.dg_connection and await self.dg_connection.is_connected():
                     try:
                         await self.dg_connection.send(data)
                     except Exception as mic_send_err:
                         logging.warning(f"STTHandler[{self.activation_id}]: Error sending mic data: {mic_send_err}")
                         # Consider stopping mic or connection here?

            self.microphone = Microphone(microphone_callback)
            logging.debug(f"STTHandler[{self.activation_id}]: Microphone object created. Starting microphone...")
            self.microphone.start()
            logging.info(f"STTHandler[{self.activation_id}]: Microphone started.")
            return True # Connection successful

        except asyncio.CancelledError:
             logging.info(f"STTHandler[{self.activation_id}]: _connect_and_stream cancelled.")
             await self._disconnect() # Ensure cleanup
             return False
        except Exception as e:
            logging.error(f"STTHandler[{self.activation_id}]: Error during connect/stream setup: {e}", exc_info=True)
            await self._disconnect() # Ensure cleanup
            # Don't send error status here, let the connection loop handle retries/final error
            return False

# --- Remove the old stt_manager class definition ---
# class stt_manager: ... (Delete or comment out this old class)

# --- Example Usage (for testing the new class directly, needs adjustment) ---
async def example_main():
     # Needs full setup with queues, recorder, DG client etc.
     logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(filename)s:%(lineno)d - %(message)s')
     print("Testing STTConnectionHandler...")
     # Replace with actual initialization
     # dg_client = DeepgramClient(...)
     # status_q = queue.Queue()
     # transcript_q = queue.Queue()
     # recorder = BackgroundAudioRecorder(...)
     # recorder.start()
     # options = LiveOptions(...)

     # handler1 = STTConnectionHandler("test-1", dg_client, status_q, transcript_q, recorder, options)
     # await handler1.start_listening()
     # await asyncio.sleep(10)
     # await handler1.stop_listening()
     # recorder.stop()
     pass

if __name__ == '__main__':
     # asyncio.run(example_main())
     print("Run this module via the main vibe_app.py which will instantiate STTConnectionHandler.")
