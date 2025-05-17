import asyncio
import logging
import time
import uuid
import os
from typing import Optional, Dict, Any

from deepgram import (
    DeepgramClient, DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

# Import necessary components - assuming they are in the same directory or install path
# These might need adjustment based on your project structure
from background_audio_recorder import BackgroundAudioRecorder
# from global_variables_manager import GlobalVariablesManager
from constants import (
    STATE_STT_SESSION_STATUS_TEMPLATE,
    STATE_SESSION_INTERIM_TRANSCRIPT_TEMPLATE,
    STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE,
    STATE_SESSION_FINAL_TRANSCRIPT_FULL_TEMPLATE, # Assuming this state key exists
    STATE_SESSION_RECOGNIZED_ACTIONS_TEMPLATE,
    STATE_SESSION_HISTORY_TEMPLATE,
    STATE_APP_STATUS, STATE_ERROR_MESSAGE, # For potential error reporting
    STATE_INPUT_DICTATION_KEY_PRESSED,
    STATE_AUDIO_STATUS, STATE_AUDIO_LATEST_CHUNK_TIMESTAMP, # If needed for audio stream
    AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, # Removed AUDIO_FORMAT
    STATE_APP_CURRENT_STT_SESSION_ID, # GVM key for current session ID
    STATE_SESSION_PREFIX, # Corrected: Changed from STATE_SESSIONS_PREFIX to STATE_SESSION_PREFIX
    CONFIG_DEEPGRAM_PREFIX, # Prefix for Deepgram specific config
    CONFIG_GENERAL_PREFIX # Added for language config
)
from i18n import _ # For potential translated log messages

logger = logging.getLogger(__name__)

# Forward declare types if needed and GVM/AudioRecorder are in separate files
# GlobalVariablesManager = Any
# BackgroundAudioRecorder = Any

class STTManager:
    """Manages Speech-to-Text connections and processing using Deepgram."""

    def __init__(self, dg_client: DeepgramClient, audio_recorder: BackgroundAudioRecorder, gvm: Any):
        """Initializes the STTManager.

        Args:
            dg_client: An initialized DeepgramClient.
            audio_recorder: The BackgroundAudioRecorder instance.
            gvm: The GlobalVariablesManager instance for state access.
        """
        self.dg_client = dg_client
        self.audio_recorder = audio_recorder
        self.gvm = gvm
        self.sessions: Dict[str, Any] = {} # activation_id -> STTConnectionHandler instance (or similar)
        self._lock = asyncio.Lock()
        self._session_counter = 0
        self._dg_connection = None # The active Deepgram LiveTranscription connection
        self._connection_task: Optional[asyncio.Task] = None
        self._session_id: Optional[str] = None
        self._language: str = "en-US" # Default, should be updated from GVM
        self._stop_processing = asyncio.Event() # Local event to signal stop within a session
        self._connection_open = asyncio.Event() # Signals successful websocket opening
        self._current_model = "nova-2" # Default, update from GVM/config
        self._retry_count = 0
        self._max_retries = 3
        self._language_update_event = asyncio.Event() # To signal language change
        self._active_dg_connection: Optional[LiveTranscriptionEvents] = None
        self._current_session_id: Optional[str] = None
        self._stop_event = asyncio.Event() # Event to stop the current session processing

    async def init(self):
        """(Re)Initializes the STTManager, potentially loading config."""
        logger.info("STTManager initialized.")
        return True

    async def _connect_to_deepgram(self) -> bool:
        """Establishes connection to Deepgram Live Transcription for the current session."""
        if not self.dg_client:
            logger.error("Deepgram client not initialized. Cannot connect.")
            if self._session_id:
                await self.gvm.set(STATE_STT_SESSION_STATUS_TEMPLATE.format(session_id=self._session_id), "error_no_client")
            return False

        if not self._session_id:
             logger.error("Session ID not set before connecting to Deepgram.")
             return False

        session_status_key = STATE_STT_SESSION_STATUS_TEMPLATE.format(session_id=self._session_id)

        try:
            # Determine language and model based on current GVM config
            current_language_code = await self.gvm.get(f"{CONFIG_GENERAL_PREFIX}.language", "en")
            self._language = await self._get_deepgram_language_code(current_language_code)
            self._current_model = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.model", "nova-2")

            logger.info(f"STTManager using Lang: {self._language}, Model: {self._current_model} for session {self._session_id}")

            # Fetch other Deepgram options from GVM/config
            punctuate = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.punctuate", True)
            interim_results = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.interim_results", True)
            try:
                utterance_end_ms_conf = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.utterance_end_ms", "1000") # Default to string
            except ValueError: # Should not happen if default is string
                logger.warning(f"Could not get utterance_end_ms. Using default '1000'.")
                utterance_end_ms_conf = "1000"
            vad_events = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.vad_events", True)
            smart_format = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.smart_format", True)
            endpointing_conf = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.endpointing", 300)
            numerals = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.numerals", True)

            # Ensure numeric options are integers
            try:
                endpointing = int(endpointing_conf)
            except ValueError:
                logger.warning(f"Could not convert endpointing '{endpointing_conf}' to int. Using default 300.")
                endpointing = 300

            options = LiveOptions(
                model=self._current_model,
                language=self._language,
                encoding="linear16",
                sample_rate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS, # Should be 1
                interim_results=interim_results,
                punctuate=punctuate,
                utterance_end_ms=utterance_end_ms_conf, # Pass as string
                vad_events=vad_events,
                smart_format=smart_format,
                numerals=numerals,
                endpointing=endpointing
                # Removed multichannel=False
            )

            await self.gvm.set(session_status_key, "connecting")
            logger.info(f"Attempting Deepgram connection (Session: {self._session_id}, Lang: {self._language}, Model: {self._current_model})...")

            self._connection_open.clear() # Ensure event is clear before connecting
            self._dg_connection = self.dg_client.listen.asynclive.v("1")

            self._dg_connection.on(LiveTranscriptionEvents.Open, self._on_open)
            self._dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_message)
            self._dg_connection.on(LiveTranscriptionEvents.Metadata, self._on_metadata)
            self._dg_connection.on(LiveTranscriptionEvents.SpeechStarted, self._on_speech_started)
            self._dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)
            self._dg_connection.on(LiveTranscriptionEvents.Error, self._on_error)
            self._dg_connection.on(LiveTranscriptionEvents.Close, self._on_close)

            await self._dg_connection.start(options)
            
            # Wait for the connection to actually open via the event handler
            try:
                 await asyncio.wait_for(self._connection_open.wait(), timeout=5.0) # Wait up to 5s for open
            except asyncio.TimeoutError:
                 logger.error(f"Timeout waiting for Deepgram connection Open event for session {self._session_id}.")
                 await self.gvm.set(session_status_key, "error_connect_timeout")
                 await self._disconnect_from_deepgram(graceful_finish=False) # Force close attempt
                 return False

            if not self._connection_open.is_set(): # Check if closed immediately
                 logger.error(f"Deepgram connection attempt failed or closed immediately (Session: {self._session_id}).")
                 await self.gvm.set(session_status_key, "error_connect_fail")
                 # Disconnect might have already been called by _on_close or _on_error
                 return False

            logger.info(f"Deepgram connection established (Session: {self._session_id}).")
            await self.gvm.set(session_status_key, "connected")
            self._retry_count = 0
            return True

        except asyncio.CancelledError:
             logger.info(f"Deepgram connection task cancelled (Session: {self._session_id}).")
             await self.gvm.set(session_status_key, "cancelled")
             await self._disconnect_from_deepgram(graceful_finish=False)
             return False
        except Exception as e:
            logger.error(f"Error connecting to Deepgram (Session: {self._session_id}): {e}", exc_info=True)
            await self.gvm.set(session_status_key, "error_connect")
            await self.gvm.set(STATE_ERROR_MESSAGE, f"Deepgram Connect Error: {e}")
            await self._disconnect_from_deepgram(graceful_finish=False)
            # Retry logic
            self._retry_count += 1
            if self._retry_count <= self._max_retries:
                 wait_time = 2 ** self._retry_count
                 logger.info(f"Retrying connection in {wait_time} seconds (Attempt {self._retry_count}/{self._max_retries}, Session: {self._session_id})...")
                 await asyncio.sleep(wait_time)
                 return await self._connect_to_deepgram()
            else:
                 logger.error(f"Max connection retries reached (Session: {self._session_id}).")
                 await self.gvm.set(session_status_key, "error_max_retries")
                 return False

    async def _stream_audio(self):
        """Streams buffered and live audio data to Deepgram for the current session."""
        if not self._dg_connection or not self._connection_open.is_set():
            logger.warning(f"Cannot stream audio, connection not ready (Session: {self._session_id}).")
            return

        try:
            # Send buffered audio first (adjust duration as needed)
            initial_buffer_duration = await self.gvm.get("config.audio.initial_buffer_send_duration_s", 2.0)
            buffered_chunks = await self.audio_recorder.get_buffered_chunks(initial_buffer_duration)
            if buffered_chunks:
                logger.info(f"Sending {len(buffered_chunks)} buffered audio chunks ({initial_buffer_duration}s) to Deepgram (Session: {self._session_id}).")
                for chunk in buffered_chunks:
                    if not self._connection_open.is_set() or self._stop_processing.is_set(): break
                    # Log before sending buffered chunk
                    logger.debug(f"Sending buffered audio chunk (size: {len(chunk)}) to Deepgram for session {self._session_id}")
                    await self._dg_connection.send(chunk)
                    await asyncio.sleep(0.005) # Small yield

            logger.debug(f"Finished sending buffer. Starting live stream poll (Session: {self._session_id}).")
            # Start polling from the time the buffer sending finished
            last_polled_timestamp = time.monotonic() 

            while self._connection_open.is_set() and not self._stop_processing.is_set():
                # Get new chunks since the last poll time using the dedicated method
                new_chunks_with_ts = await self.audio_recorder.get_live_chunks_since(last_polled_timestamp)
                
                if new_chunks_with_ts:
                    # new_chunks_with_ts is already sorted by timestamp because deque is ordered
                    timestamp_to_log = new_chunks_with_ts[-1][0] # Get timestamp of last chunk
                    logger.debug(f"Retrieved {len(new_chunks_with_ts)} new live chunks up to ts {timestamp_to_log:.2f} from recorder. Sending...")
                    
                    for ts, chunk_bytes in new_chunks_with_ts:
                        if not self._connection_open.is_set() or self._stop_processing.is_set(): break
                        logger.debug(f"Sending live audio chunk (ts: {ts:.2f}, size: {len(chunk_bytes)}) to Deepgram for session {self._session_id}")
                        await self._dg_connection.send(chunk_bytes)
                        last_polled_timestamp = ts # Update last polled time
                        # Minimal yield might still be good practice after send
                        await asyncio.sleep(0.001) 
                    # Small sleep after processing a batch
                    await asyncio.sleep(0.01)
                else:
                    # Wait a bit longer if no new chunks were found
                    await asyncio.sleep(0.05)
                # < --- End Polling Logic --- 

        except asyncio.CancelledError:
            logger.info(f"Audio streaming task cancelled (Session: {self._session_id}).")
        except Exception as e:
            # Avoid logging errors if we know the connection is closing/closed
            if self._connection_open.is_set() and not self._stop_processing.is_set():
                logger.error(f"Error during audio streaming (Session: {self._session_id}): {e}", exc_info=True)
                session_status_key = STATE_STT_SESSION_STATUS_TEMPLATE.format(session_id=self._session_id)
                await self.gvm.set(session_status_key, "error_stream")
                await self.gvm.set(STATE_ERROR_MESSAGE, f"Deepgram Stream Error: {e}")
            else:
                 logger.debug(f"Audio streaming error occurred during shutdown/disconnection (Session: {self._session_id}): {e}")

    async def _disconnect_from_deepgram(self, graceful_finish=True):
        """Closes the Deepgram connection."""
        connection_was_open = self._connection_open.is_set()
        self._connection_open.clear() # Mark as closed immediately
        
        if self._dg_connection:
            logger.debug(f"Disconnecting Deepgram connection (Session: {self._session_id}, Graceful: {graceful_finish})...")
            try:
                logger.debug("Attempting to finish Deepgram connection...")
                # This signals to Deepgram that we're done sending audio.
                # Replace finish(force=True) with finish() if force causes issues or is deprecated
                # await self._dg_connection.finish(force=True) 
                await self._dg_connection.finish() 
                logger.debug("Deepgram finish() called.")
            except Exception as e:
                logger.error(f"Error finishing Deepgram connection: {e}", exc_info=True)
            finally:
                self._dg_connection = None
                logger.info("Deepgram connection set to None.")

        # Update GVM status if it wasn't already set to error/disconnected
        if self._session_id:
             session_status_key = STATE_STT_SESSION_STATUS_TEMPLATE.format(session_id=self._session_id)
             current_status = await self.gvm.get(session_status_key, "unknown")
             if connection_was_open and current_status not in ["error_connect", "error_dg", "error_stream", "error_max_retries", "disconnected", "cancelled"]:
                  await self.gvm.set(session_status_key, "disconnected")
        # Do not clear session_id here, wait for _process_session to finish

    # --- Deepgram Event Handlers --- >
    async def _on_open(self, sender, open, **kwargs):
        """Handles the connection open event."""
        logger.info(f"Deepgram connection opened (Session: {self._session_id}): {open}")
        self._connection_open.set()

    async def _on_message(self, sender, result, **kwargs):
        """Handles transcript messages (both interim and final)."""
        if not self._session_id:
             logger.warning("Received DG message but session_id is not set.")
             return
        try:
            # Log the raw result for debugging
            try:
                # Attempt to log as JSON if possible, otherwise plain string
                import json
                raw_result_str = json.dumps(result.model_dump(mode='json') if hasattr(result, 'model_dump') else vars(result))
                logger.debug(f"Raw Deepgram result (Session: {self._session_id}): {raw_result_str}")
            except Exception as log_exc:
                logger.debug(f"Raw Deepgram result (Session: {self._session_id}, could not serialize to JSON): {result}")
                
            if not result or not hasattr(result, 'channel') or not hasattr(result.channel, 'alternatives') or not result.channel.alternatives:
                logger.debug(f"Received empty or invalid transcript structure: {result}")
                return
                
            transcript = result.channel.alternatives[0].transcript
            if transcript:
                if result.is_final:
                    logger.debug(f"[Final Segment:{self._session_id}] {transcript}")
                    await self.gvm.set(STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE.format(session_id=self._session_id), transcript)
                    # Consider appending to a full transcript state if needed by logic
                    # current_full = await self.gvm.get(STATE_SESSION_FINAL_TRANSCRIPT_FULL_TEMPLATE.format(session_id=self._session_id), "")
                    # await self.gvm.set(STATE_SESSION_FINAL_TRANSCRIPT_FULL_TEMPLATE.format(session_id=self._session_id), current_full + transcript + " ")
                else:
                    # logger.debug(f"[Interim:{self._session_id}] {transcript}")
                    await self.gvm.set(STATE_SESSION_INTERIM_TRANSCRIPT_TEMPLATE.format(session_id=self._session_id), transcript)
        except Exception as e:
             logger.error(f"Error processing transcript message (Session: {self._session_id}): {e}", exc_info=True)

    async def _on_metadata(self, sender, metadata, **kwargs):
        """Handles metadata messages."""
        logger.debug(f"Deepgram metadata received (Session: {self._session_id}): {metadata}")
        # Optional: Store metadata in GVM state if needed

    async def _on_speech_started(self, sender, speech_started, **kwargs):
        """Handles speech started events."""
        logger.debug(f"Speech started event (Session: {self._session_id}): {speech_started}")
        # Optional: Update GVM state e.g., sessions.{id}.speech_active = True

    async def _on_utterance_end(self, sender, utterance_end, **kwargs):
        """Handles utterance end events."""
        logger.debug(f"Utterance end event (Session: {self._session_id}): {utterance_end}")
        # Optional: Update GVM state e.g., sessions.{id}.speech_active = False

    async def _on_error(self, sender, error, **kwargs):
        """Handles error events from the connection."""
        logger.error(f"Deepgram connection error event (Session: {self._session_id}): {error}")
        session_status_key = STATE_STT_SESSION_STATUS_TEMPLATE.format(session_id=self._session_id)
        await self.gvm.set(session_status_key, "error_dg")
        await self.gvm.set(STATE_ERROR_MESSAGE, f"Deepgram Error: {error}")
        # Error event implies connection is likely closed or closing
        await self._disconnect_from_deepgram(graceful_finish=False) # Trigger cleanup

    async def _on_close(self, sender, close, **kwargs):
        """Handles the close event from Deepgram."""
        logger.info(f"Deepgram connection closed event (Session: {self._session_id}): {close}")
        if self._session_id:
            current_status = await self.gvm.get(f"stt.session.{self._session_id}.status", None)
            if current_status not in ["disconnected", "error"]:
                 logger.info(f"Updating session {self._session_id} status to disconnected due to OnClose event.")
                 await self.gvm.set(f"stt.session.{self._session_id}.status", "disconnected")
            else:
                 logger.debug(f"OnClose received for session {self._session_id}, but status already terminal ({current_status}). Ignoring.")
        else:
            logger.warning("OnClose received but no active session ID set in STTManager.")
        self._dg_connection = None

    # < --- End Deepgram Event Handlers --- 

    async def _process_session(self):
        """Handles a single STT session from connect to disconnect."""
        self._session_id = str(uuid.uuid4())
        # Set the global current session ID
        await self.gvm.set("app.current_stt_session_id", self._session_id)
        
        session_status_key = STATE_STT_SESSION_STATUS_TEMPLATE.format(session_id=self._session_id)
        logger.info(f"Starting new STT session: {self._session_id}")
        await self.gvm.set(session_status_key, "starting")
        await self.gvm.set(f"sessions.{self._session_id}.start_time", time.time()) # Use standard time
        # Initialize/clear session-specific states
        await self.gvm.set(STATE_SESSION_INTERIM_TRANSCRIPT_TEMPLATE.format(session_id=self._session_id), "")
        await self.gvm.set(STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE.format(session_id=self._session_id), "")
        await self.gvm.set(STATE_SESSION_RECOGNIZED_ACTIONS_TEMPLATE.format(session_id=self._session_id), [])
        await self.gvm.set(STATE_SESSION_HISTORY_TEMPLATE.format(session_id=self._session_id), [])

        self._stop_processing.clear()
        self._retry_count = 0
        audio_stream_task = None

        try:
            if await self._connect_to_deepgram():
                # Start streaming audio only after successful connection
                audio_stream_task = asyncio.create_task(self._stream_audio(), name=f"STT_AudioStream_{self._session_id}")
                
                # Wait for the trigger key to be released
                await self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, False)
                logger.info(f"Stop trigger detected for session {self._session_id}. Finishing...")
                self._stop_processing.set() # Signal audio streaming loop to stop
                
                # Wait briefly for audio streaming task to potentially finish sending last chunks
                if audio_stream_task:
                     try:
                          await asyncio.wait_for(audio_stream_task, timeout=0.5)
                     except asyncio.TimeoutError:
                          logger.warning("Audio streaming task did not finish promptly after stop signal. Cancelling.")
                          audio_stream_task.cancel()
                     except asyncio.CancelledError:
                          pass # Expected if cancelled
                     except Exception as e:
                          logger.error(f"Error waiting for audio stream task exit: {e}")

            else:
                logger.error(f"Failed to establish Deepgram connection for session {self._session_id}. Session aborted.")
                await self.gvm.set(session_status_key, "error_connect_fail") # Ensure error state is set

        except asyncio.CancelledError:
             logger.info(f"STT session processing cancelled for session {self._session_id}.")
             if audio_stream_task and not audio_stream_task.done(): audio_stream_task.cancel()
             await self.gvm.set(session_status_key, "cancelled")
             raise # Re-raise cancellation
        except Exception as e:
             logger.error(f"Unexpected error during STT session processing {self._session_id}: {e}", exc_info=True)
             if audio_stream_task and not audio_stream_task.done(): audio_stream_task.cancel()
             await self.gvm.set(session_status_key, "error_session_process")
             await self.gvm.set(STATE_ERROR_MESSAGE, f"STT Session Error: {e}")
        finally:
            # Ensure disconnection happens regardless of how the session ended
            await self._disconnect_from_deepgram()
            await self.gvm.set(f"sessions.{self._session_id}.end_time", time.time())
            logger.info(f"STT session {self._session_id} processing finished.")
            # Clear session ID only after all processing and state updates are done
            self._session_id = None 

    async def _get_deepgram_language_code(self, base_lang_code: str) -> str:
        """Converts a base language code (e.g., 'en', 'fr') to a Deepgram-compatible one."""
        if base_lang_code == "en":
            return "en-US"
        elif base_lang_code == "fr":
            return "fr"
        # Add other mappings as needed, e.g., es -> es, de -> de
        else:
            logger.warning(f"No specific Deepgram mapping for base language '{base_lang_code}'. Using as-is.")
            return base_lang_code

    async def _get_language_and_model(self):
        """Updates self._language and self._current_model based on GVM configuration."""
        base_lang_code = await self.gvm.get(f"{CONFIG_GENERAL_PREFIX}.language", "en")
        self._language = await self._get_deepgram_language_code(base_lang_code)
        self._current_model = await self.gvm.get(f"{CONFIG_DEEPGRAM_PREFIX}.model", "nova-2")
        
        logger.info(f"STT language updated to: {self._language}, model: {self._current_model}")
        self._language_update_event.set() # Signal that language/model has been updated

    async def run_loop(self):
        logger.info("STTManager run_loop starting.")
        self._stop_event.clear()

        # Initial language and model setup
        await self._get_language_and_model()

        # Task to listen for language changes from GVM
        async def language_change_listener():
            while not self._stop_event.is_set():
                try:
                    await self.gvm.wait_for_change(f"{CONFIG_GENERAL_PREFIX}.language")
                    logger.info("Detected language change in GVM. Updating STT language/model.")
                    await self._get_language_and_model()
                    # If a session is active, it will use the new lang on next connection.
                    # Optionally, could force-close current session if immediate switch is needed.
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in language_change_listener: {e}", exc_info=True)
                    await asyncio.sleep(5) # Avoid tight loop on error
        
        listener_task = asyncio.create_task(language_change_listener())

        while not self._stop_event.is_set():
            try:
                logger.debug("STTManager waiting for dictation trigger (key press)...")
                await self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, True)
                if self._stop_event.is_set(): break # Check after wait

                logger.info("Dictation trigger detected by STTManager.")

                # Ensure language/model are up-to-date before starting a new session
                self._language_update_event.clear()
                await self._get_language_and_model() # Re-fetch, could be more efficient
                await self._language_update_event.wait() # Ensure _get_language_and_model completes

                if self._active_dg_connection:
                    logger.debug("STT session already active. Waiting for it to complete...")
                    # This logic might need refinement: if a session is truly stuck,
                    # waiting indefinitely might not be ideal.
                    # However, _process_session should eventually complete or error out.
                    # For now, assume _process_session cleans up properly.
                    await asyncio.sleep(0.1) # Brief pause to allow existing session to clear if ending
                    if self._active_dg_connection: # Re-check if it cleared up
                        logger.warning("Previous STT session did not clear quickly. Skipping new trigger.")
                        await self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, False) # Wait for release
                        continue 

                session_id = str(uuid.uuid4())
                await self.gvm.set(STATE_APP_CURRENT_STT_SESSION_ID, session_id)
                self._current_session_id = session_id

                # Launch the session handling in a new task
                # This allows the run_loop to quickly return to waiting for the next trigger.
                asyncio.create_task(self._process_session(), name="STT_SessionProcessor")
                
                # Wait for the dictation key to be released before looping again
                # This prevents re-triggering immediately if key is held down.
                await self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, False)
                logger.debug("STTManager detected key release after session start.")

            except asyncio.CancelledError:
                logger.info("STTManager run_loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in STTManager run_loop: {e}", exc_info=True)
                if self._current_session_id:
                    await self._update_session_status(self._current_session_id, "error", f"Unhandled error: {e}")
                await asyncio.sleep(1) # Avoid tight loop on error
        
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            logger.debug("Language change listener task cancelled successfully.")

        await self.cleanup()
        logger.info("STTManager run_loop finished.")

    async def cleanup(self):
        """Cleans up resources, ensuring any active connection is closed."""
        logger.info("STTManager cleaning up...")
        # The cancellation of run_loop should handle cancelling active session tasks.
        # We just need to ensure the Deepgram client is handled if needed.
        if self.dg_client:
            # The SDK might handle internal client closure, or you might need:
            # await self.dg_client.close() # Check SDK documentation
            pass 
        logger.info("STTManager cleanup finished.") 