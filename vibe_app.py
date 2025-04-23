# vibe_app.py

import asyncio
import os
import threading
import logging
import time
from dotenv import load_dotenv
import queue # Import queue for thread-safe communication
import tkinter as tk # Import tkinter for the tooltip GUI
import pyautogui # Import pyautogui to get mouse position
import json # Import json module
import sys # Import sys for exiting on critical config error
import numpy as np # Import numpy
import pyaudio     # Import PyAudio
from openai import OpenAI, AsyncOpenAI # Use AsyncOpenAI for non-blocking calls

# --- Systray UI Import ---
# We need threading anyway, so let's use it for the systray
# Import the run function and the reload event
import systray_ui

# --- Audio Buffer Import ---
from audio_buffer import BufferedAudioInput

from pynput import mouse, keyboard
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone, # Import Microphone class
)

# --- Configuration Loading ---
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
  "general": {
    "min_duration_sec": 0.5,
    "selected_language": "en-US",
    "target_language": None,
    "openai_model": "gpt-4.1-nano"
  },
  "triggers": {
    "dictation_button": "middle",
    "command_button": None,
    "command_modifier": None
  },
  "tooltip": {
    "alpha": 0.85,
    "bg_color": "lightyellow",
    "fg_color": "black",
    "font_family": "Arial",
    "font_size": 10
  }
}

def load_config():
    """Loads configuration from JSON file, creates default if not found."""
    if not os.path.exists(CONFIG_FILE):
        logging.warning(f"{CONFIG_FILE} not found. Creating default config.")
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            return DEFAULT_CONFIG
        except IOError as e:
            logging.error(f"Unable to create default config file {CONFIG_FILE}: {e}")
            # Return default anyway, but log the error
            return DEFAULT_CONFIG
    else:
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded_config = json.load(f)
                # Basic validation/merging with defaults for missing keys could be added here
                # For now, assume the structure is correct if the file loads
                logging.info(f"Loaded configuration from {CONFIG_FILE}")
                return loaded_config
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding {CONFIG_FILE}: {e}. Using default config.")
            return DEFAULT_CONFIG
        except IOError as e:
            logging.error(f"Unable to read config file {CONFIG_FILE}: {e}. Using default config.")
            return DEFAULT_CONFIG
        except Exception as e:
             logging.error(f"Unexpected error loading config: {e}. Using default config.")
             return DEFAULT_CONFIG

config = load_config()

# --- PyAudio Constants --- (Define globally for AudioMonitor)
MONITOR_CHUNK_SIZE = 1024
MONITOR_FORMAT = pyaudio.paInt16
MONITOR_CHANNELS = 1
MONITOR_RATE = 16000
MAX_RMS = 5000 # Adjust based on microphone sensitivity

# --- Pynput Mappings --- (Define globally)
PYNPUT_BUTTON_MAP = {
    "left": mouse.Button.left,
    "right": mouse.Button.right,
    "middle": mouse.Button.middle,
    "x1": mouse.Button.x1,
    "x2": mouse.Button.x2,
    None: None
}
PYNPUT_MODIFIER_MAP = {
    "shift": keyboard.Key.shift,
    "shift_l": keyboard.Key.shift_l,
    "shift_r": keyboard.Key.shift_r,
    "ctrl": keyboard.Key.ctrl,
    "ctrl_l": keyboard.Key.ctrl_l,
    "ctrl_r": keyboard.Key.ctrl_r,
    "alt": keyboard.Key.alt,
    "alt_l": keyboard.Key.alt_l,
    "alt_r": keyboard.Key.alt_r,
    "cmd": keyboard.Key.cmd, # For Mac compatibility if needed later
    None: None
}

# --- Global Configurable Variables ---
# These will be updated by apply_config()
DICTATION_TRIGGER_BUTTON = None
COMMAND_TRIGGER_BUTTON = None
COMMAND_MODIFIER_KEY_STR = None
COMMAND_MODIFIER_KEY = None
MIN_DURATION_SEC = 0.5
SELECTED_LANGUAGE = "en-US"
TARGET_LANGUAGE = None
OPENAI_MODEL = "gpt-4.1-nano"
TOOLTIP_ALPHA = 0.85
TOOLTIP_BG = "lightyellow"
TOOLTIP_FG = "black"
TOOLTIP_FONT_FAMILY = "Arial"
TOOLTIP_FONT_SIZE = 10

def apply_config(cfg):
    """Applies the loaded configuration to the global variables."""
    global DICTATION_TRIGGER_BUTTON, COMMAND_TRIGGER_BUTTON, COMMAND_MODIFIER_KEY_STR
    global COMMAND_MODIFIER_KEY, MIN_DURATION_SEC, SELECTED_LANGUAGE
    global TOOLTIP_ALPHA, TOOLTIP_BG, TOOLTIP_FG, TOOLTIP_FONT_FAMILY, TOOLTIP_FONT_SIZE
    global TARGET_LANGUAGE, OPENAI_MODEL

    logging.info("Applying configuration...")
    try:
        # Triggers
        triggers_cfg = cfg.get("triggers", {})
        DICTATION_TRIGGER_BUTTON = PYNPUT_BUTTON_MAP.get(triggers_cfg.get("dictation_button", "middle"))
        COMMAND_TRIGGER_BUTTON = PYNPUT_BUTTON_MAP.get(triggers_cfg.get("command_button"))
        COMMAND_MODIFIER_KEY_STR = triggers_cfg.get("command_modifier")
        COMMAND_MODIFIER_KEY = PYNPUT_MODIFIER_MAP.get(COMMAND_MODIFIER_KEY_STR)

        # General
        general_cfg = cfg.get("general", {})
        MIN_DURATION_SEC = float(general_cfg.get("min_duration_sec", 0.5))
        SELECTED_LANGUAGE = str(general_cfg.get("selected_language", "en-US"))
        TARGET_LANGUAGE = general_cfg.get("target_language")
        OPENAI_MODEL = str(general_cfg.get("openai_model", "gpt-4.1-nano"))

        # Tooltip
        TOOLTIP_ALPHA = float(cfg.get("tooltip", {}).get("alpha", 0.85))
        TOOLTIP_BG = str(cfg.get("tooltip", {}).get("bg_color", "lightyellow"))
        TOOLTIP_FG = str(cfg.get("tooltip", {}).get("fg_color", "black"))
        TOOLTIP_FONT_FAMILY = str(cfg.get("tooltip", {}).get("font_family", "Arial"))
        TOOLTIP_FONT_SIZE = int(cfg.get("tooltip", {}).get("font_size", 10))

        target_lang_str = TARGET_LANGUAGE if TARGET_LANGUAGE else "None"
        logging.info(f"Config applied: SourceLang={SELECTED_LANGUAGE}, TargetLang={target_lang_str}, Model={OPENAI_MODEL}, Dictation={triggers_cfg.get('dictation_button', 'middle')}, ...")

    except (ValueError, TypeError, KeyError) as e:
        logging.error(f"Error applying configuration: {e}. Some settings may not be updated correctly.")
        # Keep existing values or fall back to defaults? For now, just log.

