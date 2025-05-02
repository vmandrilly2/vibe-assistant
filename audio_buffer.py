import pyaudio
import threading
import collections
import queue
import logging
import time
import numpy as np

# --- PyAudio Constants --- (Moved from vibe_app.py)
MONITOR_CHUNK_SIZE = 1024
MONITOR_FORMAT = pyaudio.paInt16
MONITOR_CHANNELS = 1
MONITOR_RATE = 16000
MAX_RMS = 5000 # Adjust based on microphone sensitivity
# --- End Constants ---

class BufferedAudioInput:
    """Manages continuous background audio recording, buffering, and RMS calculation."""

    def __init__(self, status_q, buffer_seconds=7.0, device_index=None):
        """
        Args:
            status_q: Queue to send ('volume', rms_value) tuples to.
            buffer_seconds: Duration of audio to keep in the buffer.
            device_index: Specific input device index for PyAudio (optional).
        """
        self.status_queue = status_q
        self.device_index = device_index
        self.buffer_seconds = 7.0 # Store up to 7 seconds

        self.p = None
        self.stream = None
        self.running = threading.Event()
        self.thread = None

        # Buffer setup
        self.buffer_max_chunks = int((RATE / CHUNK_SIZE) * self.buffer_seconds)
        self._audio_buffer = collections.deque(maxlen=self.buffer_max_chunks)
        self._buffer_lock = threading.Lock()

        logging.info(f"BufferedAudioInput: Buffer initialized for ~{self.buffer_seconds}s ({self.buffer_max_chunks} chunks).")

    def _calculate_rms(self, data):
        """Calculate Root Mean Square (RMS) volume of audio data."""
        if not data: return 0
        try:
            audio_data = np.frombuffer(data, dtype=np.int16)
            if audio_data.size == 0: return 0
            rms = np.sqrt(np.mean(audio_data.astype(float)**2))
            normalized_rms = min(rms / MAX_RMS, 1.0)
            return normalized_rms
        except Exception as e:
            logging.error(f"[BufferedAudioInput] Error calculating RMS: {e}")
            return 0

    def _capture_loop(self):
        """Continuously reads audio, stores in buffer, and sends RMS to queue."""
        logging.info("[BufferedAudioInput] Capture loop started.")
        stream_opened = False
        try:
            self.p = pyaudio.PyAudio()
            self.stream = self.p.open(format=FORMAT,
                                      channels=CHANNELS,
                                      rate=RATE,
                                      input=True,
                                      frames_per_buffer=CHUNK_SIZE,
                                      input_device_index=self.device_index)
            stream_opened = True
            logging.info(f"[BufferedAudioInput] PyAudio stream opened (Device: {self.device_index or 'Default'}).")
        except Exception as e:
            logging.error(f"[BufferedAudioInput] Failed to open PyAudio stream: {e}", exc_info=True)
            self.running.clear()

        while self.running.is_set() and stream_opened:
            try:
                data = self.stream.read(CHUNK_SIZE, exception_on_overflow=False)

                # --- MODIFIED: Store timestamp with data --- >
                current_time = time.monotonic()
                with self._buffer_lock:
                    self._audio_buffer.append((current_time, data))
                # --- END MODIFIED --- >

                # 2. Calculate volume and send to status queue
                volume = self._calculate_rms(data)
                try:
                    # Use put_nowait to avoid blocking if the UI thread is slow
                    self.status_queue.put_nowait(("volume", volume))
                except queue.Full:
                    # Log less frequently if queue is full to avoid spam
                    # logging.warning("[BufferedAudioInput] Status queue full. Discarding volume update.")
                    pass

            except IOError as e:
                # Handle stream closure or errors gracefully
                if self.running.is_set():
                    logging.error(f"[BufferedAudioInput] PyAudio read error: {e}")
                break # Exit loop on IOError
            except Exception as e:
                 if self.running.is_set():
                     logging.error(f"[BufferedAudioInput] Unexpected error in capture loop: {e}", exc_info=True)
                 # Optionally break here too depending on desired robustness
                 # break

        # --- Cleanup --- #
        logging.info("[BufferedAudioInput] Capture loop ending. Cleaning up...")
        if self.stream:
            try:
                if self.stream.is_active():
                    self.stream.stop_stream()
                self.stream.close()
                logging.info("[BufferedAudioInput] PyAudio stream stopped and closed.")
            except Exception as e:
                logging.error(f"[BufferedAudioInput] Error closing PyAudio stream: {e}")
        self.stream = None

        if self.p:
            try:
                self.p.terminate()
                logging.info("[BufferedAudioInput] PyAudio instance terminated.")
            except Exception as e:
                logging.error(f"[BufferedAudioInput] Error terminating PyAudio instance: {e}")
        self.p = None
        logging.info("[BufferedAudioInput] Capture loop finished.")

    def get_buffer(self) -> list:
        """Returns a copy of the current audio buffer contents as a list. Thread-safe."""
        with self._buffer_lock:
            # Return a copy to avoid modification after retrieval
            buffer_list = list(self._audio_buffer)
            logging.debug(f"[BufferedAudioInput] Returning buffer with {len(buffer_list)} chunks.")
            return buffer_list

    # --- NEW: Method to get audio from the last N seconds --- >
    def get_buffer_last_n_seconds(self, duration_sec: float, reference_time: float) -> list:
        """Returns audio data recorded within the last 'duration_sec' before 'reference_time'.

        Args:
            duration_sec: The duration of audio to retrieve (e.g., connection time, capped).
            reference_time: The timestamp (time.monotonic()) when the period ends (e.g., connection established).

        Returns:
            A list of audio data chunks.
        """
        if duration_sec <= 0 or reference_time <= 0:
            return []

        cutoff_time = reference_time - duration_sec
        relevant_chunks = []

        with self._buffer_lock:
            # Iterate through the deque (ordered oldest to newest)
            for timestamp, data in self._audio_buffer:
                if timestamp >= cutoff_time:
                    relevant_chunks.append(data)

        logging.debug(f"[BufferedAudioInput] Retrieved {len(relevant_chunks)} chunks for the last {duration_sec:.2f}s (cutoff: {cutoff_time:.2f}, ref: {reference_time:.2f})")
        return relevant_chunks
    # --- END NEW ---

    def start(self):
        """Starts the audio capture thread if not already running."""
        if not self.running.is_set():
            self.running.set()
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            logging.info("[BufferedAudioInput] Started capture thread.")
        else:
            logging.warning("[BufferedAudioInput] Start called but already running.")

    def stop(self):
        """Stops the audio capture thread."""
        if self.running.is_set():
            logging.info("[BufferedAudioInput] Stopping capture thread...")
            self.running.clear() # Signal the loop to stop

            if self.thread and self.thread.is_alive():
                # Wait briefly for the thread to exit
                self.thread.join(timeout=1.0)
                if self.thread.is_alive():
                    logging.warning("[BufferedAudioInput] Capture thread did not stop cleanly after 1s.")
                    # Consider more forceful shutdown if necessary, but PyAudio cleanup
                    # within the loop should handle stream closing.

            self.thread = None # Clear thread reference
            logging.info("[BufferedAudioInput] Stopped.")
        else:
            logging.debug("[BufferedAudioInput] Stop called but not running.")

# --- Example Usage (if run directly) ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
    test_status_queue = queue.Queue()

    print("Initializing BufferedAudioInput...")
    buffered_input = BufferedAudioInput(test_status_queue)
    buffered_input.start()

    print("Running for 10 seconds...")
    start_time = time.time()
    while time.time() - start_time < 10:
        try:
            level_type, level_value = test_status_queue.get_nowait()
            if level_type == 'volume':
                 print(f"Volume: {level_value:.2f}", end='\r')
        except queue.Empty:
            pass
        time.sleep(0.05)

    print("\nGetting buffer...")
    buffer_data = buffered_input.get_buffer()
    print(f"Retrieved {len(buffer_data)} chunks from buffer.")

    print("Stopping...")
    buffered_input.stop()
    print("Finished.") 