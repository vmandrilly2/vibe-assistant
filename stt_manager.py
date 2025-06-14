import asyncio
import logging
import queue
import time
import json
from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone,
)
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
        self._connection_task = None
        self.is_listening = False
        self._explicitly_stopped = False # Flag for intentional stop
        self._connection_established_event = asyncio.Event()
        self._connect_lock = asyncio.Lock() # Lock to prevent concurrent connect attempts
        self.microphone = None # Store microphone instance
        self.connection_start_time = None # Track when connection attempt starts
        self.retry_count = 0 # Track connection retries
        self.is_microphone_active = False # NEW: Track mic state
        self._accept_mic_data = False # NEW: Control sending in callback
        self.connection_closed_cleanly = False # Reset flag on new open

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

    def _send_mic_status_update(self, status: bool):
        """Helper to send mic status updates via UI action queue."""
        try:
            mic_data = {"activation_id": self.activation_id, "mic_active": status}
            self.ui_action_queue.put_nowait(("mic_status_update", mic_data))
            logging.debug(f"STTHandler[{self.activation_id}]: Sent mic_status_update ({status}) to main loop.")
        except queue.Full:
            logging.warning(f"UI Action queue full sending mic_status_update for {self.activation_id}.")
        except Exception as e:
            logging.error(f"Error sending mic_status_update: {e}")

    # --- Internal STT Callbacks (Now methods of the class) ---

    async def _on_open(self, sender, open, **kwargs):
        logging.debug(f"STTHandler[{self.activation_id}] _on_open received: {open}")
        logging.info(f"STT connection opened for ID: {self.activation_id}.")

        # --- NEW: Send established time --- >
        established_time = time.monotonic()
        try:
            timing_data = {"activation_id": self.activation_id, "type": "established", "timestamp": established_time}
            self.ui_action_queue.put_nowait(("connection_timing_update", timing_data))
        except queue.Full:
             logging.warning(f"STTHandler[{self.activation_id}]: UI action queue full sending established timing update.")
        # --- END NEW ---

        self._send_status("connected")
        self._connection_established_event.set()
        self.connection_closed_cleanly = False # Reset flag on new open

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

        # --- NEW: Send closed time --- >
        closed_time = time.monotonic()
        try:
             timing_data = {"activation_id": self.activation_id, "type": "closed", "timestamp": closed_time}
             self.ui_action_queue.put_nowait(("connection_timing_update", timing_data))
        except queue.Full:
             logging.warning(f"STTHandler[{self.activation_id}]: UI action queue full sending closed timing update from _on_close.")
        # --- END NEW ---

        # Clear the established event in case of unexpected closure
        self._connection_established_event.clear()
        # Don't set is_listening=False here, the connection_loop handles retry logic
        self.connection_closed_cleanly = True

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
            self._accept_mic_data = False # Ensure False on new start

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
            self._accept_mic_data = False # <<< SET FALSE IMMEDIATELY
            if not self.is_listening and (not self._connection_task or self._connection_task.done()):
                logging.warning(f"STTHandler[{self.activation_id}]: stop_listening called but not actively listening or task already done.")
                # Attempt cleanup just in case
                await self._disconnect()
                self.is_listening = False # Ensure state is correct
                return

            logging.info(f"STTHandler[{self.activation_id}]: Stopping listening process (timeout={timeout}s)...")
            self.is_listening = False # Signal loop to stop retrying/connecting
            self._explicitly_stopped = True # Mark as intentional stop

            # --- NEW: Cancel the connection task --- >
            if self._connection_task and not self._connection_task.done():
                logging.debug(f"STTHandler[{self.activation_id}]: Cancelling connection task due to stop_listening.")
                self._connection_task.cancel()
            # --- END NEW ---

            try:
                # This method only sets flags and cancels task now.
                logging.debug(f"STTHandler[{self.activation_id}]: stop_listening called. Internal state flags set, task cancelled.") # Updated log
            except Exception as e:
                # Should not happen as we are just setting flags
                logging.error(f"STTHandler[{self.activation_id}]: Unexpected error in stop_listening: {e}", exc_info=True)

    async def _disconnect(self):
        """Safely disconnects the microphone and websocket connection for this instance."""
        logging.debug(f"STTHandler[{self.activation_id}]: Disconnecting...")
        self._accept_mic_data = False # <<< SET FALSE IMMEDIATELY
        # Ensure is_listening is False to prevent connection loop from restarting

        if self.microphone:
            logging.debug(f"STTHandler[{self.activation_id}]: Finishing microphone...")
            try:
                self.microphone.finish()
                self.is_microphone_active = False # Clear flag
                self._send_mic_status_update(False) # <-- Signal False
                self.microphone = None
            except Exception as e:
                 logging.warning(f"STTHandler[{self.activation_id}]: Error finishing microphone: {e}")
            finally:
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
        self._accept_mic_data = False # <<< SET FALSE IMMEDIATELY
        if self.microphone:
            logging.debug(f"STTHandler[{self.activation_id}]: Finishing microphone...")
            try:
                self.microphone.finish()
                self.is_microphone_active = False # Clear flag
                self._send_mic_status_update(False) # <-- Signal False
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
        """Handles the connection lifecycle, including retries."""
        attempts = 0
        logging.debug(f"STTHandler[{self.activation_id}]: Starting connection loop.")
        while self.is_listening and attempts < self.MAX_CONNECT_ATTEMPTS:
            attempts += 1
            self._connection_established_event.clear()
            self.connection_closed_cleanly = False # Reset flag for new attempt

            logging.debug(f"STTHandler[{self.activation_id}]: Attempting connection {attempts}/{self.MAX_CONNECT_ATTEMPTS}...")
            connected = await self._connect_and_stream()

            logging.debug(f"STTHandler[{self.activation_id}]: _connect_and_stream finished for attempt {attempts}. Success: {connected}")

            if connected:
                # --- Connection Successful: Wait for it to end --- >
                logging.info(f"STTHandler[{self.activation_id}]: Connection established (Attempt {attempts}). Waiting for stream end or stop signal.")
                while self.is_listening:
                    # Check if the underlying connection object still exists and is connected
                    is_connected_flag = False
                    if self.dg_connection:
                        try:
                            # Use the is_connected method if available (check SDK docs)
                            # Assuming a method like this exists, replace if necessary
                            if hasattr(self.dg_connection, 'is_connected'):
                                is_connected_flag = await self.dg_connection.is_connected()
                            else: # Fallback if no specific method
                                # Check if websocket object exists and is open (might need adjustment based on SDK internals)
                                is_connected_flag = self.dg_connection.websocket and not self.dg_connection.websocket.closed
                        except Exception as conn_check_err:
                            logging.warning(f"STTHandler[{self.activation_id}]: Error checking connection status: {conn_check_err}")
                            is_connected_flag = False # Assume disconnected on error

                    if not is_connected_flag:
                        logging.warning(f"STTHandler[{self.activation_id}]: Detected connection closed while waiting.")
                        break # Exit inner wait loop, proceed to potential retry

                    await asyncio.sleep(0.1) # Poll connection status periodically

                # --- Exited inner wait loop --- >
                if not self.is_listening:
                    logging.info(f"STTHandler[{self.activation_id}]: Stop signal received while connection was active. Exiting outer loop.")
                    break # Exit the main connection loop cleanly
                else:
                    logging.warning(f"STTHandler[{self.activation_id}]: Connection closed unexpectedly. Will retry if attempts remain.")
                    # Proceed to retry logic outside this 'if connected:' block
                    # Ensure disconnect is called before retry
                    await self._disconnect()
                # --- End Connection Wait Logic ---
            else:
                 logging.warning(f"STTHandler[{self.activation_id}]: Connection attempt {attempts} failed internally.")
                 # Fall through to retry logic

            # --- Retry Logic --- >
            # Check if we should wait before retrying (only if not connected yet AND still listening AND attempts remain)
            if not connected and self.is_listening and attempts < self.MAX_CONNECT_ATTEMPTS:
                # --- Corrected Retry Delay --- >
                retry_delay = RETRY_DELAYS_SEC[attempts - 1] # Use the predefined delay for this attempt
                logging.info(f"STTHandler[{self.activation_id}]: Waiting {retry_delay}s before next connection attempt.")
                # --- END Corrected Delay ---

                # --- NEW: Send timeout update to main loop --- >
                try:
                    timeout_data = {"activation_id": self.activation_id}
                    self.ui_action_queue.put_nowait(("connection_timeout", timeout_data))
                except queue.Full:
                     logging.warning(f"STTHandler[{self.activation_id}]: UI action queue full sending connection_timeout update.")
                # --- END NEW ---

                try:
                    await asyncio.sleep(retry_delay) # Use the correct delay variable
                except asyncio.CancelledError:
                    logging.info(f"STTHandler[{self.activation_id}]: Connection loop cancelled during retry wait.")
                    self.is_listening = False # Ensure loop condition breaks
                    break

        # --- After Loop --- >
        if not self.is_listening:
             logging.info(f"STTHandler[{self.activation_id}]: Connection loop finished due to stop signal (is_listening=False).")
             # --- NEW: Ensure final status is sent if cancelled before full connection/error ---
             if attempts < self.MAX_CONNECT_ATTEMPTS and not self.connection_closed_cleanly:
                 # If we stopped early and didn't get a clean close or max attempts error, send disconnected.
                 logging.debug(f"STTHandler[{self.activation_id}]: Sending final 'disconnected' status after early stop.")
                 self._send_status("disconnected")
             # --- END NEW ---
        elif attempts >= self.MAX_CONNECT_ATTEMPTS:
            logging.error(f"STTHandler[{self.activation_id}]: Maximum connection attempts ({self.MAX_CONNECT_ATTEMPTS}) reached.")
            self._send_status("error")

        # --- Final Cleanup --- >
        await self._disconnect() # Ensure resources are released

        logging.debug(f"STTHandler[{self.activation_id}]: Connection loop fully exited.")

    async def _connect_and_stream(self) -> bool:
        """Establishes a single connection and handles the streaming. Returns True if closed cleanly/explicitly, False on connection error."""
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

            # --- SET FLAG: Okay to send live mic data now --- >
            self._accept_mic_data = True
            logging.debug(f"STTHandler[{self.activation_id}]: Set _accept_mic_data=True")

            # --- Microphone Setup ---
            # Ensure microphone is stopped if somehow existed before
            if self.microphone: self.microphone.finish()

            # Wrapper for sending mic data
            async def microphone_callback(data):
                 # --- ADD LOGGING --- >
                 current_time_mic_cb = time.monotonic()
                 logging.debug(f"STTHandler[{self.activation_id}]: microphone_callback invoked at {current_time_mic_cb:.3f}. Flag _accept_mic_data = {self._accept_mic_data}")
                 # --- END LOGGING --- >
                 # --- NEW: Check flag before sending --- >
                 if not self._accept_mic_data:
                     # logging.debug(f"STTHandler[{self.activation_id}]: Mic data received but sending blocked by flag.")
                     return # Do not send
                 # --- END NEW ---
                 if self.dg_connection and await self.dg_connection.is_connected():
                     try:
                         await self.dg_connection.send(data)
                     except Exception as mic_send_err:
                         logging.warning(f"STTHandler[{self.activation_id}]: Error sending mic data: {mic_send_err}")
                         # Consider stopping mic or connection here?

            self.microphone = Microphone(microphone_callback)
            logging.debug(f"STTHandler[{self.activation_id}]: Microphone object created. Starting microphone...")
            # Start microphone
            try:
                self.microphone.start()
                self.is_microphone_active = True # Set flag
                self._send_mic_status_update(True) # <-- Signal True
                logging.debug(f"STTHandler[{self.activation_id}]: Set is_microphone_active=True")
                logging.info(f"STTHandler[{self.activation_id}]: Microphone started.")
            except Exception as e:
                logging.error(f"STTHandler[{self.activation_id}]: Failed to start microphone: {e}", exc_info=True)
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

# --- Example Usage (for testing the new class directly, needs adjustment) ---
async def example_main():
     # Needs full setup with queues, recorder, DG client etc.
     logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(filename)s:%(lineno)d - %(message)s')
     print("Testing STTConnectionHandler...")
     pass

if __name__ == '__main__':
     # asyncio.run(example_main())
     print("Run this module via the main vibe_app.py which will instantiate STTConnectionHandler.")