# --- Initial Configuration Application --- (After defining apply_config)
load_dotenv() # Load environment variables from .env file (still used for API key)
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # --- Load OpenAI Key ---
if not DEEPGRAM_API_KEY:
    logging.critical("DEEPGRAM_API_KEY not found in environment variables or .env file. Exiting.")
    sys.exit(1)
# --- Check for OpenAI Key (warn if missing, needed for translation) ---
if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY not found in environment variables or .env file. Translation feature will be disabled.")
    # Don't exit, allow other modes to work

# Apply the initially loaded config
apply_config(config)

# --- Initialize OpenAI Client ---
# Initialize AsyncOpenAI client if key exists
openai_client = None
if OPENAI_API_KEY:
    try:
        # Use the correct model name from config if needed during init, though usually not required here
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        logging.info("OpenAI client initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize OpenAI client: {e}")
else:
    logging.warning("OpenAI client not initialized due to missing API key. Translation disabled.")

logging.info(f"Using Source Language: {SELECTED_LANGUAGE}")
# --- Log Target Language ---
if TARGET_LANGUAGE:
    logging.info(f"Translation Enabled: Target Language = {TARGET_LANGUAGE}, Model = {OPENAI_MODEL}")
else:
    logging.info("Translation Disabled (Target Language is None)")
logging.info(f"Dictation Trigger: {config.get('triggers', {}).get('dictation_button', 'middle')}")
if COMMAND_TRIGGER_BUTTON:
    mod_str = f" + {COMMAND_MODIFIER_KEY_STR}" if COMMAND_MODIFIER_KEY else ""
    logging.info(f"Command Trigger: {config.get('triggers', {}).get('command_button')}{mod_str}")
else:
    logging.info("Command Trigger: Disabled")

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Global State ---
is_dictation_active = threading.Event()
is_command_active = threading.Event()
transcription_active_event = threading.Event() # True if either dictation or command is active
current_mode = None # 'dictation' or 'command'
tooltip_queue = queue.Queue() # Queue for communicating with the tooltip thread
status_queue = queue.Queue() # NEW: For the microphone status icon
modifier_keys_pressed = set() # Keep track of currently pressed modifier keys

# --- Keyboard Controller (for typing simulation) ---
kb_controller = keyboard.Controller()

# --- State for Dictation Typing Simulation ---
last_simulated_text = "" # Store the transcript corresponding to the last simulation action
typed_word_history = [] # Store history of typed words
final_source_text = "" # Store final source text from dictation *before* potential translation

# --- State for Command Mode ---
current_command_transcript = "" # Store the transcript for command mode
last_command_executed = None # For potential undo feature

# --- Tooltip Manager Class ---
class TooltipManager:
    """Manages a simple Tkinter tooltip window in a separate thread."""
    def __init__(self, q):
        self.queue = q
        self.root = None
        self.label = None
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event() # Signal when Tkinter root is ready
        # --- Add config references ---
        self._apply_tooltip_config()

    def _apply_tooltip_config(self):
        """Applies tooltip config to internal variables."""
        # Use the global config variables directly
        self.alpha = TOOLTIP_ALPHA
        self.bg_color = TOOLTIP_BG
        self.fg_color = TOOLTIP_FG
        self.font_family = TOOLTIP_FONT_FAMILY
        self.font_size = TOOLTIP_FONT_SIZE
        logging.debug(f"Tooltip config applied: Alpha={self.alpha}, BG={self.bg_color}, FG={self.fg_color}")

    def reload_config(self):
        """Called when main config reloads to update tooltip appearance."""
        self._apply_tooltip_config()
        # If the window exists, update its attributes
        if self.root and self.label:
            try:
                self.root.attributes('-alpha', self.alpha)
                self.label.config(bg=self.bg_color, fg=self.fg_color,
                                  font=(self.font_family, self.font_size))
                logging.info("Tooltip appearance updated from reloaded config.")
            except tk.TclError as e:
                logging.warning(f"Could not update tooltip appearance on reload: {e}")

    def start(self):
        self.thread.start()
        # Wait briefly for Tkinter to initialize to prevent race conditions on early commands
        self._tk_ready.wait(timeout=2.0)
        if not self._tk_ready.is_set():
            logging.warning("Tooltip Tkinter thread did not become ready in time.")

    def stop(self):
        """Signals the Tkinter thread to stop and cleanup."""
        logging.debug("Stop requested for TooltipManager.")
        self._stop_event.set()
        # Put a stop command on the queue to ensure the _check_queue loop wakes up
        # Use put_nowait as the thread might be shutting down anyway
        try:
            self.queue.put_nowait(("stop", None))
        except queue.Full:
            logging.warning("Tooltip queue full when sending stop command.")
        # Do NOT join the thread here - let the daemon thread exit naturally
        # or let the Tkinter thread handle its own cleanup.

    def _run_tkinter(self):
        logging.info("Tooltip thread started.")
        try:
            self.root = tk.Tk()
            self.root.withdraw() # Start hidden
            self.root.overrideredirect(True) # No border, title bar, etc.
            self.root.wm_attributes("-topmost", True) # Keep on top
            # Apply config settings during creation
            self.root.attributes('-alpha', self.alpha)
            self.label = tk.Label(self.root, text="", bg=self.bg_color, fg=self.fg_color,
                                  font=(self.font_family, self.font_size),
                                  justify=tk.LEFT, padx=5, pady=2)
            self.label.pack()

            self._tk_ready.set() # Signal that Tkinter objects are created
            logging.debug("Tooltip Tkinter objects created and ready.")

            # Start the queue checking loop using root.after
            self._check_queue()

            # Run the Tkinter main event loop.
            # This will block until the window is destroyed or tk.quit() is called.
            logging.debug("Starting Tkinter mainloop...")
            self.root.mainloop()
            logging.debug("Tkinter mainloop finished.")

        except Exception as e:
            logging.error(f"Error during Tkinter mainloop/setup in tooltip thread: {e}", exc_info=True)
            self._tk_ready.set() # Set ready even on error to prevent blocking start()
        finally:
            # Cleanup happens automatically when mainloop exits after root is destroyed
            logging.info("Tooltip thread finished.")
            # Ensure stop event is set if mainloop exited unexpectedly
            self._stop_event.set()

    def _check_queue(self):
        """Processes messages from the queue using root.after."""
        # Check stop event first
        if self._stop_event.is_set():
            logging.debug("Stop event set, initiating Tkinter cleanup.")
            self._cleanup_tk()
            return # Stop rescheduling

        try:
            # Process all available messages in the queue
            while not self.queue.empty():
                command, data = self.queue.get_nowait()
                if command == "update":
                    text, x, y = data
                    self._update_tooltip(text, x, y)
                elif command == "show":
                    self._show_tooltip()
                elif command == "hide":
                    self._hide_tooltip()
                elif command == "stop":
                    # This command ensures we wake up and check the _stop_event
                    logging.debug("Received stop command in queue.")
                    self._stop_event.set() # Ensure it's set
                    # We don't break the loop here, let the check at the start handle it
                    # This ensures cleanup happens before returning

        except queue.Empty:
            pass # No messages, just reschedule
        except tk.TclError as e:
            logging.warning(f"Tkinter error during queue processing: {e}. Stopping tooltip.")
            self._stop_event.set()
            self._cleanup_tk()
            return # Stop rescheduling
        except Exception as e:
            logging.error(f"Error processing tooltip queue: {e}", exc_info=True)
            # Consider stopping if there's a persistent error
            # self._stop_event.set()
            # self._cleanup_tk()
            # return

        # Reschedule the check if not stopping
        if not self._stop_event.is_set() and self.root:
             try:
                 self.root.after(50, self._check_queue)
             except tk.TclError:
                 logging.warning("Tooltip root destroyed before rescheduling queue check.")
                 self._stop_event.set()
                 self._cleanup_tk() # Attempt cleanup just in case

    def _cleanup_tk(self):
        """Safely destroys the Tkinter window from the Tkinter thread."""
        logging.debug("Executing _cleanup_tk.")
        if self.root:
            try:
                logging.debug("Destroying tooltip root window...")
                self.root.destroy()
                logging.info("Tkinter root window destroyed successfully.")
                self.root = None # Prevent further access
            except tk.TclError as e:
                logging.warning(f"Error destroying Tkinter root (already destroyed?): {e}")
            except Exception as e:
                logging.error(f"Unexpected error during Tkinter destroy: {e}", exc_info=True)

    def _update_tooltip(self, text, x, y):
        # Check if root exists and stop event isn't set
        if self.root and self.label and not self._stop_event.is_set():
            try:
                self.label.config(text=text)
                offset_x = 15
                offset_y = -30
                self.root.geometry(f"+{x + offset_x}+{y + offset_y}")
            except tk.TclError as e:
                 logging.warning(f"Failed to update tooltip (window likely closed): {e}")
                 self._stop_event.set() # Stop if window is broken

    def _show_tooltip(self):
        if self.root and not self._stop_event.is_set():
            try:
                self.root.deiconify() # Show the window
            except tk.TclError as e:
                 logging.warning(f"Failed to show tooltip (window likely closed): {e}")
                 self._stop_event.set()

    def _hide_tooltip(self):
        # Allow hiding even if stop_event is set, for cleanup purposes? Maybe not.
        # Let's only hide if not stopping.
        if self.root and not self._stop_event.is_set():
            try:
                self.root.withdraw() # Hide the window
            except tk.TclError as e:
                 logging.warning(f"Failed to hide tooltip (window likely closed): {e}")
                 self._stop_event.set()


