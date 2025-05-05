# interim_text_ui_manager.py
import asyncio
import logging
import tkinter as tk
from tkinter import ttk
import threading
from typing import Optional, Any
import time

# Assuming GVM access and constants
# from global_variables_manager import GlobalVariablesManager
from constants import (
    STATE_SESSION_INTERIM_TRANSCRIPT_TEMPLATE,
    STATE_INPUT_DICTATION_KEY_PRESSED # To know when to show/hide based on key press
)

logger = logging.getLogger(__name__)

class InterimTextUIManager:
    """Manages the tooltip-like UI for displaying interim transcription results."""

    def __init__(self, gvm: Any, main_loop: asyncio.AbstractEventLoop):
        logger.info("InterimTextUIManager initialized.")
        self.gvm = gvm
        self.main_loop = main_loop # Store the main loop
        self.root: Optional[tk.Tk] = None
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ui_visible = False
        self._text_label: Optional[ttk.Label] = None
        self._last_displayed_text: str = ""
        self._active_session_id: Optional[str] = None # Track which session we are showing
        self._monitor_task: Optional[asyncio.Task] = None # Task for monitoring a session
        self.alpha: float = 0.85
        self.bg_color: str = "lightyellow"
        self.fg_color: str = "black"

    async def init(self) -> bool:
        """Initializes the manager and loads UI config from GVM."""
        logger.info("InterimTextUIManager initializing...")
        try:
            self.alpha = float(await self.gvm.get("config.ui.tooltip_alpha", 0.85))
            self.bg_color = await self.gvm.get("config.ui.tooltip_bg", "lightyellow")
            self.fg_color = await self.gvm.get("config.ui.tooltip_fg", "black")
            logger.info("InterimTextUIManager initialized with UI config.")
            return True
        except Exception as e:
             logger.error(f"Failed to initialize InterimTextUIManager: {e}", exc_info=True)
             return False

    def _start_ui_thread_if_needed(self):
         """Starts the Tkinter thread if it's not already running."""
         if not (self.thread and self.thread.is_alive()):
             logger.debug("Starting Interim Text UI thread...")
             self._stop_event.clear()
             self.thread = threading.Thread(target=self._run_tk_app, daemon=True, name="InterimTextUIThread")
             self.thread.start()
             # Give thread a moment to initialize root
             time.sleep(0.1)
         else:
             logger.debug("Interim Text UI thread already running.")

    def _show_ui_thread_safe(self):
        """Schedules the UI to become visible in the Tkinter thread."""
        self._start_ui_thread_if_needed()
        if self.root:
            try:
                self.root.after(0, self._make_ui_visible)
            except tk.TclError:
                logger.debug("TclError scheduling UI show, window likely destroyed.")
        else:
            logger.warning("Cannot show Interim UI: Tkinter root not ready.")

    def _hide_ui_thread_safe(self):
         """Schedules the UI hiding in the Tkinter thread."""
         if self.root and self._ui_visible:
             try:
                 self.root.after(0, self._make_ui_invisible)
             except tk.TclError:
                 logger.debug("TclError scheduling UI hide, window likely destroyed.")
         else:
              self._ui_visible = False # Ensure state is correct even if root doesn't exist

    def _run_tk_app(self):
        """The main function for the Tkinter thread."""
        logger.debug("Interim Text UI Tkinter thread started.")
        try:
            self.root = tk.Tk()
            self.root.title("Interim Text")
            self.root.attributes("-topmost", True)
            self.root.geometry("+150+150")
            self.root.overrideredirect(True)
            self.root.attributes("-alpha", self.alpha)
            self.root.configure(background=self.bg_color)
            
            style = ttk.Style(self.root)
            style.configure('Interim.TLabel', background=self.bg_color, foreground=self.fg_color, padding=5, font=('Segoe UI', 10))
            
            self._text_label = ttk.Label(self.root, text="", style='Interim.TLabel', justify=tk.LEFT, anchor=tk.NW)
            self._text_label.pack(expand=True, fill='both')
            
            self.root.withdraw() # Start hidden
            self._ui_visible = False
            
            logger.debug("Interim Text UI Tkinter root created.")
            self.root.mainloop()

        except Exception as e:
            logger.error(f"Error in Interim Text UI Tkinter thread: {e}", exc_info=True)
        finally:
             logger.debug("Interim Text UI Tkinter mainloop finished.")
             self._ui_visible = False
             self._text_label = None
             self.root = None

    def _make_ui_visible(self):
        if self.root and not self._ui_visible:
            try:
                self.root.deiconify()
                self._ui_visible = True
                logger.debug("Interim Text UI made visible.")
            except tk.TclError as e:
                 logger.warning(f"Error making interim UI visible: {e}")

    def _make_ui_invisible(self):
        if self.root and self._ui_visible:
             try:
                 self.root.withdraw()
                 self._ui_visible = False
                 self._last_displayed_text = "" 
                 if self._text_label:
                      self._text_label.config(text="")
                 logger.debug("Interim Text UI hidden.")
             except tk.TclError as e:
                 logger.warning(f"Error hiding interim UI: {e}")

    def _update_text_thread_safe(self, text: str):
         if self.root and self._text_label:
             try:
                 # Use lambda to capture current text value for the scheduled call
                 self.root.after(0, lambda t=text: self._text_label.config(text=t) if self._text_label else None)
             except tk.TclError:
                 logger.debug("TclError requesting text update, window likely destroyed.")
         # else: logger.debug("Cannot update interim text, UI not ready.")

    async def _monitor_session(self, session_id: str):
        """Monitors the interim transcript for a specific session and updates UI."""
        self._active_session_id = session_id
        transcript_key = STATE_SESSION_INTERIM_TRANSCRIPT_TEMPLATE.format(session_id=session_id)
        logger.info(f"Starting to monitor interim transcript for session {session_id} (key: {transcript_key})")
        
        self._show_ui_thread_safe() # Request UI to show
        await asyncio.sleep(0.1) # Allow UI thread time

        while self._active_session_id == session_id:
            try:
                # Wait for changes or timeout slightly to check active status
                await asyncio.wait_for(self.gvm.wait_for_change(transcript_key), timeout=0.5) 
                
                if self._active_session_id != session_id: break # Check again after wait
                
                current_text = await self.gvm.get(transcript_key, "")
                if current_text != self._last_displayed_text:
                    self._last_displayed_text = current_text
                    self._update_text_thread_safe(current_text) # Update UI via Tk thread

            except asyncio.TimeoutError:
                 # Timeout just means no change, continue loop if still active
                 pass 
            except asyncio.CancelledError:
                logger.info(f"Interim text monitoring cancelled for session {session_id}.")
                break
            except Exception as e:
                logger.error(f"Error monitoring interim transcript for {session_id}: {e}", exc_info=True)
                await asyncio.sleep(1)
        
        logger.info(f"Stopped monitoring interim transcript for session {session_id}.")
        # Hide UI only if this was the session we were actively monitoring
        if self._active_session_id == session_id:
            self._active_session_id = None
            self._hide_ui_thread_safe() 
            
    async def run_loop(self):
        """Main loop watching for dictation key press/release to manage monitoring."""
        logger.info("InterimTextUIManager run_loop starting.")
        while True:
            try:
                # Wait for key press (dictation start)
                await self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, True)
                logger.debug("InterimTextUIManager detected key press - starting monitor.")
                
                # --- Find Active Session ID --- >
                # This is crucial and needs a reliable mechanism.
                # Possibility 1: STTManager sets "app.current_stt_session_id" in GVM when it starts.
                # Possibility 2: Find session with most recent start time without end time.
                # Using Possibility 1 as an example:
                current_session_id = await self.gvm.get("app.current_stt_session_id")
                # < --- End Find Active Session ID --- 

                if current_session_id:
                    if self._monitor_task and not self._monitor_task.done():
                        if self._active_session_id == current_session_id:
                             logger.debug("Already monitoring the correct session.")
                             continue # Skip starting new task if already monitoring this session
                        else:
                             logger.debug(f"New session ({current_session_id}) started while monitoring old ({self._active_session_id}). Cancelling old monitor.")
                             self._active_session_id = None # Mark old session inactive
                             self._monitor_task.cancel()
                             try: await self._monitor_task
                             except asyncio.CancelledError: pass
                    
                    # Start monitoring the new session
                    self._monitor_task = asyncio.create_task(self._monitor_session(current_session_id))
                else:
                     logger.warning("Dictation key pressed but could not determine active session ID from GVM state 'app.current_stt_session_id'.")

                # Now, wait for key release OR the monitoring task itself to end (e.g., if session ends early)
                key_release_task = asyncio.create_task(self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, False))
                tasks_to_wait_on = [key_release_task]
                if self._monitor_task and not self._monitor_task.done():
                     tasks_to_wait_on.append(self._monitor_task)
                     
                done, pending = await asyncio.wait(tasks_to_wait_on, return_when=asyncio.FIRST_COMPLETED)
                
                if key_release_task in done:
                     logger.debug("InterimTextUIManager detected key release.")
                     # Key released, signal the monitor task to stop and hide UI
                     self._active_session_id = None 
                     if self._monitor_task and not self._monitor_task.done():
                          # Task might finish naturally soon, but ensure hide is requested
                          self._hide_ui_thread_safe()
                else:
                     # Monitor task finished first (maybe session ended/errored)
                     logger.debug("Monitor task finished before key release.")
                     # UI hide should have been handled by _monitor_session
                
                # Cancel any remaining pending tasks (should only be key_release_task if monitor finished)
                for task in pending:
                     task.cancel()

            except asyncio.CancelledError:
                logger.info("InterimTextUIManager run_loop cancelled.")
                if self._monitor_task and not self._monitor_task.done():
                     self._active_session_id = None # Ensure monitor stops
                     self._monitor_task.cancel()
                break
            except Exception as e:
                logger.error(f"Error in InterimTextUIManager run_loop: {e}", exc_info=True)
                await asyncio.sleep(1)
                
        await self.cleanup()
        logger.info("InterimTextUIManager run_loop finished.")

    async def cleanup(self):
        """Stops the UI thread and cleans up."""
        logger.info("InterimTextUIManager cleaning up...")
        # Cancel monitor task if running
        if self._monitor_task and not self._monitor_task.done():
            self._active_session_id = None
            self._monitor_task.cancel()
            try: await self._monitor_task
            except asyncio.CancelledError: pass
             
        # Stop UI thread
        if self.thread and self.thread.is_alive():
             logger.debug("Stopping Interim Text UI thread...")
             self._stop_event.set()
             if self.root:
                  try: self.root.after(0, self._hide_ui)
                  except Exception: pass
             self.thread.join(timeout=1.0)
             if self.thread.is_alive():
                  logger.warning("Interim Text UI thread did not stop gracefully.")
        self.thread = None
        self.root = None
        self._text_label = None
        logger.info("InterimTextUIManager cleanup finished.")

# Note: Relies on a mechanism to determine the current session ID,
# e.g., GVM state "app.current_stt_session_id" set by STTManager.
# Assumes GVM has get_main_loop().
