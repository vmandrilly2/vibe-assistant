import asyncio
import logging
import time
import uuid
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
    AUDIO_SAMPLE_RATE, AUDIO_CHANNELS # Import constants
)

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
            # Get language and model details from GVM state
            self._language = await self.gvm.get("config.general.source_language", "en-US")
            self._current_model = await self.gvm.get("config.deepgram.model", "nova-2")
            options = LiveOptions(
                model=self._current_model,
                language=self._language,
                encoding="linear16",
                channels=AUDIO_CHANNELS, # Use constant
                sample_rate=AUDIO_SAMPLE_RATE, # Use constant
                punctuate=await self.gvm.get("config.deepgram.punctuate", True),
                interim_results=True,
                utterance_end_ms=str(await self.gvm.get("config.deepgram.utterance_end_ms", 1000)),
                vad_events=await self.gvm.get("config.deepgram.vad_events", True),
                smart_format=await self.gvm.get("config.deepgram.smart_format", True),
                numerals=await self.gvm.get("config.deepgram.numerals", True),
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
                new_chunks_with_ts = self.audio_recorder.get_live_chunks_since(last_polled_timestamp)
                
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


    async def run_loop(self):
        """Main loop watching GVM state to start STT sessions."""
        logger.info("STTManager run_loop starting.")
        active_session_task: Optional[asyncio.Task] = None

        while True:
            try:
                # Wait for the trigger key to be pressed
                # Only proceed if no session is currently active
                if active_session_task and not active_session_task.done():
                     logger.debug("STT session already active. Waiting for it to complete...")
                     # Wait for the current session to finish before checking trigger again
                     await asyncio.wait([active_session_task]) 
                     active_session_task = None # Reset task reference
                     logger.debug("Previous STT session completed.")
                     # Short sleep after session completes before checking trigger immediately
                     await asyncio.sleep(0.1) 

                logger.debug("STTManager waiting for dictation trigger (key press)...")
                await self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, True)
                logger.info("Dictation trigger detected by STTManager.")
                
                # Start processing a session
                active_session_task = asyncio.create_task(self._process_session(), name="STT_SessionProcessor")
                
                # Loop continues, will wait for task completion at the top if key pressed again quickly

            except asyncio.CancelledError:
                logger.info("STTManager run_loop cancelled.")
                if active_session_task and not active_session_task.done():
                    logger.info("Cancelling active STT session task due to manager shutdown...")
                    active_session_task.cancel()
                    try:
                        await active_session_task # Wait for cancellation to complete
                    except asyncio.CancelledError:
                        pass # Expected
                    except Exception as e:
                        logger.error(f"Error waiting for cancelled session task during cleanup: {e}")
                break
            except Exception as e:
                logger.error(f"Error in STTManager run_loop: {e}", exc_info=True)
                # Avoid tight loop on error
                await asyncio.sleep(2)

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