# --- Status Indicator Manager Class (NEW) ---
class StatusIndicatorManager:
    """Manages a Tkinter status icon window (mic icon + volume)."""
    def __init__(self, q):
        self.queue = q
        self.root = None
        self.canvas = None
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event()
        self.current_volume = 0.0 # Store current volume level (0.0 to 1.0)
        self.current_state = "hidden" # "hidden", "idle", "active"
        self.last_pos = (0, 0)

        # Icon drawing properties
        self.icon_width = 24
        self.icon_height = 36
        self.mic_body_color = "#CCCCCC" # Light grey for mic body
        self.mic_stand_color = "#AAAAAA" # Darker grey for stand
        self.volume_fill_color = "#FF0000" # Red for volume level
        self.idle_indicator_color = "#ADD8E6" # Light blue when ready but not recording
        self.bg_color = "#FEFEFE" # Use a near-white color for transparency key
        # --- Add config references (if needed later, e.g. for icon colors) ---
        # self._apply_status_config()

    def start(self):
        self.thread.start()
        self._tk_ready.wait(timeout=2.0)
        if not self._tk_ready.is_set():
            logging.warning("StatusIndicator Tkinter thread did not become ready.")

    def stop(self):
        logging.debug("Stop requested for StatusIndicatorManager.")
        self._stop_event.set()
        try:
            self.queue.put_nowait(("stop", None))
        except queue.Full:
            logging.warning("StatusIndicator queue full when sending stop command.")

    def _run_tkinter(self):
        logging.info("StatusIndicator thread started.")
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.wm_attributes("-topmost", True)
            self.root.attributes("-transparentcolor", self.bg_color)
            self.root.config(bg=self.bg_color)

            self.canvas = tk.Canvas(self.root, width=self.icon_width, height=self.icon_height,
                                    bg=self.bg_color, highlightthickness=0)
            self.canvas.pack()

            self._tk_ready.set()
            logging.debug("StatusIndicator Tkinter objects created.")
            self._check_queue() # Start the queue check / redraw loop
            self.root.mainloop()
            logging.debug("StatusIndicator mainloop finished.")

        except Exception as e:
            logging.error(f"Error during StatusIndicator mainloop/setup: {e}", exc_info=True)
            self._tk_ready.set()
        finally:
            logging.info("StatusIndicator thread finished.")
            self._stop_event.set()

    def _check_queue(self):
        if self._stop_event.is_set():
            self._cleanup_tk()
            return

        needs_redraw = False
        new_state = self.current_state
        new_pos = self.last_pos

        try:
            while not self.queue.empty():
                command, data = self.queue.get_nowait()
                if command == "volume":
                    if self.current_state == "active":
                        new_volume = data
                        # Only redraw if volume changed significantly
                        if abs(new_volume - self.current_volume) > 0.02:
                             self.current_volume = new_volume
                             needs_redraw = True
                elif command == "state":
                    target_state = data.get("state", "hidden")
                    pos = data.get("pos", self.last_pos)
                    if target_state != self.current_state:
                        new_state = target_state
                        # Reset volume when becoming active
                        if new_state == "active": self.current_volume = 0.0
                        needs_redraw = True
                    if pos != self.last_pos:
                        new_pos = pos
                        # Reposition immediately, redraw handles visibility/state change
                        self._position_window(new_pos)
                        self.last_pos = new_pos
                        # If only position changed, but state didn't, still need redraw if visible
                        if new_state != "hidden" and not needs_redraw:
                             needs_redraw = True

                elif command == "stop":
                    logging.debug("Received stop command in StatusIndicator queue.")
                    self._stop_event.set()
                    # Let the check at the start handle cleanup

        except queue.Empty: pass
        except tk.TclError as e:
            logging.warning(f"StatusIndicator Tkinter error during queue processing: {e}.")
            self._stop_event.set(); self._cleanup_tk(); return
        except Exception as e:
            logging.error(f"Error processing StatusIndicator queue: {e}", exc_info=True)

        # Update state and redraw if needed
        if new_state != self.current_state:
             self.current_state = new_state
             # Visibility is handled by state change redraw
             needs_redraw = True # Ensure redraw on state change

        if needs_redraw and self.root and not self._stop_event.is_set():
            self._draw_icon()
            if self.current_state == "hidden":
                 self.root.withdraw()
            else:
                 self.root.deiconify()

        # Reschedule
        if not self._stop_event.is_set() and self.root:
             try: self.root.after(50, self._check_queue) # ~20 FPS updates
             except tk.TclError: logging.warning("StatusIndicator root destroyed before rescheduling.")
             except Exception as e: logging.error(f"Error rescheduling StatusIndicator check: {e}")

    def _cleanup_tk(self):
        logging.debug("Executing StatusIndicator _cleanup_tk.")
        if self.root:
            try:
                self.root.destroy()
                logging.info("StatusIndicator root window destroyed.")
                self.root = None
            except Exception as e: logging.warning(f"Error destroying StatusIndicator root: {e}")

    def _position_window(self, pos):
        if self.root and not self._stop_event.is_set():
            try:
                x, y = pos
                offset_x = 5
                offset_y = 15 # Position slightly below cursor
                self.root.geometry(f"+{x + offset_x}+{y + offset_y}")
            except Exception as e:
                 logging.warning(f"Failed to position StatusIndicator: {e}")

    def _draw_icon(self):
        """Draws the microphone icon based on the current state and volume."""
        if not self.canvas or not self.root or self._stop_event.is_set():
            return

        try:
            self.canvas.delete("all") # Clear previous drawing

            if self.current_state == "hidden":
                return # Nothing to draw

            w, h = self.icon_width, self.icon_height
            body_w = w * 0.6
            body_h = h * 0.6
            body_x = (w - body_w) / 2
            body_y = h * 0.1

            stand_h = h * 0.2
            stand_y = body_y + body_h
            stand_w = w * 0.2
            stand_x = (w - stand_w) / 2

            base_h = h * 0.1
            base_y = stand_y + stand_h
            base_w = w * 0.8
            base_x = (w - base_w) / 2

            # Draw stand and base
            self.canvas.create_rectangle(stand_x, stand_y, stand_x + stand_w, stand_y + stand_h, fill=self.mic_stand_color, outline="")
            self.canvas.create_rectangle(base_x, base_y, base_x + base_w, base_y + base_h, fill=self.mic_stand_color, outline="")

            # Draw mic body outline (rounded rectangle)
            # Tkinter canvas doesn't have direct rounded rect, approximate or use simple rect
            self.canvas.create_rectangle(body_x, body_y, body_x + body_w, body_y + body_h, fill=self.mic_body_color, outline=self.mic_stand_color)

            # Draw fill based on state
            if self.current_state == "idle":
                # Draw a small idle indicator inside
                idle_r = body_w * 0.2
                idle_cx = body_x + body_w / 2
                idle_cy = body_y + body_h / 2
                self.canvas.create_oval(idle_cx - idle_r, idle_cy - idle_r, idle_cx + idle_r, idle_cy + idle_r,
                                        fill=self.idle_indicator_color, outline="")
            elif self.current_state == "active":
                # Draw volume level fill from bottom up
                fill_h = body_h * self.current_volume
                fill_y = body_y + body_h - fill_h
                if fill_h > 0:
                    self.canvas.create_rectangle(body_x, fill_y, body_x + body_w, body_y + body_h,
                                                 fill=self.volume_fill_color, outline="")

        except tk.TclError as e:
            logging.warning(f"Error drawing status icon (window closed?): {e}")
            self._stop_event.set()
        except Exception as e:
            logging.error(f"Unexpected error drawing status icon: {e}", exc_info=True)


