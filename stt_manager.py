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
# --- MODIFIED: Import BackgroundAudioRecorder --- >
from background_audio_recorder import BackgroundAudioRecorder

# --- Constants (Consider moving to a shared config or passing via options) --- >
MAX_CONNECT_ATTEMPTS = 3
# --- NEW: Escalating Timeouts and Specific Delays ---
ATTEMPT_TIMEOUTS_SEC = [1.0, 2.0, 3.0] # Timeout for each attempt
RETRY_DELAYS_SEC = [0.5, 0.2]         # Delay *before* attempt 2 and attempt 3
# --- END NEW ---
BUFFER_SEND_DURATION_CAP_SEC = 5.0 # Max seconds of buffer to send


class stt_manager:
    """Manages the connection and transcription lifecycle with the STT service (e.g., Deepgram)."""

    # --- MODIFIED: Init signature --- >
    def __init__(self, stt_client: DeepgramClient, status_q: queue.Queue, transcript_q: queue.Queue, background_recorder: BackgroundAudioRecorder):
        """
        Args:
            stt_client: An initialized DeepgramClient instance (or similar for other providers).
            status_q: Queue to send status updates (e.g., 'connecting', 'connected', 'error', 'disconnected').
            transcript_q: Queue to send received transcripts (interim/final).
            background_recorder: The BackgroundAudioRecorder instance to get the pre-activation buffer.
        """
        self.client = stt_client # Use generic name
        self.status_queue = status_q
        self.transcript_queue = transcript_q
        # --- MODIFIED: Use generic name --- >
        self.background_recorder = background_recorder

        self.dg_connection = None # Keep specific name for Deepgram connection object for now
        self.microphone = None
        self.is_listening = False # Flag indicating if we intend to be listening
        self._connect_lock = asyncio.Lock() # Prevent concurrent start/stop operations
        self._connection_established_event = asyncio.Event() # Signal successful connection
        self._current_options = None # Store options used for the current/last connection attempt
        self._current_activation_id = None # Store ID for associating transcripts

        # Task management for the connection loop
        self._connection_task = None

        logging.info("stt_manager initialized.") # Use class name

    def _send_status(self, status: str):
        """Helper to send status updates to the queue."""
        try:
            # Include the current listening intention state?
            self.status_queue.put_nowait(("connection_update", {"status": status}))
            # --- MODIFIED: Use class name in log --- >
            logging.debug(f"stt_manager sent status: {status}")
        except queue.Full:
            logging.warning(f"Status queue full when sending STT status: {status}")
        except Exception as e:
            logging.error(f"Error sending STT status update: {e}")

    # --- Internal STT Callbacks (Deepgram specific for now) --- >
    # These run in the context of the SDK's async management

    async def _on_open(self, *args, **kwargs):
        logging.debug(f"_on_open received args: {args}, kwargs: {kwargs}")
        logging.info("STT connection opened.") # Generic message
        self._send_status("connected")
        self._connection_established_event.set()

    async def _on_message(self, *args, **kwargs):
        # --- MODIFIED: Use class name in logs --- >
        logging.debug(f"_on_message received args: {args}, kwargs: {kwargs}")
        result = kwargs.get('result')
        if not result:
            logging.error(f"STTManager _on_message did not find 'result' in kwargs: {kwargs}")
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
                logging.debug(f"STTManager sending transcript ({message_type}) for activation {self._current_activation_id}: {transcript!r}")
                self.transcript_queue.put_nowait(transcript_data)
        except queue.Full:
            logging.warning(f"Transcript queue full. Discarding {message_type} transcript.")
        except (AttributeError, IndexError) as e:
            logging.error(f"Error processing STT message in STTManager: {e} - Result: {result}")
        except Exception as e:
            logging.error(f"Unhandled error in STTManager _on_message: {e}", exc_info=True)

    async def _on_metadata(self, *args, **kwargs):
        logging.debug(f"_on_metadata received args: {args}, kwargs: {kwargs}")
        metadata = kwargs.get('metadata')
        logging.debug(f"STT Metadata (STTManager): {metadata}")

    async def _on_speech_started(self, *args, **kwargs):
        logging.debug(f"_on_speech_started received args: {args}, kwargs: {kwargs}")
        logging.debug("STT Speech Started (STTManager)")

    async def _on_utterance_end(self, *args, **kwargs):
        logging.debug(f"_on_utterance_end received args: {args}, kwargs: {kwargs}")
        logging.debug("STT Utterance Ended (STTManager)")

    async def _on_error(self, *args, **kwargs):
        logging.debug(f"_on_error received args: {args}, kwargs: {kwargs}")
        error = kwargs.get('error')
        if not error:
            logging.error(f"STTManager _on_error did not find 'error' in kwargs: {kwargs}. Args: {args}")
            return
        logging.error(f"STT Handled Error (STTManager): {error}")
        self._send_status("error")

    async def _on_close(self, *args, **kwargs):
        logging.debug(f"_on_close received args: {args}, kwargs: {kwargs}")
        logging.info("STT connection closed (STTManager).") # Generic message
        self._send_status("disconnected")

    async def _on_unhandled(self, *args, **kwargs):
        logging.debug(f"_on_unhandled received args: {args}, kwargs: {kwargs}")
        unhandled = kwargs.get('unhandled')
        logging.warning(f"STT Unhandled Websocket Message (STTManager): {unhandled}")

    # --- Connection Management --- >

    async def start_listening(self, options: LiveOptions, activation_id):
        """Initiates the connection and listening process."""
        async with self._connect_lock:
            if self.is_listening:
                logging.warning("start_listening called while already listening or starting.")
                return
            # --- MODIFIED: Use class name in log --- >
            logging.info(f"stt_manager: Starting listening process (Activation ID: {activation_id})...")
            self.is_listening = True
            self._current_options = options
            self._current_activation_id = activation_id

            if self._connection_task and not self._connection_task.done():
                logging.debug("Cancelling previous connection task.")
                self._connection_task.cancel()
                try:
                    await self._connection_task
                except asyncio.CancelledError:
                    logging.debug("Previous connection task cancelled successfully.")
                except Exception as e:
                    logging.warning(f"Error awaiting previous connection task cancellation: {e}")

            self._connection_task = asyncio.create_task(self._connection_loop())


    async def stop_listening(self):
        """Stops the listening process and closes the connection."""
        async with self._connect_lock:
            if not self.is_listening and (not self._connection_task or self._connection_task.done()):
                logging.warning("stop_listening called but not actively listening or task already done.")
                if self._connection_task and self._connection_task.done():
                     await self._disconnect()
                     self._connection_task = None
                self.is_listening = False
                return
            # --- MODIFIED: Use class name in log --- >
            logging.info("stt_manager: Stopping listening process...")
            self.is_listening = False

            if self._connection_task and not self._connection_task.done():
                logging.debug("Cancelling connection task due to stop_listening.")
                self._connection_task.cancel()
                try:
                    await self._connection_task
                except asyncio.CancelledError:
                    logging.debug("Connection task cancelled successfully during stop.")
                except Exception as e:
                    logging.error(f"Error awaiting connection task cancellation during stop: {e}")

            await self._disconnect()
            self._connection_task = None
            self._current_activation_id = None
            # --- MODIFIED: Use class name in log --- >
            logging.info("stt_manager: Listening stopped.")


    async def _disconnect(self):
        """Safely disconnects the microphone and websocket connection."""
        # --- MODIFIED: Use class name in logs --- >
        logging.debug("stt_manager: Disconnecting...")
        if self.microphone:
            logging.debug("Finishing STT microphone...")
            self.microphone.finish()
            self.microphone = None
            logging.info("STT microphone finished.")

        if self.dg_connection:
            logging.debug("Finishing STT connection...")
            try:
                await self.dg_connection.finish()
                logging.info("STT connection finished.")
            except asyncio.CancelledError:
                 logging.warning("STT finish cancelled during disconnect.")
            except Exception as e:
                logging.error(f"Error during STT connection finish: {e}")
            finally:
                self.dg_connection = None

        self._connection_established_event.clear()
        logging.debug("stt_manager: Disconnect complete.")


    async def _connection_loop(self):
        """Manages the connection attempts and stays connected while is_listening is True."""
        attempts = 0
        while self.is_listening:
            if attempts >= MAX_CONNECT_ATTEMPTS:
                # --- MODIFIED: Use class name in log --- >
                logging.error(f"stt_manager: Maximum connection attempts ({MAX_CONNECT_ATTEMPTS}) reached.")
                self._send_status("error")
                self.is_listening = False
                break

            attempts += 1
            # --- MODIFIED: Get timeout and delay for this specific attempt ---
            current_attempt_timeout = ATTEMPT_TIMEOUTS_SEC[attempts - 1]
            # Delay happens *before* the next attempt, calculate it now if needed for logging/logic
            # But apply it later in the loop if the attempt fails.
            # --- END MODIFIED ---

            # --- MODIFIED: Use class name in log and mention attempt timeout --- >
            logging.info(f"stt_manager: Attempting connection (Attempt {attempts}/{MAX_CONNECT_ATTEMPTS}, Timeout: {current_attempt_timeout}s)...")
            self._send_status("connecting")
            self._connection_established_event.clear()

            try:
                # --- MODIFIED: Use per-attempt timeout ---
                connection_successful = await asyncio.wait_for(
                    self._connect_and_stream(),
                    timeout=current_attempt_timeout
                )
                # --- END MODIFIED ---

                if connection_successful:
                    # --- MODIFIED: Use class name in log --- >
                    logging.info("stt_manager: Connection successful. Monitoring connection.")
                    attempts = 0 # Reset attempts on success
                    # --- MODIFIED: Check connection status slightly less frequently ---
                    while self.is_listening and self.dg_connection and await self.dg_connection.is_connected():
                         await asyncio.sleep(0.2) # Reduced sleep from 0.5 to 0.2 for faster detection of disconnect? Or keep 0.5? Let's try 0.2

                    # --- MODIFIED: Connection Lost Handling ---
                    if self.is_listening:
                         # --- MODIFIED: Use class name in log --- >
                         logging.warning("stt_manager: Connection lost. Will attempt reconnect.")
                         await self._disconnect()
                         # No delay needed here, the loop will continue and apply the correct retry delay *before* the next attempt if attempts > 0
                    # --- END MODIFIED ---
                    else:
                         # --- MODIFIED: Use class name in log --- >
                         logging.info("stt_manager: Stop requested while connected.")
                         await self._disconnect()
                         break # Exit loop cleanly on stop request

                else:
                    # --- MODIFIED: Use class name in log --- >
                    logging.warning(f"stt_manager: Connection attempt {attempts} failed internally.")
                    await self._disconnect()
                    if self.is_listening and attempts < MAX_CONNECT_ATTEMPTS:
                         # --- MODIFIED: Apply specific delay before next attempt ---
                         current_retry_delay = RETRY_DELAYS_SEC[attempts - 1]
                         logging.info(f"Waiting {current_retry_delay}s before retry.")
                         await asyncio.sleep(current_retry_delay)
                         # --- END MODIFIED ---

            except asyncio.TimeoutError:
                # --- MODIFIED: Use class name in log, mention specific timeout --- >
                logging.warning(f"stt_manager: Connection attempt {attempts} timed out after {current_attempt_timeout}s.")
                await self._disconnect()
                if self.is_listening and attempts < MAX_CONNECT_ATTEMPTS:
                     # --- MODIFIED: Apply specific delay before next attempt ---
                     current_retry_delay = RETRY_DELAYS_SEC[attempts - 1]
                     logging.info(f"Waiting {current_retry_delay}s before retry.")
                     await asyncio.sleep(current_retry_delay)
                     # --- END MODIFIED ---

            except asyncio.CancelledError:
                 # --- MODIFIED: Use class name in log --- >
                 logging.info("stt_manager: Connection loop cancelled.")
                 await self._disconnect()
                 break

            except Exception as e:
                # --- MODIFIED: Use class name in log --- >
                logging.error(f"stt_manager: Unexpected error in connection loop (Attempt {attempts}): {e}", exc_info=True)
                await self._disconnect()
                self._send_status("error")
                if self.is_listening and attempts < MAX_CONNECT_ATTEMPTS:
                     # --- MODIFIED: Apply specific delay before next attempt ---
                     current_retry_delay = RETRY_DELAYS_SEC[attempts - 1]
                     logging.info(f"Waiting {current_retry_delay}s before retry after error.")
                     await asyncio.sleep(current_retry_delay)
                     # --- END MODIFIED ---
                else:
                     # Ensure we break if error happens on last attempt or if not listening
                     self.is_listening = False # Mark as not listening on unhandled error exit
                     break # Exit loop

        # --- Cleanup check ---
        if not self.is_listening:
             # --- MODIFIED: Use class name in log --- >
             logging.info("stt_manager: Connection loop finished (stop requested, max attempts, or error).")
             await self._disconnect() # Ensure disconnect on any exit path


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

            await self.dg_connection.start(self._current_options)

            await self._connection_established_event.wait()
            connection_established_monotonic = time.monotonic()

            # --- Send Buffer --- >
            # --- MODIFIED: Use generic recorder variable --- >
            if self.background_recorder:
                 connection_duration_sec = max(0, connection_established_monotonic - start_connect_monotonic)
                 duration_to_send_sec = min(connection_duration_sec, BUFFER_SEND_DURATION_CAP_SEC)
                 # --- MODIFIED: Use class name in log --- >
                 logging.info(f"STTManager: Connection took {connection_duration_sec:.2f}s. Sending buffer for last {duration_to_send_sec:.2f}s.")
                 # --- MODIFIED: Use generic recorder variable --- >
                 pre_activation_buffer = self.background_recorder.get_buffer_last_n_seconds(duration_to_send_sec, connection_established_monotonic)

                 if pre_activation_buffer:
                     total_bytes = sum(len(chunk) for chunk in pre_activation_buffer)
                     # --- MODIFIED: Use class name in log --- >
                     logging.info(f"STTManager: Sending pre-activation buffer: {len(pre_activation_buffer)} chunks, {total_bytes} bytes.")
                     for chunk in pre_activation_buffer:
                         if self.dg_connection and await self.dg_connection.is_connected():
                             try: await self.dg_connection.send(chunk); await asyncio.sleep(0.001)
                             except Exception as send_err: logging.warning(f"STTManager: Error sending buffer chunk: {send_err}"); break
                         else: logging.warning("STTManager: Connection lost while sending buffer."); break
                 else:
                     # --- MODIFIED: Use class name in log --- >
                     logging.info("STTManager: No pre-activation buffer to send.")
            else:
                 # --- MODIFIED: Use class name in log --- >
                 logging.warning("STTManager: BackgroundAudioRecorder not available, cannot send buffer.")

            # --- Microphone Setup --- >
            original_send = self.dg_connection.send
            async def logging_send_wrapper(data):
                is_conn_connected = False
                if self.dg_connection:
                    try: is_conn_connected = await self.dg_connection.is_connected()
                    except Exception: pass
                if is_conn_connected:
                    try:
                        await original_send(data)
                    except Exception as mic_send_err:
                        # --- MODIFIED: Use class name in log --- >
                        logging.warning(f"STTManager: Error sending mic data: {mic_send_err}")
            self.microphone = Microphone(logging_send_wrapper)
            self.microphone.start()
            # --- MODIFIED: Use class name in log --- >
            logging.info("stt_manager: Microphone started.")
            return True

        except asyncio.CancelledError:
             # --- MODIFIED: Use class name in log --- >
             logging.info("stt_manager: _connect_and_stream cancelled.")
             await self._disconnect()
             return False
        except Exception as e:
            # --- MODIFIED: Use class name in log --- >
            logging.error(f"stt_manager: Error during connect/stream setup: {e}", exc_info=True)
            await self._disconnect()
            self._send_status("error")
            return False
