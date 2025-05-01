import tkinter as tk
import tkinter.font as tkFont
import threading
import queue
import logging
import time # Keep time for logging/debugging if needed


class ActionConfirmManager:
    """Manages a Tkinter confirmation icon window for pending actions."""
    def __init__(self, command_q, action_q):
        """
        Args:
            command_q: Queue to receive commands like ("show", {"action": ..., "pos": ...}) or ("hide", None)
            action_q: Queue to send ("action_confirmed", action_name) messages back to the main app.
        """
        self.command_queue = command_q
        self.action_queue = action_q
        self.root = None
        self.canvas = None
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event()

        # --- State for the confirmation UI --- >
        self.current_state = "hidden" # "hidden" or "visible"
        self.pending_action = None    # e.g., "Enter", "Escape"
        self.last_hover_pos = (0, 0)
        self.confirmation_sent = False # Flag to prevent sending multiple confirmations
        self.initial_pos = None # Store position where the icon should appear
        self.show_time = 0 # Track when the icon was shown
        self.auto_hide_after_sec = 3.0 # Timeout duration

        # --- Simplified Icon Drawing Properties --- >
        self.icon_width = 65  # Adjusted width for text like "[Entrée]"
        self.icon_height = 28 # Adjusted height
        self.canvas_width = self.icon_width
        self.canvas_height = self.icon_height

        # Colors
        self.bg_color = "#FEFEFE" # Transparency key (needs to be unique)
        self.icon_bg = "#E0E0E0" # Normal background
        self.icon_border = "#999999"
        self.icon_text_color = "#000000"
        self.icon_hover_bg = "#BBDDFF" # Light blue when hovered for confirmation

        # Font
        self.font = ("Segoe UI", 9, "bold")

    def start(self):
        self.thread.start()
        self._tk_ready.wait(timeout=2.0)
        if not self._tk_ready.is_set():
            logging.warning("ActionConfirmManager Tkinter thread did not become ready.")

    def stop(self):
        logging.debug("Stop requested for ActionConfirmManager.")
        self._stop_event.set()
        try:
            self.command_queue.put_nowait(("stop", None))
        except queue.Full:
            logging.warning("ActionConfirmManager command queue full when sending stop command.")

    def _run_tkinter(self):
        logging.info("ActionConfirmManager thread started.")
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.wm_attributes("-topmost", True)
            self.root.attributes("-transparentcolor", self.bg_color)
            self.root.config(bg=self.bg_color)

            self.canvas = tk.Canvas(self.root, width=self.canvas_width, height=self.canvas_height,
                                    bg=self.bg_color, highlightthickness=0)
            self.canvas.pack()

            self._tk_ready.set()
            logging.debug("ActionConfirmManager Tkinter objects created.")
            self._check_queue()
            self.root.mainloop()
            logging.debug("ActionConfirmManager mainloop finished.")

        except Exception as e:
            logging.error(f"Error during ActionConfirmManager mainloop/setup: {e}", exc_info=True)
            self._tk_ready.set()
        finally:
            logging.info("ActionConfirmManager thread finishing.")
            self._stop_event.set()
            if self.root and self.root.winfo_exists():
                 try: self.root.destroy()
                 except: pass
            self.root = None

    def _check_queue(self):
        if self._stop_event.is_set():
            self._cleanup_tk()
            return

        needs_redraw = False
        position_needs_update = False
        target_state = self.current_state
        action_changed = False

        try:
            while not self.command_queue.empty():
                command, data = self.command_queue.get_nowait()

                if command == "show":
                    action = data.get("action")
                    pos = data.get("pos")
                    if action and pos:
                        if self.current_state == "hidden":
                            target_state = "visible"
                            self.initial_pos = pos
                            position_needs_update = True
                            self.show_time = time.monotonic()
                        if action != self.pending_action:
                            self.pending_action = action
                            self.confirmation_sent = False
                            needs_redraw = True
                            action_changed = True
                            if not self.confirmation_sent:
                                self.show_time = time.monotonic()
                        logging.debug(f"ActionConfirm showing for: {action} at {pos}")
                    else:
                        logging.warning(f"ActionConfirm received invalid 'show' data: {data}")

                elif command == "hide":
                    if self.current_state != "hidden":
                        target_state = "hidden"
                        if self.pending_action: needs_redraw = True # Ensure redraw on hide if action was pending
                        self.pending_action = None
                        self.confirmation_sent = False
                        logging.debug("ActionConfirm hiding.")

                elif command == "stop":
                    logging.debug("Received stop command in ActionConfirm queue.");
                    self._stop_event.set()
                    continue

        except queue.Empty: pass
        except tk.TclError as e:
            if not self._stop_event.is_set(): logging.warning(f"ActionConfirm Tkinter error processing queue: {e}.")
            self._stop_event.set(); self._cleanup_tk(); return
        except Exception as e: logging.error(f"Error processing ActionConfirm queue: {e}", exc_info=True)

        mx, my = (0, 0)
        if self.root and self.root.winfo_exists():
            try: mx, my = self.root.winfo_pointerxy()
            except tk.TclError: pass
        self.last_hover_pos = (mx, my)

        state_actually_changed = (target_state != self.current_state)
        if state_actually_changed:
            self.current_state = target_state
            if self.current_state == "hidden":
                self.pending_action = None
                self.confirmation_sent = False
                self.show_time = 0

        is_hovering = False
        last_confirmation_state = self.confirmation_sent
        if self.current_state == "visible" and self.pending_action and self.root and self.canvas:
            is_hovering = self._is_point_over_widget(mx, my, self.canvas)
            if is_hovering and not self.confirmation_sent:
                try:
                    self.action_queue.put_nowait(("action_confirmed", self.pending_action))
                    self.confirmation_sent = True
                    logging.info(f"Action '{self.pending_action}' confirmed by hover.")
                except queue.Full:
                    logging.warning(f"Action queue full sending confirmation for {self.pending_action}.")
                except Exception as e:
                    logging.error(f"Error sending confirmation for {self.pending_action}: {e}")
            # elif not is_hovering and self.confirmation_sent:
                 # If we want to reset confirmation on mouse out, do it here.
                 # self.confirmation_sent = False

        hover_state_changed = (last_confirmation_state != self.confirmation_sent)

        timeout_triggered = False
        if self.current_state == "visible" and not self.confirmation_sent and self.show_time > 0:
            elapsed = time.monotonic() - self.show_time
            if elapsed > self.auto_hide_after_sec:
                logging.debug(f"Action confirm timeout ({self.auto_hide_after_sec}s) reached for {self.pending_action}. Hiding.")
                target_state = "hidden"
                self.current_state = "hidden"
                self.pending_action = None
                self.confirmation_sent = False
                self.show_time = 0
                state_actually_changed = True
                timeout_triggered = True

        if (state_actually_changed or needs_redraw or action_changed or hover_state_changed) and self.root and not self._stop_event.is_set():
             if position_needs_update and self.initial_pos:
                 self._position_window(self.initial_pos)
             self._draw_icon(self.confirmation_sent)
             if self.current_state == "hidden":
                 if self.root.winfo_viewable(): self.root.withdraw()
             else:
                 if not self.root.winfo_viewable(): self.root.deiconify()

        if not self._stop_event.is_set() and self.root:
             try: self.root.after(50, self._check_queue)
             except tk.TclError: logging.warning("ActionConfirm root destroyed before rescheduling.")
             except Exception as e: logging.error(f"Error rescheduling ActionConfirm check: {e}")

    def _cleanup_tk(self):
        logging.debug("Executing ActionConfirm _cleanup_tk.")
        if self.root:
            try: self.root.destroy()
            except Exception as e: logging.warning(f"Error destroying ActionConfirm root: {e}")
            finally: self.root = None

    def _position_window(self, pos):
        if self.root and not self._stop_event.is_set() and pos:
            try:
                x, y = pos
                offset_x = 25 # Slightly more to the right
                offset_y = -self.canvas_height - 10 # Slightly more above
                new_x = x + offset_x; new_y = y + offset_y
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                if new_x + self.canvas_width > screen_width: new_x = screen_width - self.canvas_width
                if new_x < 0: new_x = 0
                if new_y < 0: new_y = 0
                if new_y + self.canvas_height > screen_height: new_y = screen_height - self.canvas_height
                self.root.geometry(f"+{new_x}+{new_y}")
            except Exception as e: logging.warning(f"Failed to position ActionConfirm: {e}")

    def _draw_icon(self, confirmed):
        if not self.canvas or not self.root or self._stop_event.is_set(): return
        try:
            self.canvas.delete("all")
            if self.current_state == "hidden" or not self.pending_action:
                return
            bg = self.icon_hover_bg if confirmed else self.icon_bg
            padding = 2
            self.canvas.create_rectangle(padding, padding,
                                         self.canvas_width - padding, self.canvas_height - padding,
                                         fill=bg, outline=self.icon_border, width=1, tags="background")
            display_text = self.pending_action
            if display_text == "Enter": display_text = "Entrée"
            elif display_text == "Escape": display_text = "Échap"
            # Add more abbreviations? Maybe keep it short?
            # display_text = display_text[:5] + '...' if len(display_text) > 8 else display_text

            self.canvas.create_text(self.canvas_width / 2, self.canvas_height / 2,
                                      text=f"[{display_text}]",
                                      font=self.font, fill=self.icon_text_color,
                                      anchor=tk.CENTER)
        except tk.TclError as e: logging.warning(f"Error drawing action confirm icon: {e}"); self._stop_event.set()
        except Exception as e: logging.error(f"Unexpected error drawing action confirm icon: {e}", exc_info=True)

    def _is_point_over_widget(self, point_x, point_y, widget):
        if not widget or not widget.winfo_exists(): return False
        try:
            widget_x = widget.winfo_rootx(); widget_y = widget.winfo_rooty()
            widget_width = widget.winfo_width(); widget_height = widget.winfo_height()
            return (widget_x <= point_x < widget_x + widget_width and
                    widget_y <= point_y < widget_y + widget_height)
        except tk.TclError: return False
        except Exception as e: logging.error(f"Error checking hover for widget: {e}", exc_info=True); return False