# --- Placeholder/Handler Functions ---
def simulate_typing(text):
    """Simulates typing the given text."""
    logging.info(f"Simulating type: '{text}'")
    kb_controller.type(text)

def simulate_backspace(count):
    """Simulates pressing backspace multiple times."""
    logging.info(f"Simulating {count} backspaces")
    for _ in range(count):
        kb_controller.press(keyboard.Key.backspace)
        kb_controller.release(keyboard.Key.backspace)
        time.sleep(0.01) # Small delay between key presses

def handle_dictation_interim(transcript):
    """Handles interim dictation results by displaying them in a temporary tooltip."""
    global last_simulated_text # Keep variable for now, but don't use for typing
    if not transcript:
        return

    # Log the interim transcript
    logging.debug(f"Interim Received (for Tooltip): '{transcript}'")

    # --- Tooltip Update Logic ---
    try:
        # Get current mouse position
        x, y = pyautogui.position()
        # Send update command to the tooltip manager thread
        # Use put_nowait or handle Full exception if queue might fill up,
        # but for this use case, blocking put is likely fine.
        tooltip_queue.put(("update", (transcript, x, y)))
        tooltip_queue.put(("show", None)) # Ensure it's visible
    except pyautogui.FailSafeException:
         logging.warning("PyAutoGUI fail-safe triggered (mouse moved to corner?).")
    except Exception as e:
        logging.error(f"Error getting mouse position or updating tooltip: {e}")

    # --- REMOVED TYPING/BACKSPACE LOGIC ---
    # last_simulated_text = transcript # DO NOT UPDATE STATE HERE

