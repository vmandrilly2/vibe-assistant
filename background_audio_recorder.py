# background_audio_recorder.py
import asyncio
import logging
import sounddevice as sd
import numpy as np
import time
from collections import deque

# Assuming constants like sample rate, channels, chunk size are defined elsewhere
# from constants import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, AUDIO_CHUNK_SIZE
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SIZE = 2048 # Should match Deepgram expectations if possible
BUFFER_DURATION_SECONDS = 7 # How much audio to keep buffered

logger = logging.getLogger(__name__)

class BackgroundAudioRecorder:
    """Records audio in the background when triggered and stores chunks in GVM state."""

    def __init__(self, gvm):
        self.gvm = gvm
        self.stream = None
        self.audio_buffer = deque() # Store (timestamp, chunk_bytes)
        self.buffer_max_size = int(BUFFER_DURATION_SECONDS * AUDIO_SAMPLE_RATE * 2) # Approx bytes for 16-bit mono
        self._recording_active = False
        self._recording_task = None
        self._stop_event = asyncio.Event() # Used locally within run_loop

    async def init(self):
        """Initializes the recorder (e.g., check device availability)."""
        try:
            # Check if default input device is available
            sd.check_input_settings(samplerate=AUDIO_SAMPLE_RATE, channels=AUDIO_CHANNELS)
            logger.info("BackgroundAudioRecorder initialized. Default audio input device seems available.")
            return True
        except Exception as e:
            logger.error(f"Audio input device check failed: {e}. Audio recording might not work.", exc_info=True)
            # Optionally update GVM state about the error
            # await self.gvm.set("status.audio.error", str(e))
            return False

    def _audio_callback(self, indata, frames, time_info, status):
        """This callback is executed by the sounddevice stream in a separate thread."""
        if status:
            logger.warning(f"Audio callback status: {status}")
        # Convert numpy array to bytes (assuming 16-bit PCM)
        chunk_bytes = indata.astype(np.int16).tobytes()
        timestamp = time.time() # Record timestamp for buffer management

        # Use run_coroutine_threadsafe to interact with deque and GVM state from main loop
        asyncio.run_coroutine_threadsafe(
            self._handle_audio_chunk_async(timestamp, chunk_bytes),
            self.gvm.get_main_loop() # Assuming GVM provides loop access
        )

    async def _handle_audio_chunk_async(self, timestamp: float, chunk_bytes: bytes):
        """Handles appending audio chunks to the buffer and GVM state (async)."""
        # Append to local buffer
        self.audio_buffer.append((timestamp, chunk_bytes))

        # Maintain buffer size (approximate)
        current_size = sum(len(c) for _, c in self.audio_buffer)
        while current_size > self.buffer_max_size and self.audio_buffer:
            ts, removed_chunk = self.audio_buffer.popleft()
            current_size -= len(removed_chunk)
            # logger.debug(f"Removed old chunk from buffer (ts: {ts})")

        # Update GVM state (e.g., maybe only if recording is active?)
        # Option 1: Always update GVM with the latest buffer
        # current_buffer_list = list(self.audio_buffer) # Create snapshot
        # await self.gvm.set("audio.current_buffer", current_buffer_list)

        # Option 2: Update GVM state only when recording is active (simpler)
        if self._recording_active:
             # We might need a different GVM state key for live chunks vs the buffer
             # E.g., "audio.live_chunk" or add to a list/queue "audio.stream_chunks"
             # For simplicity here, let's assume STTManager reads from the buffer when needed.
             # If direct streaming is required, GVM state needs a queue/list for chunks.
             await self.gvm.set("audio.latest_chunk_timestamp", timestamp) # Signal new chunk
             # Consider if STTManager needs the raw chunk via GVM state or just the buffer.

    async def _start_recording(self):
        """Starts the sounddevice audio stream."""
        if self.stream is not None:
            logger.warning("Recording stream already active. Ignoring start request.")
            return
        try:
            logger.info("Starting audio recording stream...")
            # Buffer management happens in the callback
            self.stream = sd.InputStream(
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype='int16', # 16-bit PCM
                blocksize=AUDIO_CHUNK_SIZE,
                callback=self._audio_callback
            )
            self.stream.start()
            self._recording_active = True
            await self.gvm.set("audio.status", "recording")
            logger.info("Audio recording stream started.")
        except sd.PortAudioError as pae:
             logger.error(f"PortAudioError starting stream: {pae}", exc_info=True)
             await self.gvm.set("audio.status", "error")
             await self.gvm.set("status.audio.error", f"PortAudioError: {pae}")
             self.stream = None
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}", exc_info=True)
            await self.gvm.set("audio.status", "error")
            await self.gvm.set("status.audio.error", str(e))
            self.stream = None

    async def _stop_recording(self):
        """Stops the sounddevice audio stream."""
        if self.stream is None:
            # logger.debug("Recording stream already stopped.")
            return
        try:
            logger.info("Stopping audio recording stream...")
            self.stream.stop()
            self.stream.close()
            self.stream = None
            self._recording_active = False
            await self.gvm.set("audio.status", "idle")
            # Clear live chunks state if used
            # await self.gvm.set("audio.stream_chunks", [])
            logger.info("Audio recording stream stopped and closed.")
        except Exception as e:
            logger.error(f"Error stopping audio stream: {e}", exc_info=True)
            # Attempt to forcefully set state to idle/error
            self.stream = None
            self._recording_active = False
            await self.gvm.set("audio.status", "error_stopping")

    async def get_buffered_chunks(self, duration_seconds: float) -> list[bytes]:
        """Retrieves chunks from the buffer covering the last N seconds."""
        cutoff_time = time.time() - duration_seconds
        # Access deque safely (copy relevant part)
        # Since callback modifies deque, this should ideally be called from main loop
        # or needs locking if accessed directly from another async task.
        # Assuming called from main loop context (e.g., STTManager task)
        buffered_data = list(self.audio_buffer) # Snapshot
        relevant_chunks = [chunk for ts, chunk in buffered_data if ts >= cutoff_time]
        logger.debug(f"Retrieved {len(relevant_chunks)} chunks for the last {duration_seconds:.2f}s (cutoff: {cutoff_time:.2f}, ref: {time.time():.2f})")
        return relevant_chunks

    async def run_loop(self):
        """Main loop watching GVM state to start/stop recording."""
        logger.info("BackgroundAudioRecorder run_loop starting.")
        is_currently_recording = False
        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                # Wait for the trigger key state to change
                await self.gvm.wait_for_change("input.dictation_key_pressed")
                
                # Check the new state
                should_be_recording = await self.gvm.get("input.dictation_key_pressed", False)

                if should_be_recording and not is_currently_recording:
                    await self._start_recording()
                    is_currently_recording = True
                elif not should_be_recording and is_currently_recording:
                    await self._stop_recording()
                    is_currently_recording = False

            except asyncio.CancelledError:
                logger.info("BackgroundAudioRecorder run_loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in BackgroundAudioRecorder run_loop: {e}", exc_info=True)
                # Avoid tight loop on error
                await asyncio.sleep(1)

        # Ensure recording is stopped on exit
        if is_currently_recording:
            await self._stop_recording()
        logger.info("BackgroundAudioRecorder run_loop finished.")

    async def cleanup(self):
        """Stops the recording and cleans up resources."""
        logger.info("BackgroundAudioRecorder cleaning up...")
        self._stop_event.set() # Signal run_loop to exit
        await self._stop_recording() # Ensure stream is stopped
        self.audio_buffer.clear()
        logger.info("BackgroundAudioRecorder cleanup finished.")

# Note: This recorder maintains its own buffer. The GVM state `audio.current_chunks`
# mentioned in the design might need clarification. Does STT read from the buffer
# via a method call (like get_buffered_chunks - which needs careful async handling)
# or should this module push the relevant chunks/buffer to GVM state?
# This implementation assumes STT can call get_buffered_chunks. 