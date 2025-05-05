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
        self.last_known_pos = (0, 0) # Store the last position received
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
        try:
            # Check stop event AND enabled status from config manager
            module_enabled = self.config_manager.get("modules.tooltip_enabled", True)
            if self._stop_event.is_set() or not module_enabled:
                if not module_enabled and not self._stop_event.is_set():
                    logging.debug("TooltipManager: Module disabled via config, stopping checks and hiding.")
                    self._hide_tooltip() # Ensure tooltip is hidden if disabled
                # No cleanup needed if just disabled, only if stopped
                if self._stop_event.is_set():
                    self._cleanup_tk()
                # Schedule one last check in case it gets re-enabled or stopped
                if self.root and not self._stop_event.is_set():
                    self.root.after(500, self._check_queue) # Check less frequently when disabled
                return # Stop processing queue if stopped or disabled
        except Exception as e:
            logging.error(f"Error checking stop/enabled status in TooltipManager: {e}")
            self._stop_event.set() # Stop if error occurs here
            self._cleanup_tk()
            return

        # --- If enabled and not stopped, process queue ---
        needs_update = False
        try:
            while not self.queue.empty():
                command, data = self.queue.get_nowait()
                if command == "update":
                    text, x, y, activation_id = data
                    self.last_known_pos = (x, y)
                    # Only update if the ID matches the currently active tooltip
                    if activation_id == self.active_tooltip_id:
                        # If this is the first update for this ID and window is hidden, show it.
                        if self.root.state() == 'withdrawn':
                            self.root.deiconify()
                        self.label.config(text=text)
                        needs_update = True # Mark for geometry update
                    # If a new activation starts while tooltip is shown from previous,
                    # ignore updates for the old one.
                elif command == "show":
                    activation_id = data
                    # Store the ID, hide if currently showing a different one, reset text.
                    # Do NOT show the window here. Wait for the first update.
                    if activation_id != self.active_tooltip_id:
                        logging.debug(f"Tooltip activation ID set to: {activation_id}. Current: {self.active_tooltip_id}")
                        self.active_tooltip_id = activation_id
                        self.label.config(text="") # Clear text for new activation
                        if self.root.state() == 'normal': # If visible from previous ID
                            self.root.withdraw()
                            needs_update = False # No geometry update needed if hiding
                elif command == "hide":
                    activation_id = data
                    # Only hide if the request matches the currently active tooltip ID,
                    # or if the ID is None (e.g., from ESC key)
                    if activation_id is None or activation_id == self.active_tooltip_id:
                        self._hide_tooltip()
                        needs_update = False # Geometry update not needed after hiding
                    else:
                        logging.debug(f"Tooltip hide request ignored for ID: {activation_id} (Active: {self.active_tooltip_id})")
                elif command == "stop":
                    logging.debug("Received stop command in TooltipManager queue.")
                    self._stop_event.set()
                    # Cleanup will happen at the start of the next check
                elif command == "reload_config": # Handle explicit config reload signal
                    logging.debug("TooltipManager received reload_config signal.")
                    self.config_manager = data # Update internal reference
                    self._apply_tooltip_config() # Re-apply style settings
                    needs_update = True # Re-apply geometry potentially
        except queue.Empty:
            pass
        except tk.TclError as e:
            if "application has been destroyed" not in str(e):
                logging.warning(f"Tooltip Tkinter error processing queue: {e}.")
            self._stop_event.set() # Ensure cleanup happens
            # Cleanup happens at start of next loop or finally block
        except Exception as e:
            logging.error(f"Error processing TooltipManager queue: {e}", exc_info=True)
            self._stop_event.set() # Ensure cleanup happens

        if needs_update and self.root and self.root.winfo_exists() and self.root.state() == 'normal':
            try:
                # Use the last known position received from the queue
                self._update_position(self.last_known_pos[0], self.last_known_pos[1])
            except tk.TclError:
                pass # Ignore if root destroyed during update
            except Exception as e:
                logging.warning(f"Error updating tooltip position: {e}")

        # --- Reschedule Check --- >
        if self.root and not self._stop_event.is_set(): # Reschedule even if disabled, but less frequently
            check_interval_ms = 50 if module_enabled else 500
            self.root.after(check_interval_ms, self._check_queue)
        # No else needed, loop stops if root gone or stop event set

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

    def _update_position(self, x, y):
        """Updates the tooltip position based on provided coordinates."""
        if self.root and not self._stop_event.is_set():
            try:
                offset_x = 15  # Example offset
                offset_y = 10 # Adjusted offset
                # Ensures width/height are calculated based on current label content
                self.root.update_idletasks()
                new_x = x + offset_x
                new_y = y + offset_y
                # Add boundary checks if necessary (optional)
                # screen_width = self.root.winfo_screenwidth()
                # screen_height = self.root.winfo_screenheight()
                # tooltip_width = self.root.winfo_width()
                # tooltip_height = self.root.winfo_height()
                # if new_x + tooltip_width > screen_width: new_x = screen_width - tooltip_width
                # if new_y + tooltip_height > screen_height: new_y = screen_height - tooltip_height
                # if new_x < 0: new_x = 0
                # if new_y < 0: new_y = 0
                self.root.geometry(f"+{new_x}+{new_y}")
            except tk.TclError as e:
                logging.warning(f"Failed to update tooltip position (window likely closed): {e}")
                self._stop_event.set()

    def _hide_tooltip(self):
        # Hide whenever requested if the window is currently visible
        if self.root and not self._stop_event.is_set():
            should_hide = self.root.state() == 'normal'
            if should_hide:
                try:
                    self.root.withdraw() # Hide the window
                    logging.debug(f"Tooltip hidden (current active ID was: {self.active_tooltip_id})")
                    self.active_tooltip_id = None # Clear the ID since it's hidden
                except tk.TclError as e:
                     logging.warning(f"Failed to hide tooltip (window likely closed): {e}")
                     self._stop_event.set() 