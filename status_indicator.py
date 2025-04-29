import tkinter as tk
import tkinter.font as tkFont
import threading
import queue
import logging
from functools import partial # Import partial for callbacks

# --- Define Mode Constants (can be shared with vibe_app.py) ---
# It's slightly redundant defining them here and in vibe_app.py,
# but keeps the StatusIndicator self-contained regarding display names.
# Consider a shared constants file later if needed.
DEFAULT_MODES = {
    "Dictation": "Dictation Mode",
    "Keyboard": "Keyboard Input Mode"
    # Add "Command" later if desired
}

class StatusIndicatorManager:
    """Manages a Tkinter status icon window (mode + mic icon + volume + languages)."""
    def __init__(self, q, action_q, config, all_languages, all_languages_target, available_modes=None):
        self.queue = q
        self.action_queue = action_q
        # --- Store config and language maps --- >
        self.config = config # Store the full config dict
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

        # --- Popups ---
        self.mode_popup = None
        self.source_popup = None
        self.target_popup = None
        # --- Store labels within popups ---
        self.mode_popup_labels = {} # {mode_name: label_widget}
        self.source_popup_labels = {} # {lang_code: label_widget}
        self.target_popup_labels = {} # {lang_code: label_widget}
        # --- Track currently hovered lang code ---
        self.hovering_over_lang_type = None # 'source' or 'target'
        self.hovering_over_lang_code = None # The actual code (e.g., 'en-US')
        self.hovering_over_mode_name = None # The name of the hovered mode (e.g., 'Keyboard')
        self.currently_hovered_label = None # Track the specific label widget being hovered

        # Icon drawing properties
        self.icon_base_width = 24 # Original icon width
        self.icon_height = 36
        # Estimate width for mode text (can be adjusted)
        self.mode_text_width_estimate = 80
        # Increase text width estimate for languages
        self.lang_text_width_estimate = 120 # Increased estimate
        self.padding = 5
        # Update canvas width calculation
        self.canvas_width = (self.mode_text_width_estimate + self.padding +
                             self.icon_base_width + self.padding +
                             self.lang_text_width_estimate)

        # Colors
        self.mic_body_color = "#CCCCCC" # Light grey for mic body
        self.mic_stand_color = "#AAAAAA" # Darker grey for stand
        # self.volume_fill_color = "#FF0000" # REMOVED - Now mode-dependent
        self.dictation_volume_color = "#FF0000" # Red for Dictation volume
        self.keyboard_volume_color = "#0000FF" # Blue for Keyboard volume
        self.command_volume_color = "#008000" # Green for Command volume (placeholder)
        # self.idle_indicator_color = "#ADD8E6" # REMOVED - No longer needed
        self.text_color = "#333333" # Color for language text
        self.inactive_text_color = "#AAAAAA" # Lighter gray for inactive target lang
        self.mode_text_color = "#333333" # Color for mode text
        self.bg_color = "#FEFEFE" # Use a near-white color for transparency key
        self.popup_bg = "#E0E0E0" # Background for popup
        self.popup_fg = "#000000"
        self.popup_highlight_bg = "#ADD8E6" # Light Blue for better visibility

        # Font object with increased size
        self.text_font_size = 12 # Increased font size
        self.text_font = tkFont.Font(family="Arial", size=self.text_font_size)
        self.popup_font = tkFont.Font(family="Arial", size=10)
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
            self._destroy_lang_popup("source")
            self._destroy_lang_popup("target")
            self._destroy_mode_popup() # Destroy mode popup on exit
            self._stop_event.set()

    def _check_queue(self):
        if self._stop_event.is_set():
            self._cleanup_tk()
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
                    if target_state != "hidden" and self.current_state == "hidden":
                        if pos:
                            self.initial_pos = pos
                            logging.debug(f"StatusIndicator stored initial position: {self.initial_pos}")
                            position_needs_update = True
                            self.menus_enabled = False
                            self.mic_hovered_since_activation = False
                        else:
                            logging.warning("StatusIndicator activated but received no initial position!")
                    elif target_state == "hidden":
                        self.initial_pos = None
                        self.menus_enabled = False
                        self.mic_hovered_since_activation = False
                    if rcvd_source_lang != self.source_lang or rcvd_target_lang != self.target_lang:
                        self.source_lang = rcvd_source_lang
                        self.target_lang = rcvd_target_lang
                        if self.current_state != "hidden":
                            needs_redraw = True
                    if rcvd_mode != self.current_mode:
                        self.current_mode = rcvd_mode
                        if self.current_state != "hidden":
                             needs_redraw = True
                    if target_state != self.current_state:
                        logging.debug(f"StatusIndicator received state change: {self.current_state} -> {target_state}, Mode: {self.current_mode}")
                        needs_redraw = True
                        if target_state == "active":
                             self.current_volume = 0.0
                        elif target_state == "hidden":
                             self._destroy_lang_popup("source")
                             self._destroy_lang_popup("target")
                             self._destroy_mode_popup()
                elif command == "check_hover_position":
                    self.last_hover_pos = data
                elif command == "selection_made":
                    logging.debug(f"StatusIndicator received selection_made command: {data}")
                    # Initiate blink, then hide
                    self._blink_and_hide(data)
                    # Set target state to hidden to prevent further actions until hide completes
                    target_state = "hidden"
                    needs_redraw = False # Prevent normal redraw during blink
                    state_actually_changed = False # Prevent normal state update logic
                elif command == "stop":
                    logging.debug("Received stop command in StatusIndicator queue.")
                    self._stop_event.set()
        except queue.Empty: pass
        except tk.TclError as e:
            if not self._stop_event.is_set(): logging.warning(f"StatusIndicator Tkinter error processing queue: {e}.")
            self._stop_event.set(); self._cleanup_tk(); return
        except Exception as e:
            logging.error(f"Error processing StatusIndicator queue: {e}", exc_info=True)
        state_actually_changed = (target_state != self.current_state)
        if state_actually_changed:
            self.current_state = target_state

        # --- Check if menus need enabling (based on mic hover) --- -> Moved before redraw
        menus_were_just_enabled = False
        if self.current_state != "hidden" and not self.mic_hovered_since_activation:
            mx, my = self.last_hover_pos
            mic_hover = self._is_point_over_mic(mx, my) # Check hover based on CURRENT draw state
            if mic_hover:
                 self.mic_hovered_since_activation = True
                 self.menus_enabled = True
                 menus_were_just_enabled = True # Signal redraw is needed
                 logging.debug("Mic hovered for the first time after activation: menus now enabled.")

        # --- Apply Changes and Redraw --- -> Redraw if state changed OR menus were just enabled
        if (needs_redraw or state_actually_changed or menus_were_just_enabled) and self.root and not self._stop_event.is_set():
            if position_needs_update and self.initial_pos:
                self._position_window(self.initial_pos)
            self._draw_icon() # Draw icon based on potentially updated menus_enabled state
            if self.current_state == "hidden":
                self.root.withdraw()
            else:
                self.root.deiconify()

        # --- Check Popups and Hovered Label ---
        self.hovering_over_lang_type = None
        self.hovering_over_lang_code = None
        self.hovering_over_mode_name = None
        if self.root and not self._stop_event.is_set() and self.current_state != "hidden":
            try:
                mx, my = self.last_hover_pos
                # REMOVED: Logic to enable menus here - moved earlier
                # --- Only allow popups if menus_enabled ---
                if self.menus_enabled:
                    self._check_hover_and_update_popups(mx, my)
                # --- Check if Hovering Over Specific Label --- >
                newly_hovered_label = None
                if self.menus_enabled:
                    popup_dicts = [
                        (self.mode_popup, self.mode_popup_labels),
                        (self.source_popup, self.source_popup_labels),
                        (self.target_popup, self.target_popup_labels)
                    ]
                    for popup, label_dict in popup_dicts:
                        if popup and self._is_point_over_popup(mx, my, popup):
                            for key, label in label_dict.items():
                                if self._is_point_over_widget(mx, my, label):
                                    newly_hovered_label = label
                                    # Store hover details for button release check
                                    if popup is self.mode_popup: self.hovering_over_mode_name = key
                                    elif popup is self.source_popup: self.hovering_over_lang_type = "source"; self.hovering_over_lang_code = key
                                    elif popup is self.target_popup: self.hovering_over_lang_type = "target"; self.hovering_over_lang_code = key
                                    break # Found the hovered label in this popup
                            if newly_hovered_label: break # Found the hovered label, stop checking other popups

                # --- Update Label Highlighting --- >
                # If a new label is hovered
                if newly_hovered_label and newly_hovered_label != self.currently_hovered_label:
                    # Unhighlight previous label if there was one
                    if self.currently_hovered_label and self.currently_hovered_label.winfo_exists():
                        try: self.currently_hovered_label.config(bg=self.popup_bg)
                        except tk.TclError: pass # Ignore error if widget destroyed
                    # Highlight the new label
                    try:
                        newly_hovered_label.config(bg=self.popup_highlight_bg)
                        self.currently_hovered_label = newly_hovered_label
                    except tk.TclError: # Handle case where widget is destroyed between check and config
                        self.currently_hovered_label = None
                # If mouse moved off the previously hovered label and not onto a new one
                elif not newly_hovered_label and self.currently_hovered_label:
                    if self.currently_hovered_label.winfo_exists():
                        try: self.currently_hovered_label.config(bg=self.popup_bg)
                        except tk.TclError: pass
                    self.currently_hovered_label = None

                # --- Destroy Popups Check (after processing hover) --- >
                self._check_and_destroy_popups(mx, my)

            except tk.TclError: pass
            except Exception as e: logging.error(f"Error during hover/popup check: {e}", exc_info=True)

        # --- Reschedule ---
        if not self._stop_event.is_set() and self.root:
             try: self.root.after(50, self._check_queue)
             except tk.TclError: logging.warning("StatusIndicator root destroyed before rescheduling.")
             except Exception as e: logging.error(f"Error rescheduling StatusIndicator check: {e}")

    def _check_and_destroy_popups(self, mouse_x, mouse_y):
        """Checks if mouse is away from popups or their areas or the mic icon and destroys them."""
        # Include mode popup check
        if not (self.source_popup or self.target_popup or self.mode_popup): return

        # Check all relevant hover states first
        is_over_mic = self._is_point_over_mic(mouse_x, mouse_y)
        is_over_mode_area = self._is_point_over_tag(mouse_x, mouse_y, "mode_area")
        is_over_source_area = self._is_point_over_tag(mouse_x, mouse_y, "source_lang_area")
        is_over_target_area = self._is_point_over_tag(mouse_x, mouse_y, "target_lang_area")
        is_over_mode_popup = self._is_point_over_popup(mouse_x, mouse_y, self.mode_popup)
        is_over_source_popup = self._is_point_over_popup(mouse_x, mouse_y, self.source_popup)
        is_over_target_popup = self._is_point_over_popup(mouse_x, mouse_y, self.target_popup)

        # --- Add Logging ---
        # Log hover states periodically for debugging (reduce frequency if too noisy)
        # if time.time() % 1 < 0.1: # Log roughly once per second
        #     logging.debug(f"Popup destroy check: mic={is_over_mic}, "
        #                   f"mode_area={is_over_mode_area}, src_area={is_over_source_area}, tgt_area={is_over_target_area}, "
        #                   f"mode_pop={is_over_mode_popup}, src_pop={is_over_source_popup}, tgt_pop={is_over_target_popup}")

        # --- Refined Destruction Logic ---
        # Destroy popups if mouse is not over the mic OR its trigger area OR the popup itself
        if self.mode_popup and not is_over_mode_area and not is_over_mode_popup and not is_over_mic:
            # logging.debug("Destroying mode popup (reason: outside assembly)")
            self._destroy_mode_popup()
        if self.source_popup and not is_over_source_area and not is_over_source_popup and not is_over_mic:
            # logging.debug("Destroying source popup (reason: outside assembly)")
            self._destroy_lang_popup("source")
        if self.target_popup and not is_over_target_area and not is_over_target_popup and not is_over_mic:
            # logging.debug("Destroying target popup (reason: outside assembly)")
            self._destroy_lang_popup("target")

    def _cleanup_tk(self):
        logging.debug("Executing StatusIndicator _cleanup_tk.")
        if self.root:
            try:
                self.root.destroy()
                logging.info("StatusIndicator root window destroyed.")
                self.root = None
            except Exception as e: logging.warning(f"Error destroying StatusIndicator root: {e}")

    def _position_window(self, pos):
        """Positions the main indicator window based on the given initial position."""
        if self.root and not self._stop_event.is_set() and pos:
            try:
                x, y = pos
                logging.debug(f"[_position_window] Positioning main indicator based on initial pos: ({x}, {y})")
                # Adjust offsets: negative x shifts left, smaller y shifts up
                offset_x = -85 # Shift significantly left
                offset_y = 10  # Shift slightly up from original +15
                new_x = x + offset_x
                new_y = y + offset_y
                # Basic screen boundary check (optional but good practice)
                screen_width = self.root.winfo_screenwidth()
                if new_x + self.canvas_width > screen_width:
                     new_x = screen_width - self.canvas_width
                if new_x < 0: new_x = 0
                if new_y < 0: new_y = 0
                # Apply geometry
                self.root.geometry(f"+{new_x}+{new_y}")
                logging.debug(f"[_position_window] Geometry set to +{new_x}+{new_y}")
            except Exception as e:
                 logging.warning(f"Failed to position StatusIndicator: {e}")
        elif not pos:
            logging.warning("[_position_window] Called without a valid position.")

    def _draw_icon(self):
        """Draws the microphone icon and language text with separate backgrounds."""
        if not self.canvas or not self.root or self._stop_event.is_set():
            return
        try:
            self.canvas.delete("all")

            if self.current_state == "hidden":
                return

            # --- Define common drawing params ---
            text_y = self.icon_height / 2
            text_bg_color = "#FFFFFF"
            text_padding_x = 3
            text_padding_y = 2
            bg_y0 = text_y - (self.text_font_size / 1.5) - text_padding_y
            bg_y1 = text_y + (self.text_font_size / 1.5) + text_padding_y

            # --- Determine fixed icon position and visibility of text areas --- >
            # ALWAYS calculate icon's horizontal starting position assuming mode text space exists
            icon_x_offset = self.mode_text_width_estimate + self.padding
            draw_text_areas = self.menus_enabled

            # --- Draw Mode Text (Left of Mic, only if menus enabled) --- >
            if draw_text_areas:
                # Draw within the allocated space (0 to self.mode_text_width_estimate)
                mode_text = self.current_mode
                # Draw background rectangle for the mode area tag
                self.canvas.create_rectangle(0, bg_y0, self.mode_text_width_estimate, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("mode_area",))
                # Position text inside the background area (e.g., left-aligned with padding)
                text_x = 0 + text_padding_x
                self.canvas.create_text(text_x, text_y, text=mode_text, anchor=tk.W, font=self.text_font, fill=self.mode_text_color, tags=("mode_area",))
            # No 'else' needed here for icon_x_offset calculation

            # --- Draw Microphone Icon (Using the ALWAYS fixed icon_x_offset) --- >
            w, h = self.icon_base_width, self.icon_height
            body_w = w * 0.6; body_h = h * 0.6; body_x = icon_x_offset + (w - body_w) / 2; body_y = h * 0.1
            stand_h = h * 0.2; stand_y = body_y + body_h; stand_w = w * 0.2; stand_x = icon_x_offset + (w - stand_w) / 2
            base_h = h * 0.1; base_y = stand_y + stand_h; base_w = w * 0.8; base_x = icon_x_offset + (w - base_w) / 2
            self.canvas.create_rectangle(stand_x, stand_y, stand_x + stand_w, stand_y + stand_h, fill=self.mic_stand_color, outline="")
            self.canvas.create_rectangle(base_x, base_y, base_x + base_w, base_y + base_h, fill=self.mic_stand_color, outline="")
            mic_body = self.canvas.create_rectangle(body_x, body_y, body_x + body_w, body_y + body_h, fill=self.mic_body_color, outline=self.mic_stand_color)

            # --- Draw Volume Indicator (if active) ---
            if self.current_state == "active":
                 volume_color = self.dictation_volume_color
                 if self.current_mode == "Keyboard": volume_color = self.keyboard_volume_color
                 fill_h = body_h * self.current_volume
                 fill_y = body_y + body_h - fill_h
                 if fill_h > 0:
                    vol_x0 = body_x; vol_y0 = max(body_y, fill_y); vol_x1 = body_x + body_w; vol_y1 = body_y + body_h
                    if vol_y0 < vol_y1: self.canvas.create_rectangle(vol_x0, vol_y0, vol_x1, vol_y1, fill=volume_color, outline="")

            # --- Draw Language Text (Right of Mic, only if menus enabled and source exists) ---
            if draw_text_areas and self.source_lang:
                # Start AFTER the mic icon (whose position depends on whether mode text was drawn)
                current_x = icon_x_offset + self.icon_base_width + self.padding

                # 1. Draw Source Language
                src_text = self.source_lang; src_width = self.text_font.measure(src_text)
                src_bg_x0 = current_x; src_bg_x1 = current_x + src_width + text_padding_x * 2
                self.canvas.create_rectangle(src_bg_x0, bg_y0, src_bg_x1, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("source_lang_area",))
                self.canvas.create_text(current_x + text_padding_x, text_y, text=src_text, anchor=tk.W, font=self.text_font, fill=self.text_color, tags=("source_lang_area",))
                current_x = src_bg_x1

                # 2. Draw Arrow and Target Language Area
                arrow_text = " > "; arrow_width = self.text_font.measure(arrow_text)
                arrow_x = current_x + self.padding // 2
                self.canvas.create_text(arrow_x, text_y, text=arrow_text, anchor=tk.W, font=self.text_font, fill=self.text_color)
                current_x = arrow_x + arrow_width + self.padding // 2

                is_target_active = self.target_lang and self.target_lang != self.source_lang
                tgt_text = self.target_lang if is_target_active else "None"
                tgt_color = self.text_color if is_target_active else self.inactive_text_color
                tgt_width = self.text_font.measure(tgt_text)
                tgt_bg_x0 = current_x; tgt_bg_x1 = current_x + tgt_width + text_padding_x * 2
                self.canvas.create_rectangle(tgt_bg_x0, bg_y0, tgt_bg_x1, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("target_lang_area",))
                self.canvas.create_text(current_x + text_padding_x, text_y, text=tgt_text, anchor=tk.W, font=self.text_font, fill=tgt_color, tags=("target_lang_area",))

        except tk.TclError as e: logging.warning(f"Error drawing status icon: {e}"); self._stop_event.set()
        except Exception as e: logging.error(f"Unexpected error drawing status icon: {e}", exc_info=True)

    def _create_lang_popup(self, lang_type):
        """Creates and displays the language selection popup directly above the corresponding text area."""
        # Ensure other popups are closed
        self._destroy_lang_popup("source" if lang_type == "target" else "target")
        self._destroy_mode_popup() # Also close mode popup
        # self._destroy_lang_popup(lang_type) # Destroy self if exists (redundant?)

        if not self.root or not self.canvas or self._stop_event.is_set() or not self.root.winfo_exists():
            return

        MAX_RECENT_DISPLAY = 3 # Max recent languages for popup
        tag_name = "source_lang_area" if lang_type == "source" else "target_lang_area"

        try:
            bbox_rel = self.canvas.bbox(tag_name)
            logging.debug(f"Popup '{lang_type}': Relative bbox for tag '{tag_name}': {bbox_rel}") # Keep this log
            if not bbox_rel:
                logging.warning(f"Could not find bbox for tag '{tag_name}' to position popup.")
                return
        except tk.TclError as e:
             logging.warning(f"TclError getting bbox for {tag_name}: {e}")
             return

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True); popup.wm_attributes("-topmost", True)
        popup.config(bg=self.popup_bg, relief=tk.SOLID, borderwidth=1)

        # --- Store labels for hover check ---
        current_popup_labels = {}

        # --- Determine which languages to show --- >
        languages_to_show = {}
        if lang_type == "source":
            recent_codes = self.config.get("general", {}).get("recent_source_languages", [])[:MAX_RECENT_DISPLAY]
            for code in recent_codes:
                if code in self.all_languages:
                     languages_to_show[code] = self.all_languages[code]
        else: # Target language
            # Always add None first
            languages_to_show[None] = self.all_languages_target[None]
            recent_codes = self.config.get("general", {}).get("recent_target_languages", [])[:MAX_RECENT_DISPLAY]
            for code in recent_codes:
                 # Don't add None again if it's somehow in recent list
                 if code is not None and code in self.all_languages_target:
                     languages_to_show[code] = self.all_languages_target[code]

        # --- Create labels based on the filtered list --- >
        for lang_code, lang_name in languages_to_show.items():
            label = tk.Label(popup, text=lang_name, font=self.popup_font, bg=self.popup_bg, fg=self.popup_fg, padx=5, pady=2, anchor=tk.W)
            label.pack(fill=tk.X)
            # label.bind("<Enter>", partial(self._on_popup_label_enter, label=label))
            # label.bind("<Leave>", partial(self._on_popup_label_leave, label=label))
            # --- Keep the regular ButtonRelease binding too ---
            label.bind("<ButtonRelease-1>", partial(self._on_popup_label_release, lang_type=lang_type, lang_code=lang_code))
            # Store label reference
            current_popup_labels[lang_code] = label

        # Assign to instance variable after loop
        if lang_type == "source":
             self.source_popup_labels = current_popup_labels
        else:
             self.target_popup_labels = current_popup_labels

        # --- Positioning Logic (unchanged) ---
        try:
            popup.update_idletasks(); self.canvas.update_idletasks()
            canvas_x = self.canvas.winfo_rootx(); canvas_y = self.canvas.winfo_rooty()
            # logging.debug(f"Popup '{lang_type}': Canvas root coords: ({canvas_x}, {canvas_y})")
            bbox_abs_x0 = canvas_x + bbox_rel[0]; bbox_abs_y0 = canvas_y + bbox_rel[1]
            bbox_abs_x1 = canvas_x + bbox_rel[2]; bbox_abs_y1 = canvas_y + bbox_rel[3]
            # logging.debug(f"Popup '{lang_type}': Absolute bbox for tag '{tag_name}': ({bbox_abs_x0}, {bbox_abs_y0}, {bbox_abs_x1}, {bbox_abs_y1})")
            popup_height = popup.winfo_reqheight(); popup_width = popup.winfo_reqwidth()
            # logging.debug(f"Popup '{lang_type}': Required size: {popup_width}x{popup_height}")
            popup_x = bbox_abs_x0; popup_y = bbox_abs_y0 - popup_height - 2
            # logging.debug(f"Popup '{lang_type}': Calculated initial coords: ({popup_x}, {popup_y})")
            screen_width = self.root.winfo_screenwidth(); screen_height = self.root.winfo_screenheight()
            adjusted = False
            if popup_x < 0: popup_x = 0; adjusted = True
            if popup_x + popup_width > screen_width: popup_x = screen_width - popup_width; adjusted = True
            if popup_y < 0:
                popup_y = bbox_abs_y1 + 2; adjusted = True
                if popup_y + popup_height > screen_height: popup_y = screen_height - popup_height; adjusted = True
            # if adjusted: logging.debug(f"Popup '{lang_type}': Adjusted coords: ({popup_x}, {popup_y})")
            popup.geometry(f"+{popup_x}+{popup_y}")
            # logging.info(f"Popup for {lang_type} positioned at +{popup_x}+{popup_y}")

        except tk.TclError as e:
            logging.warning(f"Could not get geometry for precise popup positioning: {e}.")
            try: popup.destroy(); return
            except: pass

        # Store popup reference
        if lang_type == "source": self.source_popup = popup
        else: self.target_popup = popup
        # logging.debug(f"Popup created for {lang_type}")

    def _destroy_lang_popup(self, lang_type):
        """Destroys the specified language popup if it exists."""
        popup = None
        if lang_type == "source" and self.source_popup:
            popup = self.source_popup; self.source_popup = None
            self.source_popup_labels.clear() # Clear label dict
        elif lang_type == "target" and self.target_popup:
            popup = self.target_popup; self.target_popup = None
            self.target_popup_labels.clear() # Clear label dict
        if popup:
            try:
                if self.root and self.root.winfo_exists(): popup.destroy()#; logging.debug(f"Popup destroyed for {lang_type}")
            except tk.TclError: pass
            except Exception as e: logging.error(f"Unexpected error destroying popup {lang_type}: {e}", exc_info=True)

    # --- NEW: Functions for Mode Popup ---
    def _create_mode_popup(self):
        """Creates and displays the mode selection popup directly above the mode text area."""
        # Ensure other popups are closed
        self._destroy_lang_popup("source")
        self._destroy_lang_popup("target")
        self._destroy_mode_popup() # Destroy self if exists

        if not self.root or not self.canvas or self._stop_event.is_set() or not self.root.winfo_exists():
            return

        tag_name = "mode_area"

        try:
            bbox_rel = self.canvas.bbox(tag_name)
            logging.debug(f"Popup 'Mode': Relative bbox for tag '{tag_name}': {bbox_rel}")
            if not bbox_rel:
                logging.warning(f"Could not find bbox for tag '{tag_name}' to position mode popup.")
                return
        except tk.TclError as e:
             logging.warning(f"TclError getting bbox for {tag_name}: {e}")
             return

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True); popup.wm_attributes("-topmost", True)
        popup.config(bg=self.popup_bg, relief=tk.SOLID, borderwidth=1)

        # --- Store labels for hover check ---
        self.mode_popup_labels = {} # Clear previous labels
        for mode_name, mode_display_name in self.available_modes.items():
            label = tk.Label(popup, text=mode_display_name, font=self.popup_font, bg=self.popup_bg, fg=self.popup_fg, padx=5, pady=2, anchor=tk.W)
            label.pack(fill=tk.X)
            # label.bind("<Enter>", partial(self._on_popup_label_enter, label=label))
            # label.bind("<Leave>", partial(self._on_popup_label_leave, label=label))
            label.bind("<ButtonRelease-1>", partial(self._on_mode_popup_label_release, mode_name=mode_name))
            # Store label reference
            self.mode_popup_labels[mode_name] = label

        # --- Positioning Logic (similar to language popups) ---
        try:
            popup.update_idletasks(); self.canvas.update_idletasks()
            canvas_x = self.canvas.winfo_rootx(); canvas_y = self.canvas.winfo_rooty()
            bbox_abs_x0 = canvas_x + bbox_rel[0]; bbox_abs_y0 = canvas_y + bbox_rel[1]
            popup_height = popup.winfo_reqheight(); popup_width = popup.winfo_reqwidth()
            popup_x = bbox_abs_x0; popup_y = bbox_abs_y0 - popup_height - 2 # Position above
            screen_width = self.root.winfo_screenwidth(); screen_height = self.root.winfo_screenheight()
            adjusted = False
            if popup_x < 0: popup_x = 0; adjusted = True
            if popup_x + popup_width > screen_width: popup_x = screen_width - popup_width; adjusted = True
            if popup_y < 0: # If above screen, place below
                popup_y = canvas_y + bbox_rel[3] + 2 # Below the mode text area
                adjusted = True
                if popup_y + popup_height > screen_height: popup_y = screen_height - popup_height; adjusted = True

            popup.geometry(f"+{popup_x}+{popup_y}")
            # logging.info(f"Popup for Mode positioned at +{popup_x}+{popup_y}")

        except tk.TclError as e:
            logging.warning(f"Could not get geometry for precise mode popup positioning: {e}.")
            try: popup.destroy(); return
            except: pass

        # Store popup reference
        self.mode_popup = popup
        # logging.debug("Popup created for Mode")

    def _destroy_mode_popup(self):
        """Destroys the mode popup if it exists."""
        if self.mode_popup:
            popup = self.mode_popup
            self.mode_popup = None
            self.mode_popup_labels.clear() # Clear label dict
            try:
                if self.root and self.root.winfo_exists(): popup.destroy()#; logging.debug("Popup destroyed for Mode")
            except tk.TclError: pass
            except Exception as e: logging.error(f"Unexpected error destroying mode popup: {e}", exc_info=True)

    def _on_mode_popup_label_release(self, event, mode_name):
        """Handles click release on a mode label in the popup."""
        logging.info(f"Mode selected via popup: {mode_name}")
        # Send action to main thread
        try:
            # Send the internal mode name (e.g., "Keyboard"), not the display name
            self.action_queue.put_nowait(("select_mode", mode_name))
        except queue.Full:
            logging.warning(f"Action queue full when sending mode selection ({mode_name}).")

        # Destroy ALL popups immediately after selection
        self._destroy_mode_popup()
        self._destroy_lang_popup("source")
        self._destroy_lang_popup("target")
    # --- End NEW Mode Popup Functions ---


    def _check_hover_and_update_popups(self, mouse_x, mouse_y):
        """Checks if mouse coords are over mode/language areas and creates popups."""
        # Don't check if already destroying
        if self._stop_event.is_set() or not self.root or not self.root.winfo_exists():
            return

        is_over_mode = self._is_point_over_tag(mouse_x, mouse_y, "mode_area")
        is_over_source = self._is_point_over_tag(mouse_x, mouse_y, "source_lang_area")
        is_over_target = self._is_point_over_tag(mouse_x, mouse_y, "target_lang_area")

        # Prioritize Mode popup creation
        if is_over_mode and not self.mode_popup:
             self._create_mode_popup()
        # Only create language popups if NOT hovering over mode area
        elif not is_over_mode:
            if is_over_source and not self.source_popup:
                self._create_lang_popup("source")
            elif is_over_target and not self.target_popup:
                 self._create_lang_popup("target")


    def _is_point_over_tag(self, point_x, point_y, tag_name):
        """Checks if screen coordinates (point_x, point_y) are over the canvas item with tag_name."""
        if not self.canvas or not self.root or not self.root.winfo_exists():
             return False
        try:
            # Get canvas item bounding box relative to canvas
            bbox_rel = self.canvas.bbox(tag_name)
            if not bbox_rel: return False # Tag not found or has no size

            # Get canvas position on screen
            canvas_x = self.canvas.winfo_rootx()
            canvas_y = self.canvas.winfo_rooty()

            # Calculate absolute screen coordinates for the bbox
            bbox_abs = (canvas_x + bbox_rel[0], canvas_y + bbox_rel[1],
                        canvas_x + bbox_rel[2], canvas_y + bbox_rel[3])

            # Check if point is inside
            return (bbox_abs[0] <= point_x < bbox_abs[2] and
                    bbox_abs[1] <= point_y < bbox_abs[3])
        except tk.TclError:
             # Can happen if items/window are being destroyed
             # logging.debug(f"TclError checking tag {tag_name} (transient?)")
             return False
        except Exception as e:
             logging.error(f"Error checking hover for tag {tag_name}: {e}", exc_info=True)
             return False

    def _is_point_over_popup(self, point_x, point_y, popup):
        """Checks if screen coordinates (point_x, point_y) are over the given popup window."""
        if not popup or not popup.winfo_exists():
             return False
        try:
            popup_x = popup.winfo_rootx()
            popup_y = popup.winfo_rooty()
            # Use geometry width/height as winfo_width/height might be 0 initially
            geo_parts = popup.winfo_geometry().split('+')[0].split('x')
            popup_width = int(geo_parts[0])
            popup_height = int(geo_parts[1])
            # Alternative (might be less reliable if not updated):
            # popup_width = popup.winfo_width()
            # popup_height = popup.winfo_height()

            return (popup_x <= point_x < popup_x + popup_width and
                    popup_y <= point_y < popup_y + popup_height)
        except tk.TclError:
             # logging.debug("TclError checking popup hover (transient?)")
             return False
        except Exception as e:
             logging.error(f"Error checking hover for popup: {e}", exc_info=True)
             return False

    def _on_popup_label_enter(self, event, label):
        """Highlights the label when mouse enters."""
        try: # Add try-except for robustness during shutdown
            label_text = label.cget('text') # Get text for logging
            logging.debug(f"Hover Enter: '{label_text}'") # Add log
            label.config(bg=self.popup_highlight_bg)
            label.update_idletasks() # Force visual update
        except tk.TclError: pass
        except Exception as e: logging.error(f"Error in _on_popup_label_enter: {e}", exc_info=True)

    def _on_popup_label_leave(self, event, label):
        """Unhighlights the label when mouse leaves."""
        try:
            label_text = label.cget('text') # Get text for logging
            logging.debug(f"Hover Leave: '{label_text}'") # Add log
            label.config(bg=self.popup_bg)
            label.update_idletasks() # Force visual update
        except tk.TclError: pass
        except Exception as e: logging.error(f"Error in _on_popup_label_leave: {e}", exc_info=True)

    def _on_popup_label_release(self, event, lang_type, lang_code):
        """Handles click release on a language label in the popup."""
        logging.info(f"Language selected via popup: Type={lang_type}, Code={lang_code}")
        # Send action to main thread
        try:
            self.action_queue.put_nowait(("select_language", {"type": lang_type, "lang": lang_code}))
        except queue.Full:
            logging.warning(f"Action queue full when sending language selection ({lang_type}={lang_code}).")

        # Destroy BOTH popups immediately after selection
        self._destroy_lang_popup("source")
        self._destroy_lang_popup("target")

    # --- NEW HELPER: Check if point is over a specific widget ---
    def _is_point_over_widget(self, point_x, point_y, widget):
        """Checks if screen coordinates (point_x, point_y) are over the given widget."""
        if not widget or not widget.winfo_exists():
             return False
        try:
            widget_x = widget.winfo_rootx()
            widget_y = widget.winfo_rooty()
            widget_width = widget.winfo_width()
            widget_height = widget.winfo_height()

            return (widget_x <= point_x < widget_x + widget_width and
                    widget_y <= point_y < widget_y + widget_height)
        except tk.TclError:
             # logging.debug("TclError checking widget hover (transient?)")
             return False
        except Exception as e:
             logging.error(f"Error checking hover for widget: {e}", exc_info=True)
             return False

    # --- Add helper to check if mouse is over mic icon ---
    def _is_point_over_mic(self, point_x, point_y):
        """Checks hover over mic icon, always calculating offset assuming menu space exists."""
        if not self.canvas or not self.root or not self.root.winfo_exists():
            return False
        try:
            # ALWAYS Calculate correct icon offset assuming mode text space exists
            icon_x_offset = self.mode_text_width_estimate + self.padding

            # Use this calculated offset for bounding box check
            w, h = self.icon_base_width, self.icon_height
            mic_x0 = icon_x_offset
            mic_y0 = 0
            mic_x1 = icon_x_offset + w
            mic_y1 = h
            canvas_x = self.canvas.winfo_rootx()
            canvas_y = self.canvas.winfo_rooty()
            abs_x0 = canvas_x + mic_x0
            abs_y0 = canvas_y + mic_y0
            abs_x1 = canvas_x + mic_x1
            abs_y1 = canvas_y + mic_y1
            return (abs_x0 <= point_x < abs_x1 and abs_y0 <= point_y < abs_y1)
        except Exception as e:
            # Log error if calculation fails unexpectedly
            logging.error(f"Error in _is_point_over_mic check: {e}", exc_info=True)
            return False

    # --- NEW: Blink Effect ---    
    def _blink_and_hide(self, selection_details):
        """ Briefly highlights the selected label, then hides the window. """
        if not self.root or not self.root.winfo_exists() or self._stop_event.is_set():
            return

        selected_label = None
        sel_type = selection_details.get("type")
        sel_value = selection_details.get("value")

        # --- Find the selected label widget --- >
        if sel_type == "mode":
            selected_label = self.mode_popup_labels.get(sel_value)
        elif sel_type == "language":
            lang_type = selection_details.get("lang_type")
            if lang_type == "source":
                selected_label = self.source_popup_labels.get(sel_value)
            elif lang_type == "target":
                selected_label = self.target_popup_labels.get(sel_value)

        if not selected_label or not selected_label.winfo_exists():
            logging.warning(f"Could not find label for selection to blink: {selection_details}. Hiding immediately.")
            self._hide_after_blink() # Hide immediately if label not found
            return

        # --- Blink Logic --- >
        blink_color = "#FFFF00" # Yellow blink
        original_color = self.popup_bg # Original background of popup labels
        blink_count = 3 # Number of blinks (on/off pairs)
        blink_interval_ms = 100 # Duration for each on/off state

        def blink_step(count_remaining, is_on):
            if self._stop_event.is_set() or not selected_label.winfo_exists():
                self._hide_after_blink() # Ensure hide if stopped or label destroyed
                return

            if count_remaining <= 0:
                try: selected_label.config(bg=original_color) # Ensure final state is original color
                except tk.TclError: pass
                self._hide_after_blink() # Schedule final hide
                return

            try:
                current_color = blink_color if is_on else original_color
                selected_label.config(bg=current_color)
                next_count = count_remaining if not is_on else count_remaining - 1
                self.root.after(blink_interval_ms, lambda: blink_step(next_count, not is_on))
            except tk.TclError as e:
                 logging.warning(f"TclError during blink step: {e}. Stopping blink.")
                 self._hide_after_blink()
            except Exception as e:
                 logging.error(f"Error during blink step: {e}")
                 self._hide_after_blink()

        # Start the first blink step (turn label on)
        blink_step(blink_count, True)

    def _reset_blink_and_schedule_hide(self, original_color, hide_delay_ms):
        """ Resets color after blink and schedules the final hide action. """
        if not self.root or not self.root.winfo_exists() or self._stop_event.is_set():
            return
        try:
            if self.canvas:
                self.canvas.config(bg=original_color)
            self.root.config(bg=original_color)
            self.root.attributes("-transparentcolor", original_color)

            # Step 3: Schedule the actual hide after hide_delay_ms
            self.root.after(hide_delay_ms, self._hide_after_blink)

        except tk.TclError as e:
            logging.warning(f"TclError during blink reset/hide schedule: {e}. Hiding immediately.")
            self._hide_after_blink() # Attempt immediate hide
        except Exception as e:
            logging.error(f"Error resetting blink/scheduling hide: {e}")
            self._hide_after_blink() # Attempt immediate hide

    def _hide_after_blink(self):
        """ Performs the actual hiding of the window and state cleanup. """
        if not self.root or not self.root.winfo_exists() or self._stop_event.is_set():
            return
        try:
            logging.debug("Hiding StatusIndicator after blink/selection.")
            self.root.withdraw()
            self.current_state = "hidden"
            self.menus_enabled = False
            self.mic_hovered_since_activation = False
            self._destroy_lang_popup("source")
            self._destroy_lang_popup("target")
            self._destroy_mode_popup()
        except tk.TclError as e:
            logging.warning(f"TclError during final hide: {e}.")
        except Exception as e:
            logging.error(f"Error during final hide: {e}") 