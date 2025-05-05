# session_monitor_ui.py
import asyncio
import logging
import tkinter as tk
from tkinter import ttk
import threading
import time
from typing import Optional, Dict, Any

# Assuming GVM access and constants
# from global_variables_manager import GlobalVariablesManager
from constants import (
    STATE_STT_SESSION_STATUS_TEMPLATE,
    STATE_SESSION_INTERIM_TRANSCRIPT_TEMPLATE,
    STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE
    # Add other relevant session state keys if needed
)

logger = logging.getLogger(__name__)

class SessionMonitorUI:
    """Displays information about active STT sessions."""

    def __init__(self, gvm, main_loop: asyncio.AbstractEventLoop):
        logger.info("SessionMonitorUI initialized.")
        self.gvm = gvm
        self.main_loop = main_loop # Store the main loop
        self.root: Optional[tk.Tk] = None
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.session_widgets: Dict[str, Dict[str, tk.Widget]] = {} # session_id -> {widget_name: widget}
        self.main_frame: Optional[ttk.Frame] = None
        self._update_interval = 0.2 # How often to check GVM state (seconds)

    async def init(self) -> bool:
        logger.info("SessionMonitorUI initialized.")
        return True

    def _start_ui_thread_if_needed(self):
        if not (self.thread and self.thread.is_alive()):
            logger.debug("Starting Session Monitor UI thread...")
            self._stop_event.clear()
            self.thread = threading.Thread(target=self._run_tk_app, daemon=True, name="SessionMonitorUIThread")
            self.thread.start()
            time.sleep(0.1) # Allow thread to initialize

    def _run_tk_app(self):
        logger.debug("Session Monitor UI Tkinter thread started.")
        try:
            self.root = tk.Tk()
            self.root.title("Active Sessions")
            self.root.geometry("400x300+200+50") # Position placeholder
            # self.root.attributes("-topmost", True) # Optional

            # Styling
            style = ttk.Style(self.root)
            style.theme_use('clam')
            style.configure("Session.TFrame", padding=5, relief="groove", borderwidth=1)
            style.configure("Status.TLabel", padding=2, font=('Segoe UI', 9, 'bold'))
            style.configure("Transcript.TLabel", padding=2, wraplength=350, font=('Segoe UI', 9))

            self.main_frame = ttk.Frame(self.root, padding=10)
            self.main_frame.pack(expand=True, fill="both")

            # Schedule the first update check
            self.root.after(int(self._update_interval * 1000), self._check_and_update_ui)

            logger.debug("Session Monitor UI Tkinter root created.")
            self.root.mainloop()

        except Exception as e:
            logger.error(f"Error in Session Monitor UI Tkinter thread: {e}", exc_info=True)
        finally:
            logger.debug("Session Monitor UI Tkinter mainloop finished.")
            self.main_frame = None
            self.session_widgets.clear()
            self.root = None

    def _check_and_update_ui(self):
        """Periodically called within Tk thread to request state check from main loop."""
        if not self.root: return 
        if hasattr(self.gvm, 'get_main_loop'):
            main_loop = self.gvm.get_main_loop()
            if main_loop and main_loop.is_running():
                 asyncio.run_coroutine_threadsafe(self._async_update_sessions(), main_loop)
        else:
             logger.error("SessionMonitorUI: GVM has no get_main_loop method.")
             
        self.root.after(int(self._update_interval * 1000), self._check_and_update_ui)

    async def _async_update_sessions(self):
        """Runs in asyncio loop to get session data from GVM and schedule UI updates."""
        try:
            all_sessions_data = await self.gvm.get("sessions", {}) # Get the whole sessions dict
            if not isinstance(all_sessions_data, dict):
                 logger.warning("GVM state for 'sessions' is not a dictionary.")
                 all_sessions_data = {}
                 
            # Schedule UI update in Tkinter thread, passing the data
            if self.root and self.main_frame:
                 # Pass a copy to avoid thread issues if GVM state changes during processing
                 data_copy = dict(all_sessions_data) 
                 try: self.root.after(0, lambda d=data_copy: self._update_ui_widgets(d))
                 except tk.TclError: pass # Window might be closing

        except Exception as e:
             logger.error(f"SessionMonitorUI: Error getting session data from GVM: {e}")

    def _update_ui_widgets(self, sessions_data: Dict[str, Dict[str, Any]]):
        """Updates the session widgets in the UI. Runs in Tkinter thread."""
        if not self.main_frame: return
        
        current_ids = set(sessions_data.keys())
        displayed_ids = set(self.session_widgets.keys())

        # Remove widgets for sessions that no longer exist
        ids_to_remove = displayed_ids - current_ids
        for session_id in ids_to_remove:
            if session_id in self.session_widgets:
                widgets = self.session_widgets.pop(session_id)
                if "frame" in widgets and widgets["frame"]:
                    widgets["frame"].destroy()
                logger.debug(f"Removed UI for session: {session_id}")

        # Add/Update widgets for current sessions
        row_index = 0
        # Sort sessions by start time? Or just use dict order?
        sorted_ids = sorted(sessions_data.keys(), key=lambda sid: sessions_data[sid].get("start_time", 0), reverse=True)
        
        for session_id in sorted_ids:
            session_data = sessions_data[session_id]
            if not isinstance(session_data, dict): continue # Skip invalid data
            
            if session_id not in self.session_widgets:
                # Create new frame and widgets for this session
                frame = ttk.Frame(self.main_frame, style="Session.TFrame", borderwidth=1, relief="solid")
                # Make frame expand horizontally
                frame.grid(row=row_index, column=0, sticky="ew", pady=3, padx=3)
                self.main_frame.grid_columnconfigure(0, weight=1)
                
                status_label = ttk.Label(frame, text="Status: N/A", style="Status.TLabel")
                status_label.pack(anchor="nw")
                interim_label = ttk.Label(frame, text="Interim: ", style="Transcript.TLabel", justify=tk.LEFT)
                interim_label.pack(anchor="nw", fill="x")
                final_label = ttk.Label(frame, text="Final: ", style="Transcript.TLabel", justify=tk.LEFT)
                final_label.pack(anchor="nw", fill="x")
                
                self.session_widgets[session_id] = {
                     "frame": frame,
                     "status": status_label,
                     "interim": interim_label,
                     "final": final_label
                 }
                logger.debug(f"Created UI for session: {session_id}")
            else:
                 # Ensure frame is placed correctly if it already exists
                 frame = self.session_widgets[session_id]["frame"]
                 frame.grid(row=row_index, column=0, sticky="ew", pady=3, padx=3)

            # Update widget content
            widgets = self.session_widgets[session_id]
            status = session_data.get("status", "unknown") # Use actual status key if different
            interim = session_data.get("interim_transcript", "") # Use actual key
            final = session_data.get("final_transcript_segment", "") # Use actual key
            
            widgets["status"].config(text=f"Status: {status}")
            widgets["interim"].config(text=f"Interim: {interim}")
            widgets["final"].config(text=f"Final: {final}")
            
            # Update status color
            color = "black"
            if status == "connected": color = "green"
            elif status == "connecting": color = "orange"
            elif "error" in status: color = "red"
            elif status in ["disconnected", "closed_unexpectedly", "cancelled"]: color = "grey"
            widgets["status"].config(foreground=color)
            
            row_index += 1

    async def run_loop(self):
        """Starts the UI thread and keeps the async task alive."""
        logger.info("SessionMonitorUI run_loop starting.")
        self._start_ui_thread_if_needed()
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(1) # Keep alive, updates are scheduled by _check_and_update_ui
            except asyncio.CancelledError:
                 break 
        logger.info("SessionMonitorUI run_loop finished.")

    async def cleanup(self):
        """Stops the UI thread and cleans up."""
        logger.info("SessionMonitorUI cleaning up...")
        self._stop_event.set() # Signal check loop to stop
        if self.thread and self.thread.is_alive():
             logger.debug("Stopping Session Monitor UI thread...")
             if self.root:
                  try: self.root.after(0, self.root.quit)
                  except Exception: pass
             self.thread.join(timeout=1.0)
             if self.thread.is_alive():
                  logger.warning("Session Monitor UI thread did not stop gracefully.")
        self.thread = None
        self.root = None
        self.main_frame = None
        self.session_widgets.clear()
        logger.info("SessionMonitorUI cleanup finished.")

# Note: Assumes GVM state contains a top-level "sessions" dictionary
# where keys are session IDs and values are dictionaries containing session data.
# Requires GVM provides get_main_loop().