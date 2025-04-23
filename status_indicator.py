import tkinter as tk
import tkinter.font as tkFont
import threading
import queue
import logging

class StatusIndicatorManager:
    """Manages a Tkinter status icon window (mic icon + volume + languages)."""
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
        self.source_lang = "" # Store current source language code
        self.target_lang = "" # Store current target language code (or None/empty)

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

        # Font object with increased size
        self.text_font_size = 12 # Increased font size
        self.text_font = tkFont.Font(family="Arial", size=self.text_font_size)

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
            self._stop_event.set()

    def _check_queue(self):
        if self._stop_event.is_set():
            self._cleanup_tk()
            return

        needs_redraw = False
        new_state = self.current_state
        new_pos = self.last_pos
        old_source_lang = self.source_lang
        old_target_lang = self.target_lang

        try:
            while not self.queue.empty():
                command, data = self.queue.get_nowait()
                if command == "volume":
                    new_volume = data
                    # Update volume regardless of state, draw logic handles visibility
                    if abs(new_volume - self.current_volume) > 0.02:
                         self.current_volume = new_volume
                         # Only redraw *because* of volume if active
                         if self.current_state == "active":
                             needs_redraw = True
                elif command == "state":
                    target_state = data.get("state", "hidden")
                    pos = data.get("pos", self.last_pos)
                    rcvd_source_lang = data.get("source_lang", "")
                    rcvd_target_lang = data.get("target_lang", None)

                    logging.debug(f"[_check_queue] Received state command: state={target_state}, src='{rcvd_source_lang}', tgt='{rcvd_target_lang}', pos={pos}")

                    if target_state != self.current_state:
                        new_state = target_state
                        if new_state == "active": self.current_volume = 0.0 # Reset volume display
                        needs_redraw = True

                    if pos != self.last_pos:
                        new_pos = pos
                        self._position_window(new_pos)
                        self.last_pos = new_pos
                        if new_state != "hidden" and not needs_redraw:
                             needs_redraw = True # Redraw if visible and moved

                    # Update languages and trigger redraw if changed
                    if rcvd_source_lang != self.source_lang or rcvd_target_lang != self.target_lang:
                        self.source_lang = rcvd_source_lang
                        self.target_lang = rcvd_target_lang
                        if new_state != "hidden": # Only trigger redraw for lang change if visible
                             needs_redraw = True

                elif command == "stop":
                    logging.debug("Received stop command in StatusIndicator queue.")
                    self._stop_event.set()

        except queue.Empty: pass
        except tk.TclError as e:
            logging.warning(f"StatusIndicator Tkinter error during queue processing: {e}.")
            self._stop_event.set(); self._cleanup_tk(); return
        except Exception as e:
            logging.error(f"Error processing StatusIndicator queue: {e}", exc_info=True)

        # Update state and redraw if needed
        if new_state != self.current_state:
             self.current_state = new_state
             needs_redraw = True # Ensure redraw on state change

        if needs_redraw and self.root and not self._stop_event.is_set():
            self._draw_icon()
            if self.current_state == "hidden":
                 self.root.withdraw()
            else:
                 # Update position just before showing, in case it changed while hidden
                 self._position_window(self.last_pos)
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
                # Adjust offset based on wider canvas
                offset_x = 5
                offset_y = 15 # Position slightly below cursor
                self.root.geometry(f"+{x + offset_x}+{y + offset_y}")
            except Exception as e:
                 logging.warning(f"Failed to position StatusIndicator: {e}")

    def _draw_icon(self):
        """Draws the microphone icon and language text with separate backgrounds."""
        if not self.canvas or not self.root or self._stop_event.is_set():
            return

        try:
            self.canvas.delete("all")
            logging.debug(f"[DrawIcon] State: {self.current_state}, Source: '{self.source_lang}', Target: '{self.target_lang}', Volume: {self.current_volume:.2f}")

            if self.current_state == "hidden":
                logging.debug("[DrawIcon] State is hidden, returning.")
                return

            # --- Draw Microphone Icon ---
            w, h = self.icon_base_width, self.icon_height
            icon_x_offset = 0
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
                # Common Y coordinate and background settings
                text_y = self.icon_height / 2
                text_bg_color = "#FFFFFF"
                text_padding_x = 3
                text_padding_y = 2
                # Adjust vertical padding based on font size for better centering
                bg_y0 = text_y - (self.text_font_size / 1.5) - text_padding_y # Adjust divisor as needed
                bg_y1 = text_y + (self.text_font_size / 1.5) + text_padding_y

                # Calculate starting X for the first text element
                current_x = icon_x_offset + self.icon_base_width + self.padding

                # 1. Draw Source Language
                src_text = self.source_lang
                src_width = self.text_font.measure(src_text)
                # Draw source background
                src_bg_x0 = current_x - text_padding_x
                src_bg_x1 = current_x + src_width + text_padding_x
                self.canvas.create_rectangle(src_bg_x0, bg_y0, src_bg_x1, bg_y1,
                                            fill=text_bg_color, outline=self.mic_stand_color)
                # Draw source text
                self.canvas.create_text(current_x, text_y, text=src_text, anchor=tk.W,
                                        font=self.text_font, fill=self.text_color)
                logging.debug(f"[DrawIcon] Drawn source: '{src_text}' at ({current_x}, {text_y}) with bg")
                # Update current_x for the next element
                current_x += src_width

                # 2. Draw Arrow and Target Language (if applicable)
                if self.target_lang and self.target_lang != self.source_lang:
                    arrow_text = " > "
                    arrow_width = self.text_font.measure(arrow_text)
                    tgt_text = self.target_lang
                    tgt_width = self.text_font.measure(tgt_text)

                    # Draw arrow text (no background)
                    self.canvas.create_text(current_x, text_y, text=arrow_text, anchor=tk.W,
                                            font=self.text_font, fill=self.text_color)
                    logging.debug(f"[DrawIcon] Drawn arrow: '{arrow_text}' at ({current_x}, {text_y}) no bg")
                    # Update current_x
                    current_x += arrow_width

                    # Draw target background
                    tgt_bg_x0 = current_x - text_padding_x
                    tgt_bg_x1 = current_x + tgt_width + text_padding_x
                    self.canvas.create_rectangle(tgt_bg_x0, bg_y0, tgt_bg_x1, bg_y1,
                                                fill=text_bg_color, outline=self.mic_stand_color)
                    # Draw target text
                    self.canvas.create_text(current_x, text_y, text=tgt_text, anchor=tk.W,
                                            font=self.text_font, fill=self.text_color)
                    logging.debug(f"[DrawIcon] Drawn target: '{tgt_text}' at ({current_x}, {text_y}) with bg")

            else:
                 logging.debug(f"[DrawIcon] Skipping text draw. State='{self.current_state}', Source Lang='{self.source_lang}'")

        except tk.TclError as e:
            logging.warning(f"Error drawing status icon (window closed?): {e}")
            self._stop_event.set()
        except Exception as e:
            logging.error(f"Unexpected error drawing status icon: {e}", exc_info=True) 