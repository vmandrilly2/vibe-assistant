# background_audio_recorder.py
import asyncio
import logging
import sounddevice as sd
import numpy as np
import time
from collections import deque
import threading # Need lock

# Assuming constants like sample rate, channels, chunk size are defined elsewhere
from constants import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS
# Define chunk size based on desired latency vs processing overhead
AUDIO_CHUNK_SIZE = 1024
BUFFER_DURATION_SECONDS = 7 # How much audio to keep buffered

logger = logging.getLogger(__name__)

class BackgroundAudioRecorder:
    """Records audio in the background when triggered and stores chunks in an internal buffer."""

    def __init__(self, gvm, main_loop):
        self.gvm = gvm
        self.main_loop = main_loop
        self.stream = None
        # Buffer stores (timestamp, chunk_bytes) tuples
        self.audio_buffer = deque()
        self._buffer_lock = threading.Lock() # Use threading Lock for buffer access
        self.buffer_duration_seconds = BUFFER_DURATION_SECONDS
        self._recording_active = False
        self._stop_event = asyncio.Event() # Used locally within run_loop
        self.chunk_size = AUDIO_CHUNK_SIZE

    async def init(self):
        """Initializes the recorder (e.g., check device availability)."""
        try:
            sd.check_input_settings(samplerate=AUDIO_SAMPLE_RATE, channels=AUDIO_CHANNELS)
            logger.info("BackgroundAudioRecorder initialized. Default audio input device seems available.")
            return True
        except Exception as e:
            logger.error(f"Audio input device check failed: {e}. Audio recording might not work.", exc_info=True)
            return False

    async def _read_loop(self):
        """Internal loop to continuously read audio data when stream is active."""
        logger.debug("Audio read loop starting...")
        while self._recording_active and self.stream and not self.stream.closed:
            try:
                # Read raw data from the stream
                # The number of frames read might be less than chunk_size if not available
                frames_available = self.stream.read_available
                if frames_available >= self.chunk_size:
                    indata, overflowed = self.stream.read(self.chunk_size)
                    if overflowed:
                        logger.warning("Audio buffer overflow detected during read!")
                    
                    if indata.size > 0:
                        # Get precise timestamp if possible (may depend on sounddevice backend/support)
                        # stream_time = self.stream.time # Might be monotonic
                        # For consistency, use monotonic time when adding to buffer
                        timestamp = time.monotonic() 
                        chunk_bytes = indata.astype(np.int16).tobytes()
                        
                        with self._buffer_lock:
                            self.audio_buffer.append((timestamp, chunk_bytes))
                            # Prune buffer based on time duration
                            while self.audio_buffer:
                                oldest_ts, _ = self.audio_buffer[0]
                                if timestamp - oldest_ts > self.buffer_duration_seconds:
                                    self.audio_buffer.popleft()
                                else:
                                    break # Buffer is within duration
                        # logger.debug(f"Added chunk to buffer. Size: {len(chunk_bytes)}, TS: {timestamp:.2f}, Buffer Len: {len(self.audio_buffer)}")
                    else:
                        # No data read, brief sleep
                        await asyncio.sleep(0.005)
                else:
                    # Not enough frames available, wait a bit
                    await asyncio.sleep(0.01) 

            except sd.PortAudioError as pae:
                 if self._recording_active: # Avoid logging error if stopping
                      logger.error(f"PortAudioError in read loop: {pae}", exc_info=True)
                 self._recording_active = False # Ensure loop termination
                 break
            except Exception as e:
                if self._recording_active:
                    logger.error(f"Unexpected error in audio read loop: {e}", exc_info=True)
                # Consider stopping recording on unexpected errors
                # self._recording_active = False 
                # break
                await asyncio.sleep(0.1) # Prevent tight loop on other errors

        logger.debug("Audio read loop finished.")

    async def _start_recording(self):
        """Starts the sounddevice audio stream and the read loop task."""
        if self.stream is not None and not self.stream.closed:
            logger.warning("Recording stream already active. Ignoring start request.")
            return
        try:
            logger.info("Starting audio recording stream...")
            # Clear buffer from previous runs
            with self._buffer_lock:
                 self.audio_buffer.clear()
            
            self.stream = sd.InputStream(
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype='int16',
                blocksize=self.chunk_size, # Use blocksize for read efficiency
                callback=None # IMPORTANT: No callback
            )
            self.stream.start()
            self._recording_active = True
            # Start the dedicated read loop task
            self._read_loop_task = asyncio.create_task(self._read_loop(), name="AudioReadLoop")
            await self.gvm.set("audio.status", "recording")
            logger.info("Audio recording stream and read loop started.")
        except sd.PortAudioError as pae:
             logger.error(f"PortAudioError starting stream: {pae}", exc_info=True)
             await self.gvm.set("audio.status", "error")
             await self.gvm.set("status.audio.error", f"PortAudioError: {pae}")
             if self.stream: self.stream.close(); self.stream = None
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}", exc_info=True)
            await self.gvm.set("audio.status", "error")
            await self.gvm.set("status.audio.error", str(e))
            if self.stream: self.stream.close(); self.stream = None

    async def _stop_recording(self):
        """Stops the read loop task and the sounddevice audio stream."""
        if not self._recording_active and (self.stream is None or self.stream.closed):
            return
        
        logger.info("Stopping audio recording...")
        self._recording_active = False # Signal read loop to stop

        # Stop and wait for the read loop task first
        if hasattr(self, '_read_loop_task') and self._read_loop_task and not self._read_loop_task.done():
            logger.debug("Waiting for audio read loop task to finish...")
            try:
                await asyncio.wait_for(self._read_loop_task, timeout=0.5) 
            except asyncio.TimeoutError:
                logger.warning("Audio read loop task did not finish promptly. Cancelling.")
                self._read_loop_task.cancel()
            except asyncio.CancelledError:
                pass # Expected if cancelled elsewhere
            except Exception as e:
                 logger.error(f"Error waiting for read loop task: {e}")
            self._read_loop_task = None
            logger.debug("Audio read loop task finished or cancelled.")

        # Now stop the stream
        if self.stream is not None:
            try:
                if not self.stream.closed:
                    self.stream.stop()
                    self.stream.close()
                    logger.info("Audio recording stream stopped and closed.")
            except Exception as e:
                logger.error(f"Error stopping/closing audio stream: {e}", exc_info=True)
            finally:
                self.stream = None
        
        await self.gvm.set("audio.status", "idle")
        
    async def get_buffered_chunks(self, duration_seconds: float) -> list[bytes]:
        """Retrieves chunks from the buffer covering the last N seconds (monotonic time)."""
        if duration_seconds <= 0:
            return []
            
        cutoff_time = time.monotonic() - duration_seconds
        relevant_chunks = []
        with self._buffer_lock:
            # Iterate chronologically from the oldest
            for timestamp, chunk_bytes in self.audio_buffer:
                if timestamp >= cutoff_time:
                    relevant_chunks.append(chunk_bytes)
                    
        # logger.debug(f"Retrieved {len(relevant_chunks)} chunks for the last {duration_seconds:.2f}s (cutoff: {cutoff_time:.2f}, ref: {time.monotonic():.2f})")
        return relevant_chunks

    def get_live_chunks_since(self, timestamp_mono: float) -> list[tuple[float, bytes]]:
        """Retrieves chunks from the buffer recorded since the given monotonic timestamp.

        Args:
            timestamp_mono: The monotonic time after which to retrieve chunks.

        Returns:
            A list of (timestamp, chunk_bytes) tuples recorded after the given time.
        """
        new_chunks = []
        with self._buffer_lock:
            # Iterate chronologically through the deque
            for ts, chunk_bytes in self.audio_buffer:
                if ts > timestamp_mono:
                    new_chunks.append((ts, chunk_bytes))
        # logger.debug(f"get_live_chunks_since({timestamp_mono:.2f}): Found {len(new_chunks)} new chunks.")
        return new_chunks

    async def run_loop(self):
        """Main loop watching GVM state to start/stop recording."""
        logger.info("BackgroundAudioRecorder run_loop starting.")
        is_currently_recording = False
        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                await self.gvm.wait_for_change("input.dictation_key_pressed")
                should_be_recording = await self.gvm.get("input.dictation_key_pressed", False)

                if should_be_recording and not is_currently_recording:
                    await self._start_recording()
                    # Check if stream actually started successfully
                    is_currently_recording = self.stream is not None and not self.stream.closed
                elif not should_be_recording and is_currently_recording:
                    await self._stop_recording()
                    is_currently_recording = False

            except asyncio.CancelledError:
                logger.info("BackgroundAudioRecorder run_loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in BackgroundAudioRecorder run_loop: {e}", exc_info=True)
                await asyncio.sleep(1)

        if is_currently_recording:
            await self._stop_recording()
        logger.info("BackgroundAudioRecorder run_loop finished.")

    async def cleanup(self):
        """Stops the recording and cleans up resources."""
        logger.info("BackgroundAudioRecorder cleaning up...")
        self._stop_event.set() # Signal run_loop to exit
        await self._stop_recording() # Ensure stream and read loop are stopped
        with self._buffer_lock:
             self.audio_buffer.clear()
        logger.info("BackgroundAudioRecorder cleanup finished.")

# Note: This recorder maintains its own buffer. The GVM state `audio.current_chunks`
# mentioned in the design might need clarification. Does STT read from the buffer
# via a method call (like get_buffered_chunks - which needs careful async handling)
# or should this module push the relevant chunks/buffer to GVM state?
# This implementation assumes STT can call get_buffered_chunks. 