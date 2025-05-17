# mic_ui_manager.py
import asyncio
import logging
import tkinter as tk
from tkinter import ttk
import threading
import math
import time
from typing import Optional, Any

# Assuming GVM access and constants
# from global_variables_manager import GlobalVariablesManager
from constants import (
    STATE_AUDIO_STATUS, # e.g., idle, recording, error
    STATE_STT_SESSION_STATUS_TEMPLATE, # e.g., connecting, connected, error...
    STATE_INPUT_DICTATION_KEY_PRESSED, # To show active state?
    STATE_UI_MIC_MODE
    # Add STATE_AUDIO_VOLUME if RMS calculation is reintroduced
)

logger = logging.getLogger(__name__)

class MicUIManager:
    """Manages the microphone status indicator UI."""

    WIDTH, HEIGHT = 100, 100 # Adjust size as needed
    CENTER_X, CENTER_Y = WIDTH // 2, HEIGHT // 2
    RADIUS = min(CENTER_X, CENTER_Y) - 10

    # Colors (can be moved to config/GVM)
    COLOR_BG = '#2E2E2E' # Dark background
    COLOR_IDLE = '#606060' # Grey
    COLOR_ACTIVE = '#4CAF50' # Green
    COLOR_CONNECTING = '#FFC107' # Amber
    COLOR_ERROR = '#F44336' # Red
    COLOR_OUTLINE = '#FFFFFF'

    def __init__(self, gvm: Any, main_loop: asyncio.AbstractEventLoop):
        logger.info("MicUIManager initialized.")
        self.gvm = gvm
        self.main_loop = main_loop # Store the main loop
        self.root: Optional[tk.Tk] = None
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.canvas: Optional[tk.Canvas] = None
        self._current_state = "idle" # internal state: idle, active, connecting, error
        self._active_session_id: Optional[str] = None # Track current session if needed
        self._is_ui_visible = False # Track UI visibility
        self._last_update_time = 0
        self._update_interval = 0.1 # How often to check GVM state (seconds)

    async def init(self) -> bool:
        logger.info("MicUIManager initialized.")
        # Load any specific config from GVM if needed
        return True

    def _start_ui_thread_if_needed(self):
        if not (self.thread and self.thread.is_alive()):
            logger.debug("Starting Mic UI thread...")
            self._stop_event.clear()
            self.thread = threading.Thread(target=self._run_tk_app, daemon=True, name="MicUIThread")
            self.thread.start()
            time.sleep(0.1) # Allow thread to initialize

    def _run_tk_app(self):
        """Main function for the Tkinter thread."""
        logger.debug("Mic UI Tkinter thread started.")
        try:
            self.root = tk.Tk()
            self.root.title("Mic Status")
            self.root.attributes("-topmost", True)
            # Initial position, will be updated
            self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+0+0") 
            self.root.overrideredirect(True)
            self.root.attributes("-alpha", 0.8)
            self.root.configure(background=self.COLOR_BG)
            try: self.root.wm_attributes("-transparentcolor", self.COLOR_BG)
            except tk.TclError: logger.warning("Transparent color attribute not supported.")

            self.canvas = tk.Canvas(self.root, width=self.WIDTH, height=self.HEIGHT, bg=self.COLOR_BG, highlightthickness=0)
            self.canvas.pack()
            
            self.root.withdraw() # Start hidden
            self._is_ui_visible = False
            # self._draw_indicator() # No initial draw if hidden
            
            self.root.after(int(self._update_interval * 1000), self._check_and_update_ui)
            
            logger.debug("Mic UI Tkinter root created.")
            self.root.mainloop()

        except Exception as e:
            logger.error(f"Error in Mic UI Tkinter thread: {e}", exc_info=True)
        finally:
            logger.debug("Mic UI Tkinter mainloop finished.")
            self.canvas = None
            self.root = None

    def _check_and_update_ui(self):
        """Periodically called within Tk thread to request state check from main loop."""
        if not self.root: return # Stop if root is gone
        # Schedule the async state check in the main asyncio loop
        if self.main_loop and self.main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_update_state(), self.main_loop)
        # else: logger.warning("MicUI: Main loop not running for state update.")
             
        # Reschedule the check
        self.root.after(int(self._update_interval * 1000), self._check_and_update_ui)

    async def _async_update_state(self):
        """Runs in asyncio loop to get state from GVM and schedule UI redraw/reposition."""
        new_determined_state = "idle"
        should_be_visible = False
        current_pos = None

        try:
            is_pressed = await self.gvm.get(STATE_INPUT_DICTATION_KEY_PRESSED, False)
            activation_pos = await self.gvm.get("input.initial_activation_pos", None)

            if is_pressed:
                should_be_visible = True
                current_pos = activation_pos
                # Determine color state based on audio/STT status
                audio_status = await self.gvm.get(STATE_AUDIO_STATUS)
                if audio_status and 'error' in audio_status.lower():
                    new_determined_state = "error"
                else:
                    session_id = await self.gvm.get("app.current_stt_session_id")
                    if session_id:
                        stt_status_key = STATE_STT_SESSION_STATUS_TEMPLATE.format(session_id=session_id)
                        stt_status = await self.gvm.get(stt_status_key)
                        if stt_status == "connecting": new_determined_state = "connecting"
                        elif stt_status == "connected": new_determined_state = "active"
                        elif stt_status and 'error' in stt_status.lower(): new_determined_state = "error"
                        elif audio_status == "recording": new_determined_state = "active" # If key pressed and recording
                        else: new_determined_state = "active" # Default to active if pressed and no other state
                    elif audio_status == "recording": # Key pressed, no session yet, but recording started
                        new_determined_state = "active"
                    else: # Key pressed, but no session and not actively recording (e.g. pre-session buffer) - show as connecting/pending?
                        new_determined_state = "connecting" # Or "active" to indicate responsiveness
            else: # Not pressed
                should_be_visible = False
                new_determined_state = "idle"
                      
        except Exception as e:
             logger.error(f"MicUI: Error getting state from GVM: {e}")
             new_determined_state = "error" # Default to error if state check fails
             should_be_visible = True # Show error state

        state_changed = new_determined_state != self._current_state
        visibility_changed = should_be_visible != self._is_ui_visible

        if state_changed:
            self._current_state = new_determined_state
        
        if self.root:
            if visibility_changed:
                self._is_ui_visible = should_be_visible
                try:
                    if self._is_ui_visible:
                        logger.debug(f"MicUI: Showing UI. State: {self._current_state}, Pos: {current_pos}")
                        self.root.after(0, lambda: self._update_window_position_and_show(current_pos))
                        if state_changed: # Redraw only if state also changed or first time visible
                             self.root.after(10, self._draw_indicator) # Small delay for window to appear
                    else:
                        logger.debug("MicUI: Hiding UI.")
                        self.root.after(0, self.root.withdraw)
                except tk.TclError: pass # Ignore if window closed
            elif self._is_ui_visible and state_changed: # Visible and state changed, redraw
                logger.debug(f"MicUI: State changed while visible. New state: {self._current_state}. Redrawing.")
                try: self.root.after(0, self._draw_indicator)
                except tk.TclError: pass
            elif self._is_ui_visible and current_pos: # Visible and position might need update (though only on initial press now)
                # This part would be more relevant if mouse position was continuously updated
                # For now, position is set on show. We could add a check here if pos changed.
                pass 

    def _update_window_position_and_show(self, pos):
        """Updates window position and shows it. Runs in Tk thread."""
        if not self.root: return
        if pos and isinstance(pos, tuple) and len(pos) == 2:
            # Adjust position slightly so cursor isn't exactly on the top-left corner
            adj_x = pos[0] - self.WIDTH // 2
            adj_y = pos[1] - self.HEIGHT // 2 
            self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{adj_x}+{adj_y}")
        else: # Default position if no pos given (e.g. for error state without activation_pos)
            self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+50+50") 
        self.root.deiconify()
        self._draw_indicator() # Ensure it's drawn when shown
                 
    def _draw_indicator(self):
        """Draws the indicator circle based on self._current_state. Runs in Tk thread."""
        if not self.canvas: return

        self.canvas.delete("all") # Clear previous drawings
        
        fill_color = self.COLOR_IDLE
        if self._current_state == "active":
            fill_color = self.COLOR_ACTIVE
        elif self._current_state == "connecting":
            fill_color = self.COLOR_CONNECTING
        elif self._current_state == "error":
            fill_color = self.COLOR_ERROR
            
        # Draw outer circle (indicator)
        self.canvas.create_oval(
            self.CENTER_X - self.RADIUS,
            self.CENTER_Y - self.RADIUS,
            self.CENTER_X + self.RADIUS,
            self.CENTER_Y + self.RADIUS,
            fill=fill_color,
            outline=self.COLOR_OUTLINE, width=1
        )
        # logger.debug(f"MicUI Redrawn - State: {self._current_state}")

    async def run_loop(self):
        """Starts the UI thread and keeps the async task alive."""
        logger.info("MicUIManager run_loop starting.")
        self._start_ui_thread_if_needed()
        # The actual updates are handled by the _check_and_update_ui loop
        # This task just needs to keep running until cleanup
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(1) # Keep task alive
            except asyncio.CancelledError:
                 break 
        logger.info("MicUIManager run_loop finished.")

    async def cleanup(self):
        """Stops the UI thread and cleans up."""
        logger.info("MicUIManager cleaning up...")
        self._stop_event.set() # Signal check loop to stop (indirectly)
        if self.thread and self.thread.is_alive():
             logger.debug("Stopping Mic UI thread...")
             if self.root:
                  try: self.root.after(0, self.root.quit)
                  except Exception: pass
             self.thread.join(timeout=1.0)
             if self.thread.is_alive():
                  logger.warning("Mic UI thread did not stop gracefully.")
        self.thread = None
        self.root = None
        self.canvas = None
        logger.info("MicUIManager cleanup finished.")

# Note: Relies on GVM state keys like STATE_AUDIO_STATUS, STATE_STT_SESSION_STATUS_TEMPLATE,
# and potentially "app.current_stt_session_id" to determine the visual state.
# Assumes GVM provides get_main_loop().