def handle_dictation_final(final_transcript, history):
    """Handles the final dictation transcript segment based on history.
    Calculates target state, determines diff from current state, executes typing, updates history.
    Also hides the interim tooltip.

    Returns:
        tuple: (updated_history_list, final_text_string_typed)
               The final_text_string_typed includes the trailing space if added.
    """
    logging.debug(f"Handling final dictation segment: '{final_transcript}'")

    # --- Hide the interim tooltip ---
    try:
        tooltip_queue.put_nowait(("hide", None))
    except queue.Full:
        logging.warning("Tooltip queue full when trying to hide on final.")

    # --- Step A: Calculate Target Word List ---
    target_words = [entry['text'] for entry in history] # Start with existing words
    logging.debug(f"Initial target_words from history: {target_words}")

    original_words = final_transcript.split()
    punctuation_to_strip = '.,!?;:'

    # Process the new transcript segment against the target list
    for word in original_words:
        if not word: continue
        cleaned_word = word.rstrip(punctuation_to_strip).lower()

        if cleaned_word == "back":
            if target_words:
                removed = target_words.pop()
                logging.info(f"Processing 'back', removed '{removed}' from target_words.")
            else:
                logging.info(f"Processing 'back', but target_words already empty.")
        else:
            target_words.append(word) # Append original word with punctuation

    logging.debug(f"Final target_words after processing segment: {target_words}")

    # --- Step B: Calculate Target Text ---
    target_text = " ".join(target_words) + (' ' if target_words else '') # Add trailing space
    logging.debug(f"Calculated target_text (with space): '{target_text}'")

    # --- Step C: Calculate Current Text on Screen (Estimate from OLD history) ---
    current_text_estimate = " ".join([entry['text'] for entry in history]) + (' ' if history else '')
    logging.debug(f"Estimated current text (from old history, with space): '{current_text_estimate}'")

    # --- Step D: Calculate Diff ---
    common_prefix_len = 0
    min_len = min(len(current_text_estimate), len(target_text))
    while common_prefix_len < min_len and current_text_estimate[common_prefix_len] == target_text[common_prefix_len]:
        common_prefix_len += 1

    backspaces_needed = len(current_text_estimate) - common_prefix_len
    text_to_type = target_text[common_prefix_len:]

    logging.debug(f"Diff Calculation: Prefix={common_prefix_len}, Backspaces={backspaces_needed}, Type='{text_to_type}'")

    # --- Step E: Execute Typing Actions ---
    if backspaces_needed > 0:
        simulate_backspace(backspaces_needed)
    if text_to_type:
        simulate_typing(text_to_type)

    # --- Step F: Update History to Match Target State ---
    new_history = []
    if target_words:
        logging.debug(f"Rebuilding history with: {target_words}")
        for word in target_words:
            if word:
                length_with_space = len(word) + 1
                entry = {"text": word, "length_with_space": length_with_space}
                new_history.append(entry)
    else:
        logging.debug("History cleared as target_words is empty.")

    # Return the updated history AND the final text string *as typed* (including space)
    return new_history, target_text

def handle_command_interim(transcript):
    """Displays interim command transcript (e.g., in a UI)."""
    global current_command_transcript
    logging.info(f"Interim Command: '{transcript}'")
    current_command_transcript = transcript
    # TODO: Update command feedback UI
    pass

def handle_command_final(final_transcript):
    """Stores the final command transcript."""
    global current_command_transcript
    logging.info(f"Final Command Transcript: '{final_transcript}'")
    current_command_transcript = final_transcript
    # Final command execution happens on button release in main loop
    pass

def execute_command(command_text):
    """Interprets and executes the command."""
    global last_command_executed
    logging.info(f"Executing Command: '{command_text}'")
    last_command_executed = None # Reset undo state
    # TODO: Send to AI for interpretation
    # TODO: Map interpretation to action (keyboard sim / script exec)
    # Example: Simple mapping
    if "press enter" in command_text.lower():
        logging.info("Action: Simulating Enter key")
        kb_controller.press(keyboard.Key.enter)
        kb_controller.release(keyboard.Key.enter)
        last_command_executed = ("key_press", keyboard.Key.enter)
    # TODO: Execute action safely
    # TODO: Store action details in last_command_executed for undo
    pass

def undo_last_command():
    """Attempts to undo the last executed command."""
    logging.info(f"Attempting Undo for: {last_command_executed}")
    # TODO: Implement undo logic based on stored action
    pass

