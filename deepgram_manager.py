import asyncio
import logging
import queue
import time
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone,
)
from audio_buffer import BufferedAudioInput # Needed to send buffer

# --- Constants (Consider moving to a shared config or passing via options) --- >
MAX_CONNECT_ATTEMPTS = 3
CONNECT_RETRY_DELAY_SEC = 0.5
OVERALL_CONNECT_TIMEOUT_SEC = 5.0
BUFFER_SEND_DURATION_CAP_SEC = 5.0 # Max seconds of buffer to send


class DeepgramManager:
    """Manages the connection and transcription lifecycle with Deepgram."""

    def __init__(self, deepgram_client: DeepgramClient, status_q: queue.Queue, transcript_q: queue.Queue, buffered_audio: BufferedAudioInput):
        """
        Args:
            deepgram_client: An initialized DeepgramClient instance.
            status_q: Queue to send status updates (e.g., 'connecting', 'connected', 'error', 'disconnected').
            transcript_q: Queue to send received transcripts (interim/final).
            buffered_audio: The BufferedAudioInput instance to get the pre-activation buffer.
        """
        self.client = deepgram_client
        self.status_queue = status_q
        self.transcript_queue = transcript_q
        self.buffered_audio_input = buffered_audio # Store reference to audio buffer

        self.dg_connection = None
        self.microphone = None
        self.is_listening = False # Flag indicating if we intend to be listening
        self._connect_lock = asyncio.Lock() # Prevent concurrent start/stop operations
        self._connection_established_event = asyncio.Event() # Signal successful connection
        self._current_options = None # Store options used for the current/last connection attempt
        self._current_activation_id = None # Store ID for associating transcripts

        # Task management for the connection loop
        self._connection_task = None

        logging.info("DeepgramManager initialized.")

    def _send_status(self, status: str):
        """Helper to send status updates to the queue."""
        try:
            # Include the current listening intention state?
            self.status_queue.put_nowait(("connection_update", {"status": status}))
            logging.debug(f"DeepgramManager sent status: {status}")
        except queue.Full:
            logging.warning(f"Status queue full when sending DG status: {status}")
        except Exception as e:
            logging.error(f"Error sending DG status update: {e}")

    # --- Internal Deepgram Callbacks --- >
    # These run in the context of the Deepgram SDK's async management

    # --- FINAL ATTEMPT: Generic Signature + Extract from kwargs --- >
    async def _on_open(self, *args, **kwargs):
        # For Open, the OpenResponse seems to be in args[1]
        logging.debug(f"_on_open received args: {args}, kwargs: {kwargs}")
        # open_response = args[1] if len(args) > 1 else None # We don't use it, just log
        logging.info("Deepgram connection opened.")
        self._send_status("connected")
        self._connection_established_event.set()

    async def _on_message(self, *args, **kwargs):
        logging.debug(f"_on_message received args: {args}, kwargs: {kwargs}")
        # Extract 'result' from kwargs
        result = kwargs.get('result')
        if not result:
            logging.error(f"DGManager _on_message did not find 'result' in kwargs: {kwargs}")
            return
        try:
            transcript = result.channel.alternatives[0].transcript
            if transcript:
                message_type = "final" if result.is_final else "interim"
                transcript_data = {
                    "type": message_type,
                    "transcript": transcript,
                    "activation_id": self._current_activation_id
                }
                logging.debug(f"DGManager sending transcript ({message_type}) for activation {self._current_activation_id}: {transcript!r}")
                self.transcript_queue.put_nowait(transcript_data)
        except queue.Full:
            logging.warning(f"Transcript queue full. Discarding {message_type} transcript.")
        except (AttributeError, IndexError) as e:
            logging.error(f"Error processing Deepgram message in DGManager: {e} - Result: {result}")
        except Exception as e:
            logging.error(f"Unhandled error in DGManager _on_message: {e}", exc_info=True)

    async def _on_metadata(self, *args, **kwargs):
        logging.debug(f"_on_metadata received args: {args}, kwargs: {kwargs}")
        # Extract 'metadata' from kwargs
        metadata = kwargs.get('metadata')
        logging.debug(f"Deepgram Metadata (DGManager): {metadata}")

    async def _on_speech_started(self, *args, **kwargs):
        logging.debug(f"_on_speech_started received args: {args}, kwargs: {kwargs}")
        # Extract 'speech_started' from kwargs (though we don't use it)
        # speech_started = kwargs.get('speech_started')
        logging.debug("Deepgram Speech Started (DGManager)")

    async def _on_utterance_end(self, *args, **kwargs):
        logging.debug(f"_on_utterance_end received args: {args}, kwargs: {kwargs}")
        # Extract 'utterance_end' from kwargs (though we don't use it)
        # utterance_end = kwargs.get('utterance_end')
        logging.debug("Deepgram Utterance Ended (DGManager)")

    async def _on_error(self, *args, **kwargs):
        logging.debug(f"_on_error received args: {args}, kwargs: {kwargs}")
        # Extract 'error' from kwargs
        error = kwargs.get('error')
        if not error:
            logging.error(f"DGManager _on_error did not find 'error' in kwargs: {kwargs}. Args: {args}")
            return
        logging.error(f"Deepgram Handled Error (DGManager): {error}")
        self._send_status("error")

    async def _on_close(self, *args, **kwargs):
        logging.debug(f"_on_close received args: {args}, kwargs: {kwargs}")
        # Extract 'close' from kwargs (though we don't use it)
        # close_details = kwargs.get('close')
        logging.info("Deepgram connection closed (DGManager).")
        self._send_status("disconnected")

    async def _on_unhandled(self, *args, **kwargs):
        logging.debug(f"_on_unhandled received args: {args}, kwargs: {kwargs}")
        # Extract 'unhandled' from kwargs
        unhandled = kwargs.get('unhandled')
        logging.warning(f"Deepgram Unhandled Websocket Message (DGManager): {unhandled}")

    # --- Connection Management --- >

    async def start_listening(self, options: LiveOptions, activation_id):
        """Initiates the connection and listening process."""
        async with self._connect_lock: # Ensure only one start/stop operation at a time
            if self.is_listening:
                logging.warning("start_listening called while already listening or starting.")
                return

            logging.info(f"DeepgramManager: Starting listening process (Activation ID: {activation_id})...")
            self.is_listening = True # <-- SET FLAG IMMEDIATELY
            self._current_options = options
            self._current_activation_id = activation_id # Store activation ID

            # Cancel any previous connection task before starting a new one
            if self._connection_task and not self._connection_task.done():
                logging.debug("Cancelling previous connection task.")
                self._connection_task.cancel()
                try:
                    await self._connection_task
                except asyncio.CancelledError:
                    logging.debug("Previous connection task cancelled successfully.")
                except Exception as e:
                    logging.warning(f"Error awaiting previous connection task cancellation: {e}")

            # Start the connection loop as a background task
            self._connection_task = asyncio.create_task(self._connection_loop())


    async def stop_listening(self):
        """Stops the listening process and closes the connection."""
        async with self._connect_lock:
            if not self.is_listening and (not self._connection_task or self._connection_task.done()):
                logging.warning("stop_listening called but not actively listening or task already done.")
                # If task exists but is done, ensure cleanup happens just in case
                if self._connection_task and self._connection_task.done():
                     await self._disconnect() # Ensure resources are released
                     self._connection_task = None
                self.is_listening = False # Ensure flag is false
                return

            logging.info("DeepgramManager: Stopping listening process...")
            self.is_listening = False # Signal intention to stop

            # Cancel the connection loop task if it's running
            if self._connection_task and not self._connection_task.done():
                logging.debug("Cancelling connection task due to stop_listening.")
                self._connection_task.cancel()
                try:
                    await self._connection_task # Wait for cancellation to complete
                except asyncio.CancelledError:
                    logging.debug("Connection task cancelled successfully during stop.")
                except Exception as e:
                    logging.error(f"Error awaiting connection task cancellation during stop: {e}")

            # Ensure disconnection happens even if task was already done or cancelled
            await self._disconnect()
            self._connection_task = None
            self._current_activation_id = None # Clear activation ID on stop
            logging.info("DeepgramManager: Listening stopped.")


    async def _disconnect(self):
        """Safely disconnects the microphone and websocket connection."""
        logging.debug("DeepgramManager: Disconnecting...")
        if self.microphone:
            logging.debug("Finishing Deepgram microphone...")
            self.microphone.finish()
            self.microphone = None
            logging.info("Deepgram microphone finished.")

        if self.dg_connection:
            logging.debug("Finishing Deepgram connection...")
            try:
                await self.dg_connection.finish()
                logging.info("Deepgram connection finished.")
            except asyncio.CancelledError:
                 logging.warning("Deepgram finish cancelled during disconnect.")
            except Exception as e:
                logging.error(f"Error during dg_connection.finish: {e}")
            finally:
                self.dg_connection = None # Ensure it's None after attempt

        # Reset connection established event
        self._connection_established_event.clear()
        logging.debug("DeepgramManager: Disconnect complete.")


    async def _connection_loop(self):
        """Manages the connection attempts and stays connected while is_listening is True."""
        attempts = 0
        while self.is_listening:
            if attempts >= MAX_CONNECT_ATTEMPTS:
                logging.error(f"DeepgramManager: Maximum connection attempts ({MAX_CONNECT_ATTEMPTS}) reached.")
                self._send_status("error")
                self.is_listening = False # Stop trying
                break

            attempts += 1
            logging.info(f"DeepgramManager: Attempting connection (Attempt {attempts}/{MAX_CONNECT_ATTEMPTS})...")
            self._send_status("connecting")
            self._connection_established_event.clear() # Reset for this attempt

            try:
                # Use a timeout for the connection attempt itself
                connection_successful = await asyncio.wait_for(
                    self._connect_and_stream(),
                    timeout=OVERALL_CONNECT_TIMEOUT_SEC
                )

                if connection_successful:
                    logging.info("DeepgramManager: Connection successful. Monitoring connection.")
                    attempts = 0 # Reset attempts on success
                    # Keep running while connected and listening is intended
                    while self.is_listening and self.dg_connection and await self.dg_connection.is_connected():
                         await asyncio.sleep(0.5) # Check connection status periodically

                    # If loop exits, connection was lost or stop was requested
                    if self.is_listening:
                         logging.warning("DeepgramManager: Connection lost. Will attempt reconnect.")
                         await self._disconnect() # Clean up before reconnecting
                         # Status becomes 'disconnected' via _on_close or _disconnect
                         await asyncio.sleep(CONNECT_RETRY_DELAY_SEC) # Wait before retry
                    else:
                         logging.info("DeepgramManager: Stop requested while connected.")
                         await self._disconnect() # Clean stop
                         break # Exit connection loop

                else:
                    # _connect_and_stream returned False (internal error)
                    logging.warning(f"DeepgramManager: Connection attempt {attempts} failed internally.")
                    await self._disconnect() # Ensure cleanup
                    if self.is_listening: # Only retry if still supposed to be listening
                         await asyncio.sleep(CONNECT_RETRY_DELAY_SEC)

            except asyncio.TimeoutError:
                logging.warning(f"DeepgramManager: Connection attempt {attempts} timed out after {OVERALL_CONNECT_TIMEOUT_SEC}s.")
                await self._disconnect() # Ensure cleanup
                if self.is_listening: # Only retry if still supposed to be listening
                    await asyncio.sleep(CONNECT_RETRY_DELAY_SEC)

            except asyncio.CancelledError:
                 logging.info("DeepgramManager: Connection loop cancelled.")
                 await self._disconnect() # Ensure cleanup on cancellation
                 break # Exit loop

            except Exception as e:
                logging.error(f"DeepgramManager: Unexpected error in connection loop: {e}", exc_info=True)
                await self._disconnect() # Ensure cleanup
                self._send_status("error")
                if self.is_listening: # Only retry if still supposed to be listening
                     await asyncio.sleep(CONNECT_RETRY_DELAY_SEC)
                else:
                     break # Exit loop if error occurred during stop

        # Final cleanup if loop exits for any reason other than clean stop via stop_listening
        if not self.is_listening:
             logging.debug("DeepgramManager: Connection loop finished naturally or due to error/max attempts.")
             # Disconnect might have already been called, but call again for safety
             await self._disconnect()


    async def _connect_and_stream(self) -> bool:
        """Attempts a single connection, sends buffer, starts microphone."""
        start_connect_monotonic = time.monotonic()
        connection_established_monotonic = None
        try:
            self.dg_connection = self.client.listen.asyncwebsocket.v("1")

            # Register internal handlers
            self.dg_connection.on(LiveTranscriptionEvents.Open, self._on_open)
            self.dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_message)
            self.dg_connection.on(LiveTranscriptionEvents.Metadata, self._on_metadata)
            self.dg_connection.on(LiveTranscriptionEvents.SpeechStarted, self._on_speech_started)
            self.dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)
            self.dg_connection.on(LiveTranscriptionEvents.Error, self._on_error)
            self.dg_connection.on(LiveTranscriptionEvents.Close, self._on_close)
            self.dg_connection.on(LiveTranscriptionEvents.Unhandled, self._on_unhandled)

            # Start connection (without waiting here, wait for event)
            await self.dg_connection.start(self._current_options)

            # Wait for the _on_open callback to set the event
            await self._connection_established_event.wait() # Wait indefinitely until event is set or task cancelled
            connection_established_monotonic = time.monotonic()

            # --- Send Buffer --- >
            if self.buffered_audio_input:
                 connection_duration_sec = max(0, connection_established_monotonic - start_connect_monotonic)
                 duration_to_send_sec = min(connection_duration_sec, BUFFER_SEND_DURATION_CAP_SEC)
                 logging.info(f"DGManager: Connection took {connection_duration_sec:.2f}s. Sending buffer for last {duration_to_send_sec:.2f}s.")
                 pre_activation_buffer = self.buffered_audio_input.get_buffer_last_n_seconds(duration_to_send_sec, connection_established_monotonic)

                 if pre_activation_buffer:
                     total_bytes = sum(len(chunk) for chunk in pre_activation_buffer)
                     logging.info(f"DGManager: Sending pre-activation buffer: {len(pre_activation_buffer)} chunks, {total_bytes} bytes.")
                     for chunk in pre_activation_buffer:
                         if self.dg_connection and await self.dg_connection.is_connected():
                             try: await self.dg_connection.send(chunk); await asyncio.sleep(0.001)
                             except Exception as send_err: logging.warning(f"DGManager: Error sending buffer chunk: {send_err}"); break
                         else: logging.warning("DGManager: Connection lost while sending buffer."); break
                 else:
                     logging.info("DGManager: No pre-activation buffer to send.")
            else:
                 logging.warning("DGManager: BufferedAudioInput not available, cannot send buffer.")

            # --- Microphone Setup --- >
            original_send = self.dg_connection.send # Reference before wrapping
            async def logging_send_wrapper(data):
                # Check connection status before sending
                is_conn_connected = False
                if self.dg_connection:
                    try: is_conn_connected = await self.dg_connection.is_connected()
                    except Exception: pass # Ignore errors checking state
                if is_conn_connected:
                    try:
                        await original_send(data)
                    except Exception as mic_send_err:
                        logging.warning(f"DGManager: Error sending mic data: {mic_send_err}")
                        # Handle potential connection drop during send? Signal error?
                        # self._send_status("error")
                        # await self.stop_listening() # May cause issues if called from here
            self.microphone = Microphone(logging_send_wrapper)
            self.microphone.start()
            logging.info("DeepgramManager: Microphone started.")
            return True # Connection and streaming setup successful

        except asyncio.CancelledError:
             logging.info("DGManager: _connect_and_stream cancelled.")
             await self._disconnect() # Ensure cleanup
             return False # Indicate failure
        except Exception as e:
            logging.error(f"DGManager: Error during connect/stream setup: {e}", exc_info=True)
            await self._disconnect() # Ensure cleanup
            self._send_status("error") # Send error status on setup failure
            return False # Indicate failure