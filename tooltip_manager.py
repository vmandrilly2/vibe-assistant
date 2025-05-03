import tkinter as tk
import threading
import queue
import logging
import pyautogui # Needed for FailSafeException

# --- Global Configurable Variables (Copied from vibe_app.py - TODO: Refactor to avoid duplication) ---
# These should ideally be passed during init or read from a shared config object/module
TOOLTIP_ALPHA = 0.85
TOOLTIP_BG = "lightyellow"
TOOLTIP_FG = "black"
TOOLTIP_FONT_FAMILY = "Arial"
TOOLTIP_FONT_SIZE = 10
# --- End Copied Globals ---

class TooltipManager:
    """Manages a simple Tkinter tooltip window in a separate thread."""
    # --- MODIFIED: Add transcription_active_event parameter --- >
    def __init__(self, q, transcription_active_event, initial_config):
        """
        Args:
            q: Queue for receiving commands.
            transcription_active_event: Event to check if transcription is active.
            initial_config: The initial config dictionary (or relevant tooltip section).
        """
        self.queue = q
        # --- Store the event --- >
        self.transcription_active_event = transcription_active_event
        self.root = None
        self.label = None
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event() # Signal when Tkinter root is ready
        self.active_tooltip_id = None # <<< NEW: Store the ID of the currently active tooltip
        # --- Store ConfigManager reference ---
        self.config_manager = initial_config # Rename initial_config to config_manager for clarity
        self._apply_tooltip_config() # Apply initial config using the manager

    def _apply_tooltip_config(self):
        """Applies tooltip config from the ConfigManager to internal variables."""
        if not self.config_manager:
            logging.error("TooltipManager: ConfigManager not available for applying config.")
            return
        # Read settings using the manager's get method
        self.alpha = float(self.config_manager.get("tooltip.alpha", 0.85))
        self.bg_color = str(self.config_manager.get("tooltip.bg_color", "lightyellow"))
        self.fg_color = str(self.config_manager.get("tooltip.fg_color", "black"))
        self.font_family = str(self.config_manager.get("tooltip.font_family", "Arial"))
        self.font_size = int(self.config_manager.get("tooltip.font_size", 10))
        logging.debug(f"Tooltip config applied: Alpha={self.alpha}, BG={self.bg_color}, FG={self.fg_color}")

    def reload_config(self, config_mgr): # Accepts the manager instance
        """Called when main config reloads to update tooltip appearance."""
        self.config_manager = config_mgr # Update manager reference if needed
        self._apply_tooltip_config() # Re-apply settings using the manager
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
                    # Unpack data including the activation ID
                    text, x, y, activation_id = data
                    self._update_tooltip(text, x, y, activation_id)
                elif command == "show":
                    # Get the activation ID
                    activation_id = data
                    self._show_tooltip(activation_id)
                elif command == "hide":
                    # Get the activation ID
                    activation_id = data # May be None for general hide
                    self._hide_tooltip(activation_id)
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

    def _update_tooltip(self, text, x, y, activation_id):
        # --- NEW: Check if transcription is still active --- >
        if not self.transcription_active_event.is_set():
            logging.debug("Tooltip update ignored: transcription_active_event is not set.")
            return
        # --- END NEW ---

        # Check if root exists and stop event isn't set
        if self.root and self.label and not self._stop_event.is_set():
            try:
                self.label.config(text=text)
                offset_x = 15
                offset_y = -30
                self.root.geometry(f"+{x + offset_x}+{y + offset_y}")
                # Store the ID associated with this visible tooltip
                self.active_tooltip_id = activation_id
            except tk.TclError as e:
                 logging.warning(f"Failed to update tooltip (window likely closed): {e}")
                 self._stop_event.set() # Stop if window is broken
            except pyautogui.FailSafeException: # Catch failsafe during update
                 logging.warning("PyAutoGUI fail-safe triggered during tooltip update.")
                 # Optionally trigger app stop?
                 self._stop_event.set() # Stop tooltip thread

    def _show_tooltip(self, activation_id):
        # --- NEW: Check if transcription is still active --- >
        if not self.transcription_active_event.is_set():
            logging.debug("Tooltip show ignored: transcription_active_event is not set.")
            return
        # --- END NEW ---

        if self.root and not self._stop_event.is_set():
            try:
                self.root.deiconify() # Show the window
                # Store the ID associated with this visible tooltip
                self.active_tooltip_id = activation_id
                logging.debug(f"Tooltip shown for activation ID: {activation_id}")
            except tk.TclError as e:
                 logging.warning(f"Failed to show tooltip (window likely closed): {e}")
                 self._stop_event.set()

    def _hide_tooltip(self, activation_id):
        # Only hide if not stopping AND the activation ID matches the currently shown tooltip
        # --- Allow hiding even if ID doesn't match if activation_id is None (general hide) ---
        if self.root and not self._stop_event.is_set():
            should_hide = activation_id is None or self.active_tooltip_id == activation_id
            if should_hide:
                try:
                    self.root.withdraw() # Hide the window
                    logging.debug(f"Tooltip hidden (requested ID: {activation_id}, current: {self.active_tooltip_id})")
                    self.active_tooltip_id = None # Clear the ID since it's hidden
                except tk.TclError as e:
                     logging.warning(f"Failed to hide tooltip (window likely closed): {e}")
                     self._stop_event.set()
            elif self.root.winfo_viewable(): # Only log if the tooltip is actually visible
                logging.debug(f"Ignored hide tooltip command for activation ID {activation_id} (current: {self.active_tooltip_id})") 