import tkinter as tk
import tkinter.font as tkFont
import threading
import queue
import logging
from functools import partial # Import partial for callbacks

class StatusIndicatorManager:
    """Manages a Tkinter status icon window (mic icon + volume + languages)."""
    def __init__(self, q, action_q, pref_src_langs, pref_tgt_langs):
        self.queue = q
        self.action_queue = action_q
        self.pref_src_langs = pref_src_langs
        self.pref_tgt_langs = pref_tgt_langs
        self.root = None
        self.canvas = None
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event()
        self.current_volume = 0.0 # Store current volume level (0.0 to 1.0)
        self.current_state = "hidden" # "hidden", "idle", "active"
        # --- Store the initial position received when activated ---
        self.initial_pos = None
        # --- Store last known mouse hover position for interaction checks ---
        self.last_hover_pos = (0, 0)
        self.source_lang = "" # Store current source language code
        self.target_lang = "" # Store current target language code (or None/empty)

        # --- Popups ---
        self.source_popup = None
        self.target_popup = None

        # Icon drawing properties
        self.icon_base_width = 24 # Original icon width
        self.icon_height = 36
        # Increase text width estimate for larger font and potential two parts
        self.text_width_estimate = 120 # Increased estimate
        self.padding = 5
        self.canvas_width = self.icon_base_width + self.padding + self.text_width_estimate

        # Colors
        self.mic_body_color = "#CCCCCC" # Light grey for mic body
        self.mic_stand_color = "#AAAAAA" # Darker grey for stand
        self.volume_fill_color = "#FF0000" # Red for volume level
        self.idle_indicator_color = "#ADD8E6" # Light blue when ready but not recording
        self.text_color = "#333333" # Color for language text
        self.bg_color = "#FEFEFE" # Use a near-white color for transparency key
        self.popup_bg = "#E0E0E0" # Background for popup
        self.popup_fg = "#000000"
        self.popup_highlight_bg = "#B0B0B0" # Highlight for popup items

        # Font object with increased size
        self.text_font_size = 12 # Increased font size
        self.text_font = tkFont.Font(family="Arial", size=self.text_font_size)
        self.popup_font = tkFont.Font(family="Arial", size=10)

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
            self._stop_event.set()

    def _check_queue(self):
        if self._stop_event.is_set():
            self._cleanup_tk()
            return

        # --- Flags to determine action after processing queue ---
        needs_redraw = False
        position_needs_update = False
        target_state = self.current_state # Start with current state

        # --- Process Queue ---
        try:
            while not self.queue.empty():
                command, data = self.queue.get_nowait()

                if command == "volume":
                    new_volume = data
                    if abs(new_volume - self.current_volume) > 0.02:
                        self.current_volume = new_volume
                        # Mark for redraw only if visible and active
                        if self.current_state == "active":
                            needs_redraw = True

                elif command == "state":
                    # Update target_state immediately for this iteration
                    target_state = data.get("state", "hidden")
                    pos = data.get("pos") # Initial position (only provided on activation)
                    rcvd_source_lang = data.get("source_lang", "")
                    rcvd_target_lang = data.get("target_lang", None)

                    # --- Store initial position if activating ---
                    if target_state != "hidden" and self.current_state == "hidden":
                        if pos:
                            self.initial_pos = pos
                            logging.debug(f"StatusIndicator stored initial position: {self.initial_pos}")
                            position_needs_update = True # Signal to position window later
                        else:
                            logging.warning("StatusIndicator activated but received no initial position!")
                    
                    # --- Clear initial position if hiding ---
                    elif target_state == "hidden":
                         self.initial_pos = None

                    # --- Update languages ---
                    # Check if languages actually changed
                    if rcvd_source_lang != self.source_lang or rcvd_target_lang != self.target_lang:
                        self.source_lang = rcvd_source_lang
                        self.target_lang = rcvd_target_lang
                        # Mark for redraw if languages changed while visible
                        if self.current_state != "hidden":
                            needs_redraw = True
                    
                    # Mark for redraw if the state itself changed
                    if target_state != self.current_state:
                        logging.debug(f"StatusIndicator received state change: {self.current_state} -> {target_state}")
                        needs_redraw = True
                        if target_state == "active":
                             self.current_volume = 0.0 # Reset volume on becoming active
                        elif target_state == "hidden":
                             # Destroy popups when becoming hidden
                             self._destroy_lang_popup("source")
                             self._destroy_lang_popup("target")


                elif command == "check_hover_position":
                    self.last_hover_pos = data # Update last known hover position

                elif command == "stop":
                    logging.debug("Received stop command in StatusIndicator queue.")
                    self._stop_event.set()

        except queue.Empty: pass
        except tk.TclError as e:
            if not self._stop_event.is_set(): logging.warning(f"StatusIndicator Tkinter error processing queue: {e}.")
            self._stop_event.set(); self._cleanup_tk(); return
        except Exception as e:
            logging.error(f"Error processing StatusIndicator queue: {e}", exc_info=True)


        # --- Update State AFTER processing the whole queue for this cycle ---
        state_actually_changed = (target_state != self.current_state)
        if state_actually_changed:
            self.current_state = target_state # Now update the instance state

        # --- Apply Changes and Redraw ---
        # Redraw if flag is set OR if the state actually changed this cycle
        if (needs_redraw or state_actually_changed) and self.root and not self._stop_event.is_set():
            # --- Position window FIRST if needed ---
            # This ensures geometry is set before drawing/deiconifying
            if position_needs_update and self.initial_pos:
                self._position_window(self.initial_pos)
            
            # --- Draw based on the NEW current_state ---
            self._draw_icon()

            # --- Show or Hide ---
            if self.current_state == "hidden":
                self.root.withdraw()
            else:
                # Ensure it's visible if not hidden (covers activation case)
                self.root.deiconify()

        # --- Check Popups (Based on updated last_hover_pos) ---
        # Only check if the indicator should be visible
        if self.root and not self._stop_event.is_set() and self.current_state != "hidden":
            try:
                # Create popups if needed
                self._check_hover_and_update_popups(self.last_hover_pos[0], self.last_hover_pos[1])
                # Destroy popups if needed
                self._check_and_destroy_popups(self.last_hover_pos[0], self.last_hover_pos[1])
            except tk.TclError: pass
            except Exception as e: logging.error(f"Error during hover/popup check: {e}", exc_info=True)

        # --- Reschedule ---
        if not self._stop_event.is_set() and self.root:
             try: self.root.after(50, self._check_queue)
             except tk.TclError: logging.warning("StatusIndicator root destroyed before rescheduling.")
             except Exception as e: logging.error(f"Error rescheduling StatusIndicator check: {e}")

    # --- ADDED HELPER for clarity ---
    def _check_and_destroy_popups(self, mouse_x, mouse_y):
        """Checks if mouse is away from popups or their areas and destroys them."""
        if not (self.source_popup or self.target_popup): return # Skip if no popups exist

        is_over_source_area = self._is_point_over_tag(mouse_x, mouse_y, "source_lang_area")
        is_over_target_area = self._is_point_over_tag(mouse_x, mouse_y, "target_lang_area")
        is_over_source_popup = self._is_point_over_popup(mouse_x, mouse_y, self.source_popup)
        is_over_target_popup = self._is_point_over_popup(mouse_x, mouse_y, self.target_popup)

        if self.source_popup and not is_over_source_area and not is_over_source_popup:
            # logging.debug("Mouse left source area/popup, destroying source popup.")
            self._destroy_lang_popup("source")
        if self.target_popup and not is_over_target_area and not is_over_target_popup:
            # logging.debug("Mouse left target area/popup, destroying target popup.")
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
                offset_x = 5
                offset_y = 15
                self.root.geometry(f"+{x + offset_x}+{y + offset_y}")
                logging.debug(f"[_position_window] Geometry set to +{x + offset_x}+{y + offset_y}")
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
            # Use self.current_state which is now guaranteed to be updated
            # logging.debug(f"[DrawIcon] Drawing State: {self.current_state}, Source: '{self.source_lang}', Target: '{self.target_lang}', Volume: {self.current_volume:.2f}")

            if self.current_state == "hidden":
                # logging.debug("[DrawIcon] State is hidden, returning.")
                return

            # --- Draw Microphone Icon ---
            w, h = self.icon_base_width, self.icon_height; icon_x_offset = 0
            body_w = w * 0.6; body_h = h * 0.6; body_x = icon_x_offset + (w - body_w) / 2; body_y = h * 0.1
            stand_h = h * 0.2; stand_y = body_y + body_h; stand_w = w * 0.2; stand_x = icon_x_offset + (w - stand_w) / 2
            base_h = h * 0.1; base_y = stand_y + stand_h; base_w = w * 0.8; base_x = icon_x_offset + (w - base_w) / 2
            self.canvas.create_rectangle(stand_x, stand_y, stand_x + stand_w, stand_y + stand_h, fill=self.mic_stand_color, outline="")
            self.canvas.create_rectangle(base_x, base_y, base_x + base_w, base_y + base_h, fill=self.mic_stand_color, outline="")
            self.canvas.create_rectangle(body_x, body_y, body_x + body_w, body_y + body_h, fill=self.mic_body_color, outline=self.mic_stand_color)
            if self.current_state == "idle":
                idle_r = body_w * 0.2; idle_cx = body_x + body_w / 2; idle_cy = body_y + body_h / 2
                self.canvas.create_oval(idle_cx - idle_r, idle_cy - idle_r, idle_cx + idle_r, idle_cy + idle_r, fill=self.idle_indicator_color, outline="")
            elif self.current_state == "active":
                fill_h = body_h * self.current_volume; fill_y = body_y + body_h - fill_h
                if fill_h > 0: self.canvas.create_rectangle(body_x, fill_y, body_x + body_w, body_y + body_h, fill=self.volume_fill_color, outline="")

            # --- Draw Language Text ---
            if self.current_state in ["idle", "active"] and self.source_lang:
                text_y = self.icon_height / 2; text_bg_color = "#FFFFFF"; text_padding_x = 3; text_padding_y = 2
                bg_y0 = text_y - (self.text_font_size / 1.5) - text_padding_y; bg_y1 = text_y + (self.text_font_size / 1.5) + text_padding_y
                current_x = icon_x_offset + self.icon_base_width + self.padding
                # 1. Draw Source Language
                src_text = self.source_lang; src_width = self.text_font.measure(src_text)
                src_bg_x0 = current_x - text_padding_x; src_bg_x1 = current_x + src_width + text_padding_x
                self.canvas.create_rectangle(src_bg_x0, bg_y0, src_bg_x1, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("source_lang_area",))
                self.canvas.create_text(current_x, text_y, text=src_text, anchor=tk.W, font=self.text_font, fill=self.text_color, tags=("source_lang_area",))
                # logging.debug(f"[DrawIcon] Drawn source: '{src_text}' at ({current_x}, {text_y})")
                current_x += src_width
                # 2. Draw Arrow and Target Language (if applicable)
                if self.target_lang and self.target_lang != self.source_lang:
                    arrow_text = " > "; arrow_width = self.text_font.measure(arrow_text)
                    tgt_text = self.target_lang; tgt_width = self.text_font.measure(tgt_text)
                    self.canvas.create_text(current_x, text_y, text=arrow_text, anchor=tk.W, font=self.text_font, fill=self.text_color)
                    # logging.debug(f"[DrawIcon] Drawn arrow: '{arrow_text}' at ({current_x}, {text_y})")
                    current_x += arrow_width
                    tgt_bg_x0 = current_x - text_padding_x; tgt_bg_x1 = current_x + tgt_width + text_padding_x
                    self.canvas.create_rectangle(tgt_bg_x0, bg_y0, tgt_bg_x1, bg_y1, fill=text_bg_color, outline=self.mic_stand_color, tags=("target_lang_area",))
                    self.canvas.create_text(current_x, text_y, text=tgt_text, anchor=tk.W, font=self.text_font, fill=self.text_color, tags=("target_lang_area",))
                    # logging.debug(f"[DrawIcon] Drawn target: '{tgt_text}' at ({current_x}, {text_y})")
            # else: logging.debug(f"[DrawIcon] Skipping text draw.")
        except tk.TclError as e: logging.warning(f"Error drawing status icon: {e}"); self._stop_event.set()
        except Exception as e: logging.error(f"Unexpected error drawing status icon: {e}", exc_info=True)

    def _create_lang_popup(self, lang_type):
        """Creates and displays the language selection popup."""
        other_type = "target" if lang_type == "source" else "source"
        self._destroy_lang_popup(other_type)
        self._destroy_lang_popup(lang_type) # Destroy existing of same type first
        if not self.root or self._stop_event.is_set(): return
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True); popup.wm_attributes("-topmost", True)
        popup.config(bg=self.popup_bg, relief=tk.SOLID, borderwidth=1)
        lang_dict = self.pref_src_langs if lang_type == "source" else self.pref_tgt_langs
        for lang_code, lang_name in lang_dict.items():
            display_name = lang_name if lang_code is not None else "None"
            label = tk.Label(popup, text=display_name, font=self.popup_font, bg=self.popup_bg, fg=self.popup_fg, padx=5, pady=2, anchor=tk.W)
            label.pack(fill=tk.X)
            label.bind("<Enter>", partial(self._on_popup_label_enter, label=label))
            label.bind("<Leave>", partial(self._on_popup_label_leave, label=label))
            label.bind("<ButtonRelease-1>", partial(self._on_popup_label_release, lang_type=lang_type, lang_code=lang_code))
        try:
            popup.update_idletasks(); self.root.update_idletasks()
            main_win_x = self.root.winfo_rootx(); main_win_y = self.root.winfo_rooty()
            popup_offset_x = self.canvas_width + 5; popup_offset_y = 0
            popup.geometry(f"+{main_win_x + popup_offset_x}+{main_win_y + popup_offset_y}")
            # logging.debug(f"Popup for {lang_type} positioned relative at +{main_win_x + popup_offset_x}+{main_win_y + popup_offset_y}")
        except tk.TclError as e:
            logging.warning(f"Could not get geometry for popup positioning: {e}.")
            try: popup.destroy(); return
            except: pass
        if lang_type == "source": self.source_popup = popup
        else: self.target_popup = popup
        # logging.debug(f"Popup created for {lang_type}") # Noisy

    def _destroy_lang_popup(self, lang_type):
        """Destroys the specified language popup if it exists."""
        popup = None
        if lang_type == "source" and self.source_popup:
            popup = self.source_popup; self.source_popup = None
        elif lang_type == "target" and self.target_popup:
            popup = self.target_popup; self.target_popup = None
        if popup:
            try:
                if self.root and self.root.winfo_exists(): popup.destroy()#; logging.debug(f"Popup destroyed for {lang_type}")
                # else: logging.debug(f"Skipped destroying popup for {lang_type}, root destroyed.")
            except tk.TclError: pass # Ignore errors during shutdown
            except Exception as e: logging.error(f"Unexpected error destroying popup {lang_type}: {e}", exc_info=True)

    def _check_hover_and_update_popups(self, mouse_x, mouse_y):
        """Checks if mouse coords are over language areas and creates popups."""
        # Don't check if already destroying
        if self._stop_event.is_set() or not self.root or not self.root.winfo_exists():
            return

        is_over_source = self._is_point_over_tag(mouse_x, mouse_y, "source_lang_area")
        is_over_target = self._is_point_over_tag(mouse_x, mouse_y, "target_lang_area")

        # Create source popup if hovering and it doesn't exist
        if is_over_source and not self.source_popup:
            # logging.debug(f"Hover check: Mouse ({mouse_x},{mouse_y}) detected over source area, creating popup.") # Noisy
            self._create_lang_popup("source")
        # Create target popup if hovering and it doesn't exist (and should exist)
        elif is_over_target and not self.target_popup:
             if self.target_lang and self.target_lang != self.source_lang:
                 # logging.debug(f"Hover check: Mouse ({mouse_x},{mouse_y}) detected over target area, creating popup.") # Noisy
                 self._create_lang_popup("target")
        # Note: This function ONLY creates popups based on hover position.
        # The separate check in _check_queue handles destroying them when hover stops.

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
            label.config(bg=self.popup_highlight_bg)
        except tk.TclError: pass

    def _on_popup_label_leave(self, event, label):
        """Unhighlights the label when mouse leaves."""
        try:
            label.config(bg=self.popup_bg)
        except tk.TclError: pass

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