# --- NEW/MODIFIED: Translation Function ---
async def translate_and_type(text_to_translate, source_lang_code, target_lang_code):
    """Translates text using OpenAI and types the result."""
    global openai_client, OPENAI_MODEL # Use configured model
    if not openai_client:
        logging.error("OpenAI client not available. Cannot translate.")
        simulate_typing(" [Translation Error: OpenAI not configured]")
        return
    if not text_to_translate:
        logging.warning("No text provided for translation.")
        return
    if not source_lang_code or not target_lang_code:
        logging.error(f"Missing source ({source_lang_code}) or target ({target_lang_code}) language for translation.")
        simulate_typing(" [Translation Error: Language missing]")
        return
    if source_lang_code == target_lang_code:
         logging.info("Source and target languages are the same, skipping translation call.")
         return # No need to translate if languages match

    # Get full language names for the prompt (optional, but potentially helpful for the model)
    # Using TARGET_LANGUAGE_OPTIONS from systray might be fragile if systray isn't imported/run
    # Let's stick to codes for now, or define a local map if needed.
    source_lang_name = source_lang_code # Or lookup full name
    target_lang_name = target_lang_code # Or lookup full name

    logging.info(f"Requesting translation from '{source_lang_name}' to '{target_lang_name}' for: '{text_to_translate}' using model '{OPENAI_MODEL}'")
    # Indicate translation is starting *after* the source text's trailing space
    simulate_typing("-> ") # Add space after arrow

    try:
        prompt = f"Translate the following text accurately from {source_lang_name} to {target_lang_name}. Output only the translated text:\n\n{text_to_translate}"

        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL, # Use the model from config
            messages=[
                {"role": "system", "content": "You are an expert translation engine."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2, # Lower temperature for more direct translation
            max_tokens=int(len(text_to_translate) * 2.5 + 50) # Generous token estimate
        )

        translated_text = response.choices[0].message.content.strip()
        logging.info(f"Translation received: '{translated_text}'")

        if translated_text:
            # Type the translation, followed by a space for subsequent typing
            simulate_typing(translated_text + " ")
        else:
            logging.warning("OpenAI returned an empty translation.")
            simulate_typing("[Translation Empty] ")

    except Exception as e:
        logging.error(f"Error during OpenAI translation request: {e}", exc_info=True)
        simulate_typing(f"[Translation Error: {type(e).__name__}] ")


# --- Deepgram Event Handlers ---
async def on_open(self, open, **kwargs):
    logging.info("Deepgram connection opened.")

async def on_message(self, result, **kwargs):
    global typed_word_history, final_source_text # Keep global here to update the list reference
    try:
        transcript = result.channel.alternatives[0].transcript
        if not transcript:
            return

        if current_mode == "dictation": # Handles both dictation and dictation+translation
            if result.is_final:
                final_part = transcript # Get the final utterance part
                # Pass history in, get potentially modified history back AND the text typed
                updated_history, text_typed_this_segment = handle_dictation_final(final_part, typed_word_history)
                typed_word_history = updated_history # Update history state

                # --- Accumulate the final source text ---
                # We rebuild the full text from the *updated* history after processing the segment
                final_source_text = " ".join([entry['text'] for entry in typed_word_history])
                logging.debug(f"Dictation final source text updated: '{final_source_text}'")
            else:
                # Interim handler updates tooltip
                handle_dictation_interim(transcript)

        elif current_mode == "command":
            if result.is_final:
                handle_command_final(transcript)
            else:
                handle_command_interim(transcript)

    except (AttributeError, IndexError) as e:
        logging.error(f"Error processing Deepgram message: {e} - Result: {result}")
    except Exception as e: # Catch potential errors from handlers
        logging.error(f"Unhandled error in on_message handler: {e}", exc_info=True)

async def on_metadata(self, metadata, **kwargs):
    logging.debug(f"Deepgram Metadata: {metadata}")

async def on_speech_started(self, speech_started, **kwargs):
    logging.debug("Deepgram Speech Started")

async def on_utterance_end(self, utterance_end, **kwargs):
    logging.debug("Deepgram Utterance Ended")

async def on_error(self, error, **kwargs):
    logging.error(f"Deepgram Handled Error: {error}")

async def on_close(self, close, **kwargs):
    logging.info("Deepgram connection closed.")

async def on_unhandled(self, unhandled, **kwargs):
    logging.warning(f"Deepgram Unhandled Websocket Message: {unhandled}")

# --- Pynput Listener Callbacks ---
def on_click(x, y, button, pressed):
    global current_mode, start_time, status_queue
    global is_dictation_active, is_command_active # REMOVED is_interpreter_active
    global transcription_active_event
    global typed_word_history, final_source_text # Reset state vars

    trigger_mode = None
    active_event = None
    required_modifier = None
    modifier_str = None # For logging

    # Determine which mode is being triggered, checking modifiers
    if button == DICTATION_TRIGGER_BUTTON:
        # Ensure Command modifier (if any) is NOT pressed for dictation
        is_command_modifier_pressed = (COMMAND_MODIFIER_KEY is not None and COMMAND_MODIFIER_KEY in modifier_keys_pressed)
        if not is_command_modifier_pressed:
            trigger_mode = "dictation"
            active_event = is_dictation_active
        else:
             logging.debug(f"Dictation button ({button}) pressed, but command modifier ({COMMAND_MODIFIER_KEY_STR}) is also pressed. Ignoring.")
             return

    elif COMMAND_TRIGGER_BUTTON is not None and button == COMMAND_TRIGGER_BUTTON:
        modifier_ok = (COMMAND_MODIFIER_KEY is None or COMMAND_MODIFIER_KEY in modifier_keys_pressed)
        if modifier_ok:
            trigger_mode = "command"
            active_event = is_command_active
            required_modifier = COMMAND_MODIFIER_KEY
            modifier_str = COMMAND_MODIFIER_KEY_STR
        else:
            logging.debug(f"Command button ({button}) pressed, but required modifier ({COMMAND_MODIFIER_KEY_STR}) is not pressed. Ignoring.")
            return

    else:
        return # Ignore other button clicks

    if trigger_mode:
        if pressed:
            if not transcription_active_event.is_set(): # Start only if nothing else is active
                mod_log_str = f" with {modifier_str} " if required_modifier else ""
                logging.info(f"{trigger_mode.capitalize()} button pressed{mod_log_str}- starting mode.")
                # Clear potentially lingering state
                is_dictation_active.clear()
                is_command_active.clear()
                # --- Reset state specific to the mode being started ---
                if trigger_mode == "dictation":
                    typed_word_history.clear()
                    final_source_text = ""
                elif trigger_mode == "command":
                    global current_command_transcript
                    current_command_transcript = ""

                # Set the active mode
                active_event.set()
                transcription_active_event.set()
                current_mode = trigger_mode
                start_time = time.time() # Record start time for duration check

                # Show Status Indicator
                try:
                    status_queue.put_nowait(("state", {"state": "active", "pos": (x, y)}))
                except queue.Full:
                    logging.warning("Status queue full when trying to show indicator.")

            else:
                logging.warning(f"Attempted to start {trigger_mode} while already active ({current_mode})")
        else: # Button released
            if active_event and active_event.is_set():
                duration = time.time() - start_time if start_time else 0
                logging.info(f"{trigger_mode.capitalize()} button released (duration: {duration:.2f}s). Stopping mode.")
                # Clear events to signal stopping
                active_event.clear()
                transcription_active_event.clear() # Signal main loop to stop DG/Mic

                # Hide Tooltip & Status Indicator
                if trigger_mode == "dictation": # Tooltip only for dictation
                    try: tooltip_queue.put_nowait(("hide", None))
                    except queue.Full: logging.warning("Tooltip queue full on release.")
                try:
                    status_queue.put_nowait(("state", {"state": "hidden"}))
                except queue.Full:
                    logging.warning("Status queue full when trying to hide indicator.")

                # Post-processing (like translation) is handled in the main loop after stopping
                # Don't reset current_mode here, main loop needs it

def on_press(key):
    global current_mode, modifier_keys_pressed
    # Track modifier key presses
    if key in PYNPUT_MODIFIER_MAP.values() and key is not None:
         logging.debug(f"Modifier pressed: {key}")
         modifier_keys_pressed.add(key)

    try:
        if is_command_active.is_set() and key == keyboard.Key.esc:
            logging.info("ESC pressed during command - cancelling.")
            is_command_active.clear()
            transcription_active_event.clear() # Signal stop
            current_mode = "cancel" # Special mode to indicate cancellation
            # TODO: Hide command feedback UI
            
        # TODO: Add keybind for undo_last_command()?
            
    except AttributeError:
        pass
    except Exception as e: # Catch potential errors
        logging.error(f"Error in on_press handler: {e}", exc_info=True)

def on_release(key):
    """Callback for key release events."""
    global modifier_keys_pressed, status_queue
    global is_command_active, transcription_active_event # REMOVED is_interpreter_active

    # Track modifier key releases
    if key in modifier_keys_pressed:
        logging.debug(f"Modifier released: {key}")
        modifier_keys_pressed.discard(key)

    # If the command modifier key is released while the command trigger button is still held, stop command mode.
    if key == COMMAND_MODIFIER_KEY and is_command_active.is_set():
         logging.info(f"Command modifier ({key}) released while command mode active. Stopping mode.")
         is_command_active.clear()
         transcription_active_event.clear() # Signal stop
         # Hide Status Indicator
         try: status_queue.put_nowait(("state", {"state": "hidden"}))
         except queue.Full: logging.warning("Status queue full hiding indicator on cmd mod release.")
         # Post-processing will happen in the main loop's stop flow


# --- Main Application Logic ---
async def main():
    global start_time, current_mode, current_command_transcript, tooltip_queue, status_queue
    global config # Make main config global for reloading
    global final_source_text # Access final dictation text
    global typed_word_history # Access dictation history

    logging.info("Starting Vibe App...")

    # --- Initialize Systray UI --- (Before other UI components)
    systray_icon = None
    systray_thread = threading.Thread(target=systray_ui.run_systray, args=(systray_ui.exit_app_event,), daemon=True)
    systray_thread.start()
    logging.info("Systray UI thread started.")

    # --- Start Tooltip Manager ---
    tooltip_mgr = TooltipManager(tooltip_queue)
    tooltip_mgr.start()
    logging.info("Tooltip Manager started.")

    # --- Start Status Indicator Manager ---
    status_mgr = StatusIndicatorManager(status_queue)
    status_mgr.start()
    logging.info("Status Indicator Manager started.")

    # --- Initialize and Start Buffered Audio Input --- 
    # Replaces the old AudioMonitor
    buffered_audio_input = BufferedAudioInput(status_queue) 
    buffered_audio_input.start()
    logging.info("Buffered Audio Input thread started.")

    # Initialize Deepgram Client with reduced verbosity
    try:
        config_dg = DeepgramClientOptions(verbose=logging.WARNING)
        deepgram: DeepgramClient = DeepgramClient(DEEPGRAM_API_KEY, config_dg)
    except Exception as e:
        logging.error(f"Failed to initialize Deepgram client: {e}")
        # No need to exit here, API key check already happened
        return

    # Start pynput listeners
    mouse_listener = mouse.Listener(on_click=on_click)
    keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener.start()
    keyboard_listener.start()
    logging.info("Input listeners started.")

    dg_connection = None
    microphone = None
    active_mode_on_stop = None # Store the mode when stopping

    try:
        while True:
            # Start Transcription Flow
            if transcription_active_event.is_set() and dg_connection is None:
                logging.info(f"Activating {current_mode} mode...")
                # Reset relevant state variables at the START of activation
                if current_mode == "dictation":
                    typed_word_history.clear()
                    final_source_text = "" # Reset final source text accumulator
                elif current_mode == "command":
                    current_command_transcript = ""

                try:
                    # 1. Get connection object
                    dg_connection = deepgram.listen.asyncwebsocket.v("1")

                    # 2. Register handlers
                    dg_connection.on(LiveTranscriptionEvents.Open, on_open)
                    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
                    dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
                    dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
                    dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
                    dg_connection.on(LiveTranscriptionEvents.Error, on_error)
                    dg_connection.on(LiveTranscriptionEvents.Close, on_close)
                    dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)

                    # 3. Define options using configured SOURCE language
                    options = LiveOptions(
                        model="nova-2",
                        language=SELECTED_LANGUAGE, # Use SOURCE language
                        smart_format=True,
                        interim_results=True, utterance_end_ms="1000",
                        vad_events=True, endpointing=300,
                        encoding="linear16", channels=1, sample_rate=16000,
                        # Keep word-level timestamps if handle_dictation_final relies on them implicitly
                        # (Though current implementation seems okay without explicit timestamps)
                        punctuate=True, # Enable punctuation
                        numerals=True, # Enable number formatting
                    )
                    
                    # 4. Start connection
                    await dg_connection.start(options)

                    # 4.5 Send buffered audio FIRST
                    pre_activation_buffer = buffered_audio_input.get_buffer()
                    if pre_activation_buffer:
                        logging.info(f"Sending {len(pre_activation_buffer)} chunks from pre-activation buffer...")
                        for chunk in pre_activation_buffer:
                            if dg_connection and await dg_connection.is_connected():
                                try: await dg_connection.send(chunk)
                                except Exception as send_e:
                                    logging.error(f"Error sending buffered chunk: {send_e}")
                                    break # Stop sending buffer if error occurs
                            else:
                                logging.warning("Connection closed before finishing sending buffer.")
                                break
                        logging.info("Finished sending pre-activation buffer.")
                    else:
                        logging.info("Pre-activation buffer was empty.")

                    # 5. Initialize and start *Deepgram* Microphone
                    #    This uses the *original* logging_send_wrapper
                    original_send = dg_connection.send
                    async def logging_send_wrapper(data):
                        # (Keep the corrected version from previous step)
                        is_conn_connected = False
                        if dg_connection:
                            try: is_conn_connected = await dg_connection.is_connected()
                            except Exception as check_e: logging.error(f"Error checking dg_connection state: {check_e}")
                        if is_conn_connected:
                            logging.debug(f"DG Mic sending {len(data)} bytes...")
                            try: await original_send(data)
                            except asyncio.CancelledError: logging.warning("DG Send cancelled.")
                            except Exception as send_e: logging.error(f"Error during dg_connection.send: {send_e}")
                        else: logging.warning("DG Mic send attempted but connection closed.")

                    microphone = Microphone(logging_send_wrapper) # DEEPGRAM Microphone
                    microphone.start()
                    logging.info("Deepgram connection and microphone started.")

                    # Buffered audio input is already running continuously

                except Exception as e:
                    logging.error(f"Failed to start Deepgram/Microphone: {e}")
                    # Reset state fully on failure
                    if dg_connection:
                       try: await dg_connection.finish()
                       except Exception: pass
                    is_dictation_active.clear()
                    is_command_active.clear()
                    transcription_active_event.clear()
                    current_mode = None
                    dg_connection = None
                    microphone = None
                    # Hide Status Indicator on failure
                    try: status_queue.put_nowait(("state", {"state": "hidden"}))
                    except queue.Full: pass


            # Stop Transcription Flow
            elif not transcription_active_event.is_set() and dg_connection is not None:
                active_mode_on_stop = current_mode # Capture mode *before* resetting
                logging.info(f"Deactivating {active_mode_on_stop} mode...")
                duration = time.time() - start_time if start_time else 0
                start_time = None # Reset start time

                # Ensure tooltip & status are hidden
                try: tooltip_queue.put_nowait(("hide", None))
                except queue.Full: logging.warning("Tooltip queue full on deactivate.")
                try: status_queue.put_nowait(("state", {"state": "hidden"}))
                except queue.Full: logging.warning("Status queue full on deactivate.")

                # 1. Stop *Deepgram* microphone
                if microphone:
                    microphone.finish()
                    microphone = None
                    logging.info("Deepgram microphone finished.")

                # 2. Stop Deepgram connection
                if dg_connection:
                    try:
                        # Wait briefly for any final messages from DG before finishing
                        await asyncio.sleep(0.1)
                        await dg_connection.finish()
                        logging.info("Deepgram connection finished.")
                    except Exception as e:
                         logging.error(f"Error finishing deepgram connection: {e}")
                    dg_connection = None

                # 3. Post-processing (check duration, handle final transcripts/commands, TRANSLATE)
                translation_task = None # Initialize translation task placeholder
                if active_mode_on_stop == "cancel":
                     logging.info("Command cancelled by user.")
                elif duration >= MIN_DURATION_SEC:
                    if active_mode_on_stop == "dictation":
                        # Final source text is already in final_source_text (built by on_message)
                        # Check if translation is needed
                        if TARGET_LANGUAGE and TARGET_LANGUAGE != SELECTED_LANGUAGE and final_source_text:
                            logging.info("Dictation finished, initiating translation...")
                            # Create the translation task, but don't await it here
                            # to allow state reset to happen quickly.
                            translation_task = asyncio.create_task(
                                translate_and_type(final_source_text.strip(), SELECTED_LANGUAGE, TARGET_LANGUAGE)
                            )
                        else:
                            logging.info("Dictation finished. No translation needed (target lang same or none, or no text).")
                            # Ensure the trailing space added by handle_dictation_final is kept if no translation
                            # (It should already be typed)

                    elif active_mode_on_stop == "command":
                        execute_command(current_command_transcript)
                else:
                    logging.info(f"Transcription duration ({duration:.2f}s) less than minimum ({MIN_DURATION_SEC}s), discarding.")
                    # Clear history/text even if discarded
                    if active_mode_on_stop == "dictation":
                        typed_word_history.clear()
                        final_source_text = ""


                # 4. Reset state (happens *before* awaiting translation)
                current_mode = None
                current_command_transcript = ""
                # Don't reset final_source_text here if translation is pending
                # typed_word_history is reset at the *start* of the next dictation

                # 5. Await translation task if it was created
                if translation_task:
                    try:
                        logging.debug("Waiting for translation task to complete...")
                        await translation_task
                        logging.debug("Translation task finished.")
                    except Exception as e:
                        logging.error(f"Error occurred during await of translation task: {e}", exc_info=True)


            # --- Check for Config Reload Event from Systray ---
            if systray_ui.config_reload_event.is_set():
                logging.info("Detected config reload request from systray.")
                old_source_language = SELECTED_LANGUAGE # Store language before reload
                old_target_language = TARGET_LANGUAGE
                logging.debug(f"Lang before reload: Source={old_source_language}, Target={old_target_language}")

                config = load_config() # Reload the config dictionary
                apply_config(config)  # Apply the new config to global vars
                logging.debug(f"Lang after applying new config: Source={SELECTED_LANGUAGE}, Target={TARGET_LANGUAGE}")

                # Check if source language changed and connection is active
                source_language_changed = (SELECTED_LANGUAGE != old_source_language)
                restart_dg_needed = (dg_connection is not None and source_language_changed)

                # Notify UI components that need updating
                if tooltip_mgr:
                    tooltip_mgr.reload_config()
                # if status_mgr: # If status icon needs config updates
                #    status_mgr.reload_config()
                systray_ui.config_reload_event.clear() # Clear the event AFTER processing
                logging.debug("Config reload event cleared.")

                # Restart Deepgram connection if source language changed mid-session
                if restart_dg_needed:
                    logging.info("Source language changed mid-session, restarting Deepgram connection.")
                    # Reset state and restart
                    is_dictation_active.clear()
                    is_command_active.clear()
                    transcription_active_event.clear()
                    current_mode = None
                    dg_connection = None
                    microphone = None
                    # Hide Status Indicator on restart
                    try: status_queue.put_nowait(("state", {"state": "hidden"}))
                    except queue.Full: pass

            # Add checks for thread health
            if not tooltip_mgr.thread.is_alive() and not tooltip_mgr._stop_event.is_set():
                 logging.error("TooltipManager thread terminated unexpectedly. Stopping.")
                 break
            if not status_mgr.thread.is_alive() and not status_mgr._stop_event.is_set():
                 logging.error("StatusIndicatorManager thread terminated unexpectedly. Stopping.")
                 break
            # Audio monitor thread only runs when active, so no check here

            await asyncio.sleep(0.05)

    except (asyncio.CancelledError, KeyboardInterrupt):
        logging.info("Main task cancelled or interrupted.")
    except Exception as e:
         logging.error(f"Unexpected error in main loop: {e}", exc_info=True)
    finally:
        logging.info("Stopping Vibe App...")

        # --- Stop Audio Monitor if still running ---
        # if 'audio_monitor' in locals(): audio_monitor.stop()
        # --- Stop Buffered Audio Input --- 
        if 'buffered_audio_input' in locals(): 
            buffered_audio_input.stop()

        # --- Stop Systray --- (Needs access to the icon object to stop it)
        # This is tricky because icon is created in the thread.
        # Option 1: Use a global or shared object (less ideal)
        # Option 2: Signal the thread to stop itself (better)
        # For now, relying on daemon thread termination, but pystray might need explicit stop.
        # We added icon.stop() in the systray's on_exit_clicked, needs integration.
        # Let's try stopping the icon from the systray thread when on_exit_clicked is called.
        # We also need a way to signal the main loop to exit if 'Exit' is clicked.
        # ADDING an exit event for this.
        if systray_ui.exit_app_event.is_set(): # Check if systray requested exit
             logging.info("Exit requested via systray menu.")
             # No need to explicitly stop systray thread if it stopped itself via icon.stop()
        elif systray_thread and systray_thread.is_alive():
             logging.warning("Systray thread still alive, explicit stop not implemented yet.")
             # Ideally, we'd signal the systray thread to call icon.stop()
             # For now, daemon should handle it, but might not be clean.

        # Signal GUI managers to stop
        if 'tooltip_mgr' in locals(): tooltip_mgr.stop()
        if 'status_mgr' in locals(): status_mgr.stop()
        logging.info("GUI Managers stop requested.")

        # Cleanup listeners (ensure they exist before stopping/joining)
        if 'mouse_listener' in locals() and mouse_listener.is_alive():
            logging.debug("Stopping mouse listener...")
            mouse_listener.stop()
            mouse_listener.join(timeout=0.5)
            logging.info("Mouse listener stopped.")
        if 'keyboard_listener' in locals() and keyboard_listener.is_alive():
            logging.debug("Stopping keyboard listener...")
            keyboard_listener.stop()
            keyboard_listener.join(timeout=0.5)
            logging.info("Keyboard listener stopped.")

        # Ensure Deepgram microphone and connection are stopped
        if microphone:
            logging.debug("Finishing Deepgram microphone on exit...")
            microphone.finish() # Stop microphone first
            logging.info("Deepgram microphone finished on exit.")
            await asyncio.sleep(0.01) # Allow loop iteration for mic cleanup tasks

        # Check connection state *before* finishing
        is_conn_connected_final = False
        if dg_connection:
             try:
                 is_conn_connected_final = await dg_connection.is_connected()
             except Exception: pass # Ignore check error during shutdown

             if is_conn_connected_final:
                 logging.debug("Finishing Deepgram connection on exit...")
                 try:
                     await dg_connection.finish()
                     logging.info("Deepgram connection finished on exit.")
                 except asyncio.CancelledError:
                      logging.warning("Deepgram finish cancelled, likely during shutdown.")
                 except Exception as e:
                      logging.error(f"Error during final dg_connection.finish: {e}")
             else:
                  logging.info("Deepgram connection already closed or not connected on exit.")
        else:
             logging.info("No active Deepgram connection to finish on exit.")

        logging.info("Vibe App finished.")


# --- Add Exit Event for Systray Communication ---
systray_ui.exit_app_event = threading.Event() # Create event in main module

if __name__ == "__main__":
    # API Key check moved earlier, before main()
    # Check if config loading failed catastrophically (though load_config tries to return defaults)
    if config is None:
         print("CRITICAL: Configuration could not be loaded. Exiting.")
         # Logging might not be set up yet, so print
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("Application interrupted by user (Ctrl+C).")
        except Exception as e:
            logging.error(f"An unexpected error occurred in main run: {e}", exc_info=True) 