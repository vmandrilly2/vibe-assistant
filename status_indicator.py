import tkinter as tk
import tkinter.font as tkFont
import threading
import queue
import logging
from functools import partial # Import partial for callbacks
import time # Need time for precise timestamps
import i18n # Import the module
from i18n import _ # Import the get_translation alias

# --- Constants Import --- (Assuming constants.py exists)
try:
    from constants import ALL_LANGUAGES, NATIVE_LANGUAGE_NAMES # Import language maps
except ImportError as e:
    logging.error(f"StatusIndicator failed to import constants: {e}. Using fallbacks.")
    # Fallback definitions
    ALL_LANGUAGES = {"en-US": "English (US)", "fr-FR": "French"}
    NATIVE_LANGUAGE_NAMES = {"en-US": "English (US)", "fr-FR": "Français"}

# --- Define Mode Constants (can be shared with vibe_app.py) ---
# It's slightly redundant defining them here and in vibe_app.py,
# but keeps the StatusIndicator self-contained regarding display names.
# Consider a shared constants file later if needed.
DEFAULT_MODES = {
    "Dictation": "Dictation Mode",
    "Command": "Command Mode"
    # Add "Command" later if desired
}

# --- Constants --- >
MAX_RECENT_LANG_DISPLAY = 3 # How many recent languages to show in popups
MAX_RECENT_TARGET_LANG_DISPLAY = 7
MAX_MODE_DISPLAY = 3 # Max modes to pre-create labels for (adjust if more modes)

class StatusIndicatorManager:
    """Manages a Tkinter status icon window (mode + mic icon + volume + languages)."""
    def __init__(self, q, action_q, config_manager, all_languages, all_languages_target, available_modes=None):
        self.queue = q
        self.action_queue = action_q
        # --- No need to pass translation function explicitly, just use imported _ --- >
        # --- Store config and language maps --- >
        self.config_manager = config_manager # Store the ConfigManager instance
        self.all_languages = all_languages
        self.all_languages_target = all_languages_target # Includes None
        # Use provided modes or default
        self.available_modes = available_modes if available_modes is not None else DEFAULT_MODES
        self.root = None
        self.canvas = None
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event()
        self.current_volume = 0.0 # Store current volume level (0.0 to 1.0)
        self.current_state = "hidden" # "hidden", "idle", "active"
        self.current_mode = list(self.available_modes.keys())[0] # Default to the first mode initially
        # --- Store the initial position received when activated ---
        self.initial_pos = None
        # --- Store last known mouse hover position for interaction checks ---
        self.last_hover_pos = (0, 0)
        self.source_lang = "" # Store current source language code
        self.target_lang = "" # Store current target language code (or None/empty)

        # --- Popups (Initialized to None, created in _run_tkinter) ---
        self.mode_popup = None
        self.source_popup = None
        self.target_popup = None
        # --- Store PRE-CREATED Label Widgets --- >
        self.mode_labels = []
        self.source_labels = []
        self.target_labels = []
        # --- Store Data Associated with Labels --- >
        self.label_data = {} # {widget_id: {"type": "mode"/"source"/"target", "value": mode_name/lang_code}}
        # --- Track currently hovered lang code --- > (Renamed for clarity)
        self.hovered_label_widget = None # Track the specific label widget being hovered
        self.hovered_data = None # {type: ..., value: ...} corresponding to hovered_label_widget
        # --- NEW: Connection Status --- >
        self.connection_status = "idle" # Changed initial state to 'idle'

        # Icon drawing properties
        self.icon_base_width = 24 # Original icon width
        self.icon_height = 36
        # Estimate width for mode text (can be adjusted)
        self.mode_text_width_estimate = 80
        # Increase text width estimate for languages
        self.lang_text_width_estimate = 120 # Increased estimate
        self.padding = 0 # No padding between elements
        # Update canvas width calculation
        self.canvas_width = (self.mode_text_width_estimate +
                             self.icon_base_width +
                             self.lang_text_width_estimate)

        # Colors
        self.mic_body_color = "#CCCCCC" # Light grey for mic body (Initial/Idle)
        self.mic_stand_color = "#AAAAAA" # Darker grey for stand
        # self.volume_fill_color = "#FF0000" # REMOVED - Now mode-dependent
        self.dictation_volume_color = "#FF0000" # Red for Dictation volume
        self.command_volume_color = "#0000FF" # Blue for Command volume (was keyboard)
        # --- NEW/UPDATED Colors --- >
        self.mic_connecting_color = "#FFD700" # Gold/Yellow for connecting
        self.mic_error_color = "#FF6347" # Tomato red for error state
        self.mic_connected_color = "#90EE90" # Light Green for successful connection
        # --- End NEW/UPDATED Colors --- >
        self.text_color = "#333333" # Color for language text
        self.inactive_text_color = "#AAAAAA" # Lighter gray for inactive target lang
        self.mode_text_color = "#333333" # Color for mode text
        self.bg_color = "#FEFEFE" # Use a near-white color for transparency key
        self.popup_bg = "#E0E0E0" # Background for popup
        self.popup_fg = "#000000"
        self.popup_highlight_bg = "#ADD8E6" # Light Blue for better visibility

        # Font object with increased size
        self.text_font_size = 10 # Keep size 10 for consistency?
        # --- Menus only enabled after hovering mic ---
        self.menus_enabled = False
        # --- Track if mic has been hovered since activation ---
        self.mic_hovered_since_activation = False

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

            self.canvas = tk.Canvas(self.root, width=self.canvas_width, height=self.icon_height,
                                    bg=self.bg_color, highlightthickness=0)
            self.canvas.pack()

            # --- PRE-CREATE POPUPS and LABELS --- >
            self._initialize_popups_and_labels()
            # --- End Pre-create ---

            self._tk_ready.set()
            logging.debug("StatusIndicator Tkinter objects created (including hidden popups/labels).")
            self._check_queue() # Start the queue check / redraw loop
            self.root.mainloop()
            logging.debug("StatusIndicator mainloop finished.")

        except Exception as e:
            logging.error(f"Error during StatusIndicator mainloop/setup: {e}", exc_info=True)
            self._tk_ready.set() # Signal ready even on error
        finally:
            logging.info("StatusIndicator thread finishing.")
            # Ensure popups are destroyed properly in cleanup
            self._destroy_popups() # Call helper to destroy all
            self._stop_event.set()

    # --- MODIFIED: Initialize Popups AND Labels ---
    def _initialize_popups_and_labels(self):
        """Creates the Toplevel popup windows and the label widgets within them, initially hidden."""
        logging.debug("Initializing hidden popups and labels...")
        try:
            # --- Mode Popup --- >
            self.mode_popup = tk.Toplevel(self.root)
            self.mode_popup.overrideredirect(True); self.mode_popup.wm_attributes("-topmost", True)
            self.mode_popup.config(bg=self.popup_bg, relief=tk.SOLID, borderwidth=0)
            self.mode_labels = []
            for i in range(MAX_MODE_DISPLAY): # Create max number of labels needed
                label = tk.Label(self.mode_popup, text="", font=("Segoe UI", 10), bg=self.popup_bg, fg=self.popup_fg, padx=5, pady=2, anchor=tk.W)
                # Bind a generic callback
                label.bind("<ButtonRelease-1>", self._on_popup_label_release)
                self.mode_labels.append(label)
            self._update_mode_popup_content() # Populate initial content and pack visible labels
            self.mode_popup.withdraw() # Hide the popup window

            # --- Source Language Popup --- >
            self.source_popup = tk.Toplevel(self.root)
            self.source_popup.overrideredirect(True); self.source_popup.wm_attributes("-topmost", True)
            self.source_popup.config(bg=self.popup_bg, relief=tk.SOLID, borderwidth=0)
            self.source_labels = []
            for i in range(MAX_RECENT_LANG_DISPLAY):
                label = tk.Label(self.source_popup, text="", font=("Segoe UI", 10), bg=self.popup_bg, fg=self.popup_fg, padx=5, pady=2, anchor=tk.W)
                label.bind("<ButtonRelease-1>", self._on_popup_label_release)
                self.source_labels.append(label)
            self._update_lang_popup_content("source")
            self.source_popup.withdraw()

            # --- Target Language Popup --- >
            self.target_popup = tk.Toplevel(self.root)
            self.target_popup.overrideredirect(True); self.target_popup.wm_attributes("-topmost", True)
            self.target_popup.config(bg=self.popup_bg, relief=tk.SOLID, borderwidth=0)
            self.target_labels = []
            # Need 1 extra label for "None" option
            for i in range(MAX_RECENT_TARGET_LANG_DISPLAY + 1):
                label = tk.Label(self.target_popup, text="", font=("Segoe UI", 10), bg=self.popup_bg, fg=self.popup_fg, padx=5, pady=2, anchor=tk.W)
                label.bind("<ButtonRelease-1>", self._on_popup_label_release)
                self.target_labels.append(label)
            self._update_lang_popup_content("target")
            self.target_popup.withdraw()

            logging.debug("Popups and labels initialized successfully.")
        except Exception as e:
            logging.error(f"Error initializing popups/labels: {e}", exc_info=True)
            # Attempt to destroy any partially created popups
            self._destroy_popups()

    # --- MODIFIED: Update Popup Content (Updates existing labels) ---
    def _update_mode_popup_content(self):
        """Updates the text and associated data of existing mode labels."""
        if not self.mode_popup or not self.mode_popup.winfo_exists():
            logging.warning("Attempted to update content of non-existent mode popup.")
            return

        # Filter out the current mode before displaying options
        current_mode_name = self.current_mode
        modes_to_display = [
            (mode_name, display_name)
            # --- Use translated display names --- >
            for mode_name, display_name in self.available_modes.items()
            if mode_name != current_mode_name
        ]

        # Clear old data for labels associated with *this* popup type
        keys_to_remove = [k for k, v in self.label_data.items() if v.get("popup") == self.mode_popup]
        for key in keys_to_remove:
            if key in self.label_data: del self.label_data[key]

        for i, label in enumerate(self.mode_labels):
            if i < len(modes_to_display):
                mode_name, display_name = modes_to_display[i]
                # --- Use translation --- >
                translated_name = _(f"mode_names.{mode_name}", default=display_name)
                label.config(text=translated_name)
                # Store data associated with this label's ID
                self.label_data[label.winfo_id()] = {"popup": self.mode_popup, "type": "mode", "value": mode_name}
                # Ensure label is packed/visible
                if not label.winfo_ismapped():
                     label.pack(fill=tk.X)
            else:
                # Hide unused labels
                label.pack_forget()
        # logging.debug("Mode popup content updated.")

    def _update_lang_popup_content(self, lang_type):
        """Updates the text and associated data of existing language labels."""
        popup = self.source_popup if lang_type == "source" else self.target_popup
        labels = self.source_labels if lang_type == "source" else self.target_labels

        if not popup or not popup.winfo_exists():
            logging.warning(f"Attempted to update content of non-existent {lang_type} popup.")
            return

        # --- Determine which languages to show --- >
        langs_to_display = [] # List of (lang_code, display_name)
        if lang_type == "source":
            # Get current source language to exclude it from the popup list
            current_source_lang = self.source_lang # Access the instance variable
            # Ensure recent list exists and is a list
            recent_source_languages_raw = self.config_manager.get("general.recent_source_languages", [])
            if not isinstance(recent_source_languages_raw, list):
                 logging.warning("StatusIndicator: recent_source_languages is not a list, using empty.")
                 recent_source_languages_raw = []
            for code in recent_source_languages_raw:
                # Add only if it exists in the full list AND is not the currently selected one
                if code in self.all_languages and code != current_source_lang:
                    # --- Use NATIVE name for source --- >
                    english_name = ALL_LANGUAGES.get(code, code) # Fallback to English/code
                    native_name = NATIVE_LANGUAGE_NAMES.get(code, english_name) # Fallback to Native/English/code
                    langs_to_display.append((code, native_name))
        else: # Target language
            # Add "None" first
            # --- Translate "None" --- >
            default_none_name = self.all_languages_target.get(None, "None")
            langs_to_display.append((None, _(f"language_names.none", default=default_none_name)))
            # Ensure recent list exists and is a list
            recent_codes = self.config_manager.get("general.recent_target_languages", [])[:MAX_RECENT_TARGET_LANG_DISPLAY]
            if not isinstance(recent_codes, list):
                 logging.warning("StatusIndicator: recent_target_languages is not a list, using empty.")
            for code in recent_codes:
                 if code is not None and code in self.all_languages_target:
                     # --- Translate name --- >
                     default_name = self.all_languages_target.get(code, code) # Fallback to code
                     langs_to_display.append((code, _(f"language_names.{code}", default=default_name)))

        # Clear old data for labels associated with *this* popup type
        keys_to_remove = [k for k, v in self.label_data.items() if v.get("popup") == popup]
        for key in keys_to_remove:
            if key in self.label_data: del self.label_data[key]

        # --- Update existing labels --- >
        for i, label in enumerate(labels):
             if i < len(langs_to_display):
                 lang_code, display_name = langs_to_display[i]
                 label.config(text=display_name)
                 # Store data associated with this label's ID
                 self.label_data[label.winfo_id()] = {"popup": popup, "type": lang_type, "value": lang_code}
                 # Ensure label is packed/visible
                 if not label.winfo_ismapped():
                      label.pack(fill=tk.X)
             else:
                 # Hide unused labels
                 label.pack_forget()
        # logging.debug(f"{lang_type} popup content updated.")

    # --- Main Check Queue Loop (Largely similar, calls different functions) ---
    def _check_queue(self):
        if self._stop_event.is_set():
            self._cleanup_tk() # Calls _destroy_popups
            return
        needs_redraw = False
        position_needs_update = False
        target_state = self.current_state
        try:
            while not self.queue.empty():
                command, data = self.queue.get_nowait()
                if command == "volume":
                    new_volume = data
                    if abs(new_volume - self.current_volume) > 0.02:
                        self.current_volume = new_volume
                        if self.current_state == "active":
                            needs_redraw = True
                elif command == "state":
                    target_state = data.get("state", "hidden")
                    pos = data.get("pos")
                    rcvd_source_lang = data.get("source_lang", "")
                    rcvd_target_lang = data.get("target_lang", None)
                    rcvd_mode = data.get("mode", self.current_mode)
                    # --- NEW: Process connection_status if included in state message --- >
                    rcvd_conn_status = data.get("connection_status")
                    # --- END NEW --- >
                    # Handle showing/hiding main indicator
                    if target_state != "hidden" and self.current_state == "hidden":
                        if pos:
                            self.initial_pos = pos; position_needs_update = True
                            self.menus_enabled = False; self.mic_hovered_since_activation = False
                        else: logging.warning("StatusIndicator activated but no initial position!")
                    elif target_state == "hidden":
                        self.initial_pos = None; self.menus_enabled = False; self.mic_hovered_since_activation = False
                        self._hide_all_popups() # Hide popups when main indicator hides
                    # Update language/mode state
                    lang_changed = rcvd_source_lang != self.source_lang or rcvd_target_lang != self.target_lang
                    mode_changed = rcvd_mode != self.current_mode
                    if lang_changed: self.source_lang = rcvd_source_lang; self.target_lang = rcvd_target_lang
                    if mode_changed: self.current_mode = rcvd_mode
                    # --- NEW: Update internal connection status if provided in state message --- >
                    conn_status_changed = False
                    if rcvd_conn_status is not None and rcvd_conn_status != self.connection_status:
                        if rcvd_conn_status in ["idle", "connecting", "connected", "error"]:
                            self.connection_status = rcvd_conn_status
                            conn_status_changed = True
                            logging.debug(f"StatusIndicator connection status updated via state cmd: {self.connection_status}")
                        else:
                            logging.warning(f"Received unknown connection status in state cmd: {rcvd_conn_status}")
                    # --- END NEW --- >
                    if (lang_changed or mode_changed or conn_status_changed) and self.current_state != "hidden": needs_redraw = True
                    # Handle state change
                    if target_state != self.current_state:
                        logging.debug(f"StatusIndicator state change: {self.current_state} -> {target_state}, Mode: {self.current_mode}")
                        needs_redraw = True
                        if target_state == "active": self.current_volume = 0.0
                        # Hiding handled above
                # --- NEW: Handle Connection Status Update --- >
                elif command == "connection_update":
                    new_status = data.get("status", "idle") # Default to idle if not specified
                    # --- MODIFIED: Accept only simplified states ---
                    # Accept "idle", "connecting", "connected", "error"
                    if new_status in ["idle", "connecting", "connected", "error"]:
                        if new_status != self.connection_status:
                            self.connection_status = new_status
                            logging.debug(f"StatusIndicator connection status updated: {self.connection_status}")
                            # Redraw needed only if the indicator is currently visible
                            if self.current_state != "hidden":
                                needs_redraw = True
                    else:
                        logging.warning(f"Received unknown connection status: {new_status}")
                elif command == "selection_made":
                    logging.debug(f"StatusIndicator received selection_made: {data}")
                    self._blink_and_hide(data) # Handles hiding popups and main window
                    target_state = "hidden"; needs_redraw = False; state_actually_changed = False # Prevent normal updates during blink
                elif command == "stop":
                    logging.debug("Received stop command in StatusIndicator queue."); self._stop_event.set()
        except queue.Empty: pass
        except tk.TclError as e:
            if not self._stop_event.is_set(): logging.warning(f"StatusIndicator Tkinter error processing queue: {e}.")
            self._stop_event.set(); self._cleanup_tk(); return
        except Exception as e: logging.error(f"Error processing StatusIndicator queue: {e}", exc_info=True)

        # --- Get current mouse position directly within Tkinter thread --- >
        mx, my = (0, 0) # Default if root doesn't exist
        if self.root and self.root.winfo_exists():
            try:
                mx, my = self.root.winfo_pointerxy()
            except tk.TclError:
                pass # Ignore if window doesn't exist
        # --- Store position for potential use by other methods if needed ---
        self.last_hover_pos = (mx, my)

        state_actually_changed = (target_state != self.current_state)
        if state_actually_changed: self.current_state = target_state

        # --- Check if menus need enabling (based on mic hover) ---
        menus_were_just_enabled = False
        if self.current_state != "hidden" and not self.mic_hovered_since_activation:
            mx, my = self.last_hover_pos
            mic_hover = self._is_point_over_mic(mx, my)
            if mic_hover:
                 self.mic_hovered_since_activation = True; self.menus_enabled = True
                 menus_were_just_enabled = True; logging.debug("Mic hovered: menus enabled.")

        # --- Apply Changes and Redraw ---
        if (needs_redraw or state_actually_changed or menus_were_just_enabled) and self.root and not self._stop_event.is_set():
             if position_needs_update and self.initial_pos: self._position_window(self.initial_pos)
             self._draw_icon()
             if self.current_state == "hidden": self.root.withdraw()
             else: self.root.deiconify()
             # Immediately check hover if menus just enabled
             if menus_were_just_enabled:
                 mx, my = self.last_hover_pos
                 self._check_hover_and_manage_popups(mx, my)

        # --- Check Popups and Hovered Label ---
        self.hovered_label_widget = None # Reset hover state each cycle
        self.hovered_data = None
        if self.root and not self._stop_event.is_set() and self.current_state != "hidden":
             try:
                 mx, my = self.last_hover_pos
                 if self.menus_enabled:
                     # Manage popups (show/hide based on hover)
                     self._check_hover_and_manage_popups(mx, my)

                     # Check hover over labels in visible popups
                     newly_hovered_widget = None
                     is_mouse_over_interactive_area = ( # Check main indicator areas
                         self._is_point_over_mic(mx, my) or
                         self._is_point_over_tag(mx, my, "mode_area") or
                         self._is_point_over_tag(mx, my, "source_lang_area") or
                         self._is_point_over_tag(mx, my, "arrow_area") or
                         self._is_point_over_tag(mx, my, "target_lang_area")
                     )
                     # Check visible popups and their labels
                     # Iterate through all pre-created labels, checking if their parent popup is visible
                     all_labels = self.mode_labels + self.source_labels + self.target_labels
                     for label in all_labels:
                         if not label.winfo_ismapped(): continue # Skip hidden labels
                         parent_popup = label.master
                         if parent_popup.state() == 'normal': # Is the parent popup visible?
                             if self._is_point_over_widget(mx, my, parent_popup):
                                 is_mouse_over_interactive_area = True # Hovering visible popup is interactive
                                 if self._is_point_over_widget(mx, my, label):
                                     newly_hovered_widget = label
                                     break # Found the specific label
                     # Update global hover state if a label is hovered
                     if newly_hovered_widget:
                         widget_id = newly_hovered_widget.winfo_id()
                         if widget_id in self.label_data:
                             self.hovered_label_widget = newly_hovered_widget
                             self.hovered_data = self.label_data[widget_id]

                 # --- Update Label Highlighting (if menus enabled) ---
                 if self.menus_enabled:
                     # This logic needs to compare the currently hovered widget with the previous one
                     # It seems complex to track previous hover state accurately this way.
                     # Let's simplify: just highlight the currently hovered one, unhighlight others.
                     currently_hovered_id = self.hovered_label_widget.winfo_id() if self.hovered_label_widget else None
                     for label in self.mode_labels + self.source_labels + self.target_labels:
                         if not label.winfo_exists(): continue
                         try:
                             if label.winfo_id() == currently_hovered_id:
                                 if label.cget('bg') != self.popup_highlight_bg:
                                     label.config(bg=self.popup_highlight_bg)
                             else:
                                 if label.cget('bg') != self.popup_bg:
                                     label.config(bg=self.popup_bg)
                         except tk.TclError: pass # Ignore errors if widget destroyed during check

                 # --- Disable Menus if Mouse Left Interactive Area --- >
                 is_mouse_over_any_visible_popup = False
                 for popup in [self.mode_popup, self.source_popup, self.target_popup]:
                     if popup and popup.winfo_exists() and popup.state() == 'normal':
                          if self._is_point_over_widget(mx, my, popup):
                              is_mouse_over_any_visible_popup = True; break

                 if self.menus_enabled and not is_mouse_over_interactive_area and not is_mouse_over_any_visible_popup:
                     logging.debug("Mouse left interactive area. Disabling menus and hiding popups.")
                     self.menus_enabled = False; self.mic_hovered_since_activation = False
                     needs_redraw = True # Redraw indicator without text areas
                     self._hide_all_popups()
                     # Unhighlight any potentially highlighted label immediately
                     if self.hovered_label_widget and self.hovered_label_widget.winfo_exists():
                         try: self.hovered_label_widget.config(bg=self.popup_bg)
                         except tk.TclError: pass
                     self.hovered_label_widget = None; self.hovered_data = None

             except tk.TclError: pass # Ignore transient errors
             except Exception as e: logging.error(f"Error during hover/popup check: {e}", exc_info=True)

        # --- Reschedule ---
        if not self._stop_event.is_set() and self.root:
             try: self.root.after(25, self._check_queue)
             except tk.TclError: logging.warning("StatusIndicator root destroyed before rescheduling.")
             except Exception as e: logging.error(f"Error rescheduling StatusIndicator check: {e}")

    # --- MODIFIED: Cleanup TK - calls _destroy_popups ---
    def _cleanup_tk(self):
        logging.debug("Executing StatusIndicator _cleanup_tk.")
        # Destroy popups first
        self._destroy_popups()
        # Then destroy the main root window
        if self.root:
            try:
                self.root.destroy()
                logging.info("StatusIndicator root window destroyed.")
                self.root = None
            except Exception as e: logging.warning(f"Error destroying StatusIndicator root: {e}")

    # --- NEW: Destroy Popups ---
    def _destroy_popups(self):
        """Destroys all popup windows if they exist."""
        logging.debug("Destroying all popups...")
        for popup in [self.mode_popup, self.source_popup, self.target_popup]:
            if popup and popup.winfo_exists():
                try: popup.destroy()
                except Exception as e: logging.warning(f"Error destroying a popup: {e}")
        self.mode_popup, self.source_popup, self.target_popup = None, None, None
        # Clear label references and data
        self.mode_labels.clear(); self.source_labels.clear(); self.target_labels.clear()
        self.label_data.clear()
        logging.debug("Popups destroyed.")

    def _position_window(self, pos):
        """Positions the main indicator window based on the given initial position."""
        if self.root and not self._stop_event.is_set() and pos:
            try:
                x, y = pos
                offset_x = -85; offset_y = 10
                new_x = x + offset_x; new_y = y + offset_y
                screen_width = self.root.winfo_screenwidth()
                if new_x + self.canvas_width > screen_width: new_x = screen_width - self.canvas_width
                if new_x < 0: new_x = 0
                if new_y < 0: new_y = 0
                self.root.geometry(f"+{new_x}+{new_y}")
            except Exception as e: logging.warning(f"Failed to position StatusIndicator: {e}")
        elif not pos: logging.warning("[_position_window] Called without a valid position.")

    def _draw_icon(self):
        """Draws the microphone icon and language text with separate backgrounds."""
        if not self.canvas or not self.root or self._stop_event.is_set(): return
        try:
            self.canvas.delete("all")
            if self.current_state == "hidden": return

            text_y = self.icon_height / 2; text_bg_color = "#FFFFFF"; text_padding_x = 3; text_padding_y = 2
            bg_y0 = text_y - (self.text_font_size / 1.5) - text_padding_y
            bg_y1 = text_y + (self.text_font_size / 1.5) + text_padding_y
            icon_x_offset = self.mode_text_width_estimate
            draw_text_areas = self.menus_enabled

            # Mode Text
            if draw_text_areas:
                # --- Use translated mode name --- >
                mode_text = self.current_mode
                translated_mode_text = _(f"mode_names.{mode_text}", default=mode_text)
                self.canvas.create_rectangle(0, bg_y0, self.mode_text_width_estimate, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("mode_area",))
                text_x = 0 + text_padding_x
                self.canvas.create_text(text_x, text_y, text=translated_mode_text, anchor=tk.W, font=("Segoe UI", 10), fill=self.mode_text_color, tags=("mode_area",))

            # Mic Icon
            w, h = self.icon_base_width, self.icon_height
            body_w = w * 0.6; body_h = h * 0.6; body_x = icon_x_offset + (w - body_w) / 2; body_y = h * 0.1
            stand_h = h * 0.2; stand_y = body_y + body_h; stand_w = w * 0.2; stand_x = icon_x_offset + (w - stand_w) / 2
            base_h = h * 0.1; base_y = stand_y + stand_h; base_w = w * 0.8; base_x = icon_x_offset + (w - base_w) / 2
            self.canvas.create_rectangle(stand_x, stand_y, stand_x + stand_w, stand_y + stand_h, fill=self.mic_stand_color, outline="")
            self.canvas.create_rectangle(base_x, base_y, base_x + base_w, base_y + base_h, fill=self.mic_stand_color, outline="")
            # --- Determine Mic Body Color based on connection_status --- >
            if self.connection_status == "connected":
                current_mic_body_color = self.mic_connected_color # Green for connected
            elif self.connection_status == "connecting":
                current_mic_body_color = self.mic_connecting_color # Yellow for connecting
            elif self.connection_status == "error":
                current_mic_body_color = self.mic_error_color # Red for final error
            else: # Includes "idle"
                current_mic_body_color = self.mic_body_color # Default grey
            mic_body = self.canvas.create_rectangle(body_x, body_y, body_x + body_w, body_y + body_h, fill=current_mic_body_color, outline=self.mic_stand_color)
            # --- End Mic Body --- >

            # Volume Indicator
            # --- MODIFIED: Only draw volume if successfully connected ('connected') --- >
            if self.current_state == "active" and self.connection_status == "connected":
                 volume_color = self.dictation_volume_color
                 if self.current_mode == "Command": volume_color = self.command_volume_color
                 fill_h = body_h * self.current_volume; fill_y = body_y + body_h - fill_h
                 if fill_h > 0:
                    vol_x0 = body_x; vol_y0 = max(body_y, fill_y); vol_x1 = body_x + body_w; vol_y1 = body_y + body_h
                    if vol_y0 < vol_y1: self.canvas.create_rectangle(vol_x0, vol_y0, vol_x1, vol_y1, fill=volume_color, outline="")

            # Language Text
            if draw_text_areas and self.source_lang:
                current_x = icon_x_offset + self.icon_base_width
                # --- Use NATIVE name for source --- >
                english_name_src = ALL_LANGUAGES.get(self.source_lang, self.source_lang) # Fallback
                src_text = NATIVE_LANGUAGE_NAMES.get(self.source_lang, english_name_src) # Native name
                # --- END MODIFIED --- >
                src_width = tkFont.Font(family="Segoe UI", size=self.text_font_size).measure(src_text)
                src_bg_x0 = current_x; src_bg_x1 = current_x + src_width + text_padding_x * 2
                self.canvas.create_rectangle(src_bg_x0, bg_y0, src_bg_x1, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("source_lang_area",))
                self.canvas.create_text(current_x + text_padding_x, text_y, text=src_text, anchor=tk.W, font=("Segoe UI", 10), fill=self.text_color, tags=("source_lang_area",))
                current_x = src_bg_x1

                arrow_text = ">"; arrow_width = tkFont.Font(family="Segoe UI", size=self.text_font_size).measure(arrow_text)
                arrow_x = current_x
                self.canvas.create_text(arrow_x, text_y, text=arrow_text, anchor=tk.W, font=("Segoe UI", 10), fill=self.text_color, tags=("arrow_area",))
                current_x = arrow_x + arrow_width

                is_target_active = self.target_lang and self.target_lang != self.source_lang
                # --- Translate target lang name (or None) --- >
                target_key = self.target_lang if is_target_active else "none"
                default_tgt_name = self.all_languages_target.get(self.target_lang, self.target_lang) if is_target_active else self.all_languages_target.get(None, "None")
                # If target_key is None, i18n might not find it directly, use "none" key explicitly
                tgt_text_key = f"language_names.{target_key if target_key is not None else 'none'}"
                tgt_text = _(tgt_text_key, default=default_tgt_name)
                # --- END MODIFIED --- >
                tgt_color = self.text_color if is_target_active else self.inactive_text_color
                tgt_width = tkFont.Font(family="Segoe UI", size=self.text_font_size).measure(tgt_text)
                tgt_bg_x0 = current_x; tgt_bg_x1 = current_x + tgt_width + text_padding_x * 2
                self.canvas.create_rectangle(tgt_bg_x0, bg_y0, tgt_bg_x1, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("target_lang_area",))
                self.canvas.create_text(current_x + text_padding_x, text_y, text=tgt_text, anchor=tk.W, font=("Segoe UI", 10), fill=tgt_color, tags=("target_lang_area",))

        except tk.TclError as e: logging.warning(f"Error drawing status icon: {e}"); self._stop_event.set()
        except Exception as e: logging.error(f"Unexpected error drawing status icon: {e}", exc_info=True)

    # --- MODIFIED: Show and Update Popups ---
    def _show_and_update_mode_popup(self):
        """Updates content, positions, and shows the pre-created mode popup."""
        if not self.mode_popup or not self.mode_popup.winfo_exists():
            logging.error("Mode popup does not exist, cannot show/update.")
            return
        # Ensure other popups are hidden first
        self._hide_lang_popup("source")
        self._hide_lang_popup("target")
        # logging.debug("Showing and updating mode popup...")
        # Update content of existing labels
        self._update_mode_popup_content()
        # Positioning Logic
        try:
            tag_name = "mode_area"
            bbox_rel = self.canvas.bbox(tag_name)
            if not bbox_rel: logging.warning(f"Cannot position mode popup, tag '{tag_name}' not found."); return
            self.mode_popup.update_idletasks(); self.canvas.update_idletasks()
            canvas_x = self.canvas.winfo_rootx(); canvas_y = self.canvas.winfo_rooty()
            bbox_abs_x0 = canvas_x + bbox_rel[0]; bbox_abs_y0 = canvas_y + bbox_rel[1]
            popup_height = self.mode_popup.winfo_reqheight(); popup_width = self.mode_popup.winfo_reqwidth()
            popup_x = bbox_abs_x0; popup_y = bbox_abs_y0 - popup_height
            screen_width = self.root.winfo_screenwidth(); screen_height = self.root.winfo_screenheight()
            if popup_x < 0: popup_x = 0
            if popup_x + popup_width > screen_width: popup_x = screen_width - popup_width
            if popup_y < 0: popup_y = canvas_y + bbox_rel[3]
            if popup_y + popup_height > screen_height: popup_y = screen_height - popup_height
            self.mode_popup.geometry(f"+{popup_x}+{popup_y}")
            self.mode_popup.deiconify() # Show the window
        except tk.TclError as e: logging.warning(f"TclError positioning/showing mode popup: {e}")
        except Exception as e: logging.error(f"Error showing/updating mode popup: {e}", exc_info=True)

    def _show_and_update_lang_popup(self, lang_type):
        """Updates content, positions, and shows the pre-created language popup."""
        if not self.mode_popup or not self.mode_popup.winfo_exists():
            logging.error("Mode popup does not exist, cannot show/update.")
            return
        # Ensure other popups are hidden first
        self._hide_mode_popup()
        self._hide_lang_popup("source" if lang_type == "target" else "target")
        # logging.debug(f"Showing and updating {lang_type} popup...")
        # Update content of existing labels
        self._update_lang_popup_content(lang_type)
        # Positioning Logic
        try:
            tag_name = "source_lang_area" if lang_type == "source" else "target_lang_area"
            bbox_rel = self.canvas.bbox(tag_name)
            if not bbox_rel: logging.warning(f"Cannot position {lang_type} popup, tag '{tag_name}' not found."); return
            popup = self.source_popup if lang_type == "source" else self.target_popup
            popup.update_idletasks(); self.canvas.update_idletasks()
            canvas_x = self.canvas.winfo_rootx(); canvas_y = self.canvas.winfo_rooty()
            bbox_abs_x0 = canvas_x + bbox_rel[0]; bbox_abs_y0 = canvas_y + bbox_rel[1]
            popup_height = popup.winfo_reqheight(); popup_width = popup.winfo_reqwidth()
            popup_x = bbox_abs_x0; popup_y = bbox_abs_y0 - popup_height
            screen_width = self.root.winfo_screenwidth(); screen_height = self.root.winfo_screenheight()
            if popup_x < 0: popup_x = 0
            if popup_x + popup_width > screen_width: popup_x = screen_width - popup_width
            if popup_y < 0: popup_y = canvas_y + bbox_rel[3]
            if popup_y + popup_height > screen_height: popup_y = screen_height - popup_height
            popup.geometry(f"+{popup_x}+{popup_y}")
            popup.deiconify() # Show the window
        except tk.TclError as e: logging.warning(f"TclError positioning/showing {lang_type} popup: {e}")
        except Exception as e: logging.error(f"Error showing/updating {lang_type} popup: {e}", exc_info=True)


    # --- RENAMED: Hide Popups ---
    def _hide_lang_popup(self, lang_type):
        """Hides the specified language popup if it exists."""
        popup = self.source_popup if lang_type == "source" else self.target_popup
        if popup and popup.winfo_exists():
            try:
                popup.withdraw()
                # Unhighlight any label within this popup
                labels_to_check = self.source_labels if lang_type == "source" else self.target_labels
                for label in labels_to_check:
                    if label.winfo_exists() and label.cget('bg') == self.popup_highlight_bg:
                        label.config(bg=self.popup_bg)
            except tk.TclError: pass
            except Exception as e: logging.error(f"Error hiding {lang_type} popup: {e}", exc_info=True)

    def _hide_mode_popup(self):
        """Hides the mode popup if it exists."""
        if self.mode_popup and self.mode_popup.winfo_exists():
            try:
                self.mode_popup.withdraw()
                # Unhighlight any label within this popup
                for label in self.mode_labels:
                    if label.winfo_exists() and label.cget('bg') == self.popup_highlight_bg:
                        label.config(bg=self.popup_bg)
            except tk.TclError: pass
            except Exception as e: logging.error(f"Error hiding mode popup: {e}", exc_info=True)

    def _hide_all_popups(self):
        """Hides all popups."""
        self._hide_mode_popup()
        self._hide_lang_popup("source")
        self._hide_lang_popup("target")
        self.hovered_label_widget = None # Clear hover state when hiding all
        self.hovered_data = None

    # --- MODIFIED: Check Hover and Manage Popups ---
    def _check_hover_and_manage_popups(self, mouse_x, mouse_y):
        """Checks hover and shows/hides the appropriate pre-created popups."""
        if self._stop_event.is_set() or not self.root or not self.root.winfo_exists(): return
        # Check which area is hovered
        is_over_mode = self._is_point_over_tag(mouse_x, mouse_y, "mode_area")
        is_over_source = self._is_point_over_tag(mouse_x, mouse_y, "source_lang_area")
        is_over_target = self._is_point_over_tag(mouse_x, mouse_y, "target_lang_area")

        # Determine which popup *should* be visible (Prioritize Mode > Source > Target)
        popup_to_show = None
        if is_over_mode:
            popup_to_show = "mode"
        elif is_over_source:
            popup_to_show = "source"
        elif is_over_target:
            popup_to_show = "target"

        # Show the correct popup (hide others automatically within the show function)
        if popup_to_show == "mode":
            self._show_and_update_mode_popup()
        elif popup_to_show == "source":
            self._show_and_update_lang_popup("source")
        elif popup_to_show == "target":
            self._show_and_update_lang_popup("target")
        # else: # If none of the specific areas are hovered, but we are still checking hover (e.g., mouse over mic but not text areas)
              # We don't necessarily hide all popups here. Hiding happens when leaving the *entire* interactive area in _check_queue.
              # Or when another popup is explicitly shown by the calls above.

    # --- Helper: Is Point Over Tag (Unchanged) ---
    def _is_point_over_tag(self, point_x, point_y, tag_name):
        if not self.canvas or not self.root or not self.root.winfo_exists(): return False
        try:
            bbox_rel = self.canvas.bbox(tag_name);
            if not bbox_rel: return False
            canvas_x = self.canvas.winfo_rootx(); canvas_y = self.canvas.winfo_rooty()
            bbox_abs = (canvas_x + bbox_rel[0], canvas_y + bbox_rel[1],
                        canvas_x + bbox_rel[2], canvas_y + bbox_rel[3])
            return (bbox_abs[0] <= point_x < bbox_abs[2] and bbox_abs[1] <= point_y < bbox_abs[3])
        except tk.TclError: return False
        except Exception as e: logging.error(f"Error checking hover for tag {tag_name}: {e}", exc_info=True); return False

    # --- Helper: Is Point Over Popup (Unchanged) ---
    def _is_point_over_popup(self, point_x, point_y, popup):
        if not popup or not popup.winfo_exists(): return False
        try:
            popup_x = popup.winfo_rootx(); popup_y = popup.winfo_rooty()
            geo_parts = popup.winfo_geometry().split('+')[0].split('x')
            popup_width = int(geo_parts[0]); popup_height = int(geo_parts[1])
            return (popup_x <= point_x < popup_x + popup_width and
                    popup_y <= point_y < popup_y + popup_height)
        except tk.TclError: return False
        except Exception as e: logging.error(f"Error checking hover for popup: {e}", exc_info=True); return False

    # --- MODIFIED: Generic Popup Release Callback ---
    def _on_popup_label_release(self, event):
        """Handles click release on any popup label."""
        # --- ADD DEBUG LOG ---
        logging.debug(f"_on_popup_label_release triggered for widget: {event.widget}")
        # --- END ADD ---
        widget = event.widget
        widget_id = widget.winfo_id()

        if widget_id in self.label_data:
            data = self.label_data[widget_id]
            action_type = data.get("type")
            action_value = data.get("value")

            if action_type == "mode":
                logging.info(f"Mode selected via popup: {action_value}")
                try: self.action_queue.put_nowait(("select_mode", action_value))
                except queue.Full: logging.warning(f"Action queue full sending mode selection ({action_value}).")
            elif action_type == "source" or action_type == "target":
                logging.info(f"Language selected via popup: Type={action_type}, Code={action_value}")
                try: self.action_queue.put_nowait(("select_language", {"type": action_type, "lang": action_value}))
                except queue.Full: logging.warning(f"Action queue full sending language selection ({action_type}={action_value}).")
            else:
                logging.warning(f"Unknown action type '{action_type}' for clicked label.")
        else:
            logging.warning(f"No data found for clicked label widget ID: {widget_id}")

        # Hide ALL popups immediately after selection
        self._hide_all_popups()

    # --- Helper: Is Point Over Widget (Unchanged) ---
    def _is_point_over_widget(self, point_x, point_y, widget):
        if not widget or not widget.winfo_exists(): return False
        try:
            widget_x = widget.winfo_rootx(); widget_y = widget.winfo_rooty()
            widget_width = widget.winfo_width(); widget_height = widget.winfo_height()
            return (widget_x <= point_x < widget_x + widget_width and
                    widget_y <= point_y < widget_y + widget_height)
        except tk.TclError: return False
        except Exception as e: logging.error(f"Error checking hover for widget: {e}", exc_info=True); return False

    # --- Helper: Is Point Over Mic (Unchanged) ---
    def _is_point_over_mic(self, point_x, point_y):
        if not self.canvas or not self.root or not self.root.winfo_exists(): return False
        try:
            icon_x_offset = self.mode_text_width_estimate
            w, h = self.icon_base_width, self.icon_height
            mic_x0 = icon_x_offset; mic_y0 = 0; mic_x1 = icon_x_offset + w; mic_y1 = h
            canvas_x = self.canvas.winfo_rootx(); canvas_y = self.canvas.winfo_rooty()
            abs_x0 = canvas_x + mic_x0; abs_y0 = canvas_y + mic_y0; abs_x1 = canvas_x + mic_x1; abs_y1 = canvas_y + mic_y1
            return (abs_x0 <= point_x < abs_x1 and abs_y0 <= point_y < abs_y1)
        except Exception as e: logging.error(f"Error in _is_point_over_mic check: {e}", exc_info=True); return False

    # --- Blink Effect (Finds label by data now) ---
    def _blink_and_hide(self, selection_details):
        """ Briefly highlights the selected label, then hides the main window and all popups. """
        if not self.root or not self.root.winfo_exists() or self._stop_event.is_set(): return

        selected_label = None
        sel_type = selection_details.get("type") # mode or language
        sel_value = selection_details.get("value") # mode_name or lang_code
        sel_lang_type = selection_details.get("lang_type") # source or target (only if type is language)

        # Find the label widget corresponding to the selection
        target_data_type = sel_type if sel_type == "mode" else sel_lang_type
        for widget_id, data in self.label_data.items():
            if data.get("type") == target_data_type and data.get("value") == sel_value:
                # Find the widget object from its ID (this is a bit indirect)
                parent_popup = data.get("popup")
                if parent_popup and parent_popup.winfo_exists():
                    for child in parent_popup.winfo_children():
                        if child.winfo_id() == widget_id:
                            selected_label = child
                            break
            if selected_label: break

        if not selected_label or not selected_label.winfo_exists():
            logging.warning(f"Cannot find label for selection to blink: {selection_details}. Hiding immediately.")
            self._hide_after_blink() # Hides main window and all popups
            return

        blink_color = "#FFFF00"; original_color = self.popup_bg
        blink_count = 3; blink_interval_ms = 100

        def blink_step(count_remaining, is_on):
            if self._stop_event.is_set() or not selected_label.winfo_exists():
                self._hide_after_blink(); return
            if count_remaining <= 0:
                try: selected_label.config(bg=original_color)
                except tk.TclError: pass
                self._hide_after_blink(); return # Schedule final hide
            try:
                current_color = blink_color if is_on else original_color
                selected_label.config(bg=current_color)
                next_count = count_remaining if not is_on else count_remaining - 1
                self.root.after(blink_interval_ms, lambda: blink_step(next_count, not is_on))
            except tk.TclError as e: logging.warning(f"TclError during blink: {e}"); self._hide_after_blink()
            except Exception as e: logging.error(f"Error during blink: {e}"); self._hide_after_blink()

        blink_step(blink_count, True) # Start blink

    # --- MODIFIED: Hide After Blink (Hides main window AND all popups) ---
    def _hide_after_blink(self):
        """ Performs the actual hiding of the main window and all popups. """
        if not self.root or not self.root.winfo_exists() or self._stop_event.is_set(): return
        try:
            logging.debug("Hiding StatusIndicator and all popups after blink/selection.")
            self.root.withdraw() # Hide main window
            self._hide_all_popups() # Hide any potentially visible popups
            # Reset state
            self.current_state = "hidden"; self.menus_enabled = False; self.mic_hovered_since_activation = False
        except tk.TclError as e: logging.warning(f"TclError during final hide: {e}.")
        except Exception as e: logging.error(f"Error during final hide: {e}") 