import tkinter as tk
import tkinter.ttk as ttk # For Treeview/Table if needed
import threading
import queue
import logging
import time

# Assuming MAX_CONCURRENT_SESSIONS is accessible or passed
# from constants import MAX_CONCURRENT_SESSIONS # Or pass it in init

class SessionMonitor:
    """Manages a Tkinter window to display real-time STT session states."""

    def __init__(self, monitor_q: queue.Queue, max_sessions: int):
        """
        Args:
            monitor_q: Queue for receiving session state updates.
            max_sessions: The maximum number of concurrent sessions configured.
        """
        self.queue = monitor_q
        self.max_sessions = max_sessions
        self.root = None
        self.labels = {} # Dictionary to hold labels for session data {slot_index: {label_key: widget}}
        self.thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._stop_event = threading.Event()
        self._tk_ready = threading.Event()
        self.last_state = {} # Store last received state to update UI efficiently
        self.last_displayed_values = {} # Store last text set for each label {slot_num: {label_key: text}}
        self.headers = [] # Added to store headers

        logging.info("SessionMonitor initialized.")

    def start(self):
        self.thread.start()
        self._tk_ready.wait(timeout=2.0)
        if not self._tk_ready.is_set():
            logging.warning("SessionMonitor Tkinter thread did not become ready.")

    def stop(self):
        logging.debug("Stop requested for SessionMonitor.")
        self._stop_event.set()
        try:
            # Send a dummy message to wake up the queue check
            self.queue.put_nowait(("stop", None))
        except queue.Full:
            logging.warning("SessionMonitor queue full when sending stop command.")

    def _run_tkinter(self):
        logging.info("SessionMonitor thread started.")
        try:
            self.root = tk.Tk()
            self.root.title("STT Session Monitor")
            self.root.geometry("600x280+10+10") # Increased size
            self.root.wm_attributes("-topmost", True) # Keep on top

            # --- Create UI Elements (Labels for each slot) ---
            # Frame for better organization
            main_frame = tk.Frame(self.root, padx=5, pady=5)
            main_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

            # Header Row
            self.headers = [
                "Slot", "ID", "State", "StopReq", "Buffered", # Core Info
                "SessionTime", "ButtonTime", "MicTime", "DGConnTime", # Durations
                "ConnLatency", "Timeouts", "Buffer(ms)", "WaitProcTime" # Specific Metrics
            ]
            column_widths = {
                "ID": 12, "State": 10, "StopReq": 4, "Buffered": 4,
                "SessionTime": 9, "ButtonTime": 9, "MicTime": 9, "DGConnTime": 9,
                "ConnLatency": 9, "Timeouts": 4, "Buffer(ms)": 7, "WaitProcTime": 9
            }
            self.labels = {}
            for col, header in enumerate(self.headers):
                # Use custom widths if specified, otherwise default
                width = column_widths.get(header, 8) # Default width 8 if not specified
                tk.Label(main_frame, text=header, font=("Segoe UI", 9, "bold")).grid(row=0, column=col, padx=2, pady=2, sticky="w")

            # Data Rows (placeholders)
            for i in range(self.max_sessions):
                slot_num = i + 1
                self.labels[slot_num] = {}
                # Slot number label
                tk.Label(main_frame, text=f"Slot {slot_num}:").grid(row=slot_num, column=0, padx=2, sticky="w")

                # Create labels for each data column based on headers[1:]
                for col, header in enumerate(self.headers[1:], start=1):
                    label_key = header.lower().replace("(", "").replace(")", "") # Generate key like 'id', 'sessiontime'
                    width = column_widths.get(header, 8)
                    label_widget = tk.Label(main_frame, text="-", anchor="w", justify="left", width=width)
                    label_widget.grid(row=slot_num, column=col, padx=2, sticky="w")
                    self.labels[slot_num][label_key] = label_widget

            # Configure column weights (optional, for resizing)
            main_frame.grid_columnconfigure(1, weight=1) # Allow ID column to expand slightly
            main_frame.grid_columnconfigure(2, weight=1) # Allow State column to expand slightly

            # --- NEW: Global Stats Frame --- >
            stats_frame = tk.Frame(self.root, padx=5, pady=5)
            stats_frame.pack(fill=tk.X, side=tk.BOTTOM, anchor="sw")

            # Use grid for the separator, spanning 2 columns
            ttk.Separator(stats_frame, orient='horizontal').grid(row=0, column=0, columnspan=2, sticky='ew', pady=5)

            # Create labels within the stats frame
            self.labels['global'] = {}
            stats_font = ("Segoe UI", 9)

            success_label = tk.Label(stats_frame, text="Successful Stops:", font=stats_font)
            success_label.grid(row=1, column=0, sticky="w")
            self.labels['global']['successful_stops'] = tk.Label(stats_frame, text="0", font=stats_font, anchor="w")
            self.labels['global']['successful_stops'].grid(row=1, column=1, sticky="w", padx=5)

            min_dur_label = tk.Label(stats_frame, text="Min Stop Duration:", font=stats_font)
            min_dur_label.grid(row=2, column=0, sticky="w")
            self.labels['global']['min_stop_duration'] = tk.Label(stats_frame, text="N/A", font=stats_font, anchor="w")
            self.labels['global']['min_stop_duration'].grid(row=2, column=1, sticky="w", padx=5)

            max_dur_label = tk.Label(stats_frame, text="Max Stop Duration:", font=stats_font)
            max_dur_label.grid(row=3, column=0, sticky="w")
            self.labels['global']['max_stop_duration'] = tk.Label(stats_frame, text="N/A", font=stats_font, anchor="w")
            self.labels['global']['max_stop_duration'].grid(row=3, column=1, sticky="w", padx=5)

            missed_label = tk.Label(stats_frame, text="Stops (Final Missed):", font=stats_font)
            missed_label.grid(row=4, column=0, sticky="w")
            self.labels['global']['final_missed'] = tk.Label(stats_frame, text="0", font=stats_font, anchor="w")
            self.labels['global']['final_missed'].grid(row=4, column=1, sticky="w", padx=5)

            self._tk_ready.set()
            logging.debug("SessionMonitor Tkinter objects created.")
            self._check_queue() # Start the queue check / redraw loop
            self.root.mainloop()
            logging.debug("SessionMonitor mainloop finished.")

        except Exception as e:
            logging.error(f"Error during SessionMonitor mainloop/setup: {e}", exc_info=True)
            self._tk_ready.set() # Signal ready even on error
        finally:
            logging.info("SessionMonitor thread finishing.")
            self._stop_event.set()

    def _check_queue(self):
        """Processes messages from the monitor queue."""
        if self._stop_event.is_set():
            self._cleanup_tk()
            return

        try:
            while not self.queue.empty():
                command, data = self.queue.get_nowait()
                if command == "update_state":
                    self.last_state = data # Store the latest full state snapshot
                    self._update_display()
                elif command == "stop":
                    logging.debug("Received stop command in SessionMonitor queue.")
                    self._stop_event.set()
                    # Cleanup will happen at the start of the next check
        except queue.Empty:
            pass
        except tk.TclError as e:
            if "application has been destroyed" not in str(e):
                logging.warning(f"SessionMonitor Tkinter error processing queue: {e}.")
            self._stop_event.set() # Ensure cleanup happens
        except Exception as e:
            logging.error(f"Error processing SessionMonitor queue: {e}", exc_info=True)
            self._stop_event.set() # Ensure cleanup happens

        # Reschedule
        if self.root and not self._stop_event.is_set():
            # --- ALWAYS call _update_display after queue check to refresh timers --- >
            if self.last_state: # Only update if we have some state
                try:
                    self._update_display()
                except tk.TclError as e:
                    if "application has been destroyed" not in str(e):
                        logging.warning(f"SessionMonitor Tkinter error during periodic update: {e}.")
                    self._stop_event.set() # Stop if TK error
                except Exception as e:
                    logging.error(f"Error during periodic SessionMonitor update: {e}", exc_info=True)
                    self._stop_event.set() # Stop on other errors too

            self.root.after(500, self._check_queue) # Reschedule queue check (Reduced frequency further)

    def _update_display(self):
        """Updates the labels based on the last received state."""
        if not self.root or not self.root.winfo_exists() or not self.last_state:
            return

        active_sessions = self.last_state.get('active_sessions', {})
        processing_id = self.last_state.get('processing_id')
        waiting_ids = self.last_state.get('waiting_ids', [])
        # --- NEW: Get global stats --- >
        total_stops = self.last_state.get('total_successful_stops', 0)
        min_duration = self.last_state.get('min_stop_duration') # Can be None
        max_duration = self.last_state.get('max_stop_duration') # Can be None
        final_missed_count = self.last_state.get('total_stops_final_missed', 0) # NEW
        # --- END NEW ---

        # Create a mapping of activation_id to slot for easier lookup (can be optimized)
        # --- MODIFIED: Use a list of active/waiting IDs in display order --- >
        # Get active sessions sorted by creation time (approximated by dict order for now)
        sorted_active_ids = sorted(active_sessions.keys(), key=lambda x: active_sessions[x].get('creation_time', 0))
        # Get waiting IDs (already sorted by creation time in backend)
        waiting_ids_list = list(waiting_ids) # Use the list directly
        # Combine, ensuring no duplicates, maintaining order
        display_order_ids = []
        seen_ids = set()
        for sid in sorted_active_ids + waiting_ids_list:
            if sid not in seen_ids:
                display_order_ids.append(sid)
                seen_ids.add(sid)

        id_to_slot = {sid: i + 1 for i, sid in enumerate(display_order_ids[:self.max_sessions])}
        # --- END MODIFIED --- >

        # Update labels for each slot
        current_monotonic_time = time.monotonic()

        # --- Helper function for efficient label updates --- >
        def update_label_if_changed(slot, label_key, new_text):
            # Ensure the slot exists in the tracking dictionaries
            if slot not in self.labels: self.labels[slot] = {}
            if slot not in self.last_displayed_values: self.last_displayed_values[slot] = {}

            last_text = self.last_displayed_values[slot].get(label_key)
            if new_text != last_text:
                try:
                    self.labels[slot][label_key].config(text=new_text)
                    self.last_displayed_values[slot][label_key] = new_text
                except KeyError:
                    # logging.debug(f"SessionMonitor: Label key '{label_key}' not found for slot {slot}.")
                    pass # Ignore if label doesn't exist (e.g., during setup)
                except tk.TclError as e:
                    if "invalid command name" not in str(e):
                        logging.warning(f"SessionMonitor: TclError updating label {slot}-{label_key}: {e}")
                    # Handle cases where the label widget might be destroyed
                    pass
                except Exception as e:
                    logging.error(f"SessionMonitor: Error updating label {slot}-{label_key}: {e}", exc_info=True)
        # --- END Helper Function --- >

        for slot_num in range(1, self.max_sessions + 1):
            session_id_for_slot = None
            session_data = None

            # Find which session ID belongs to this slot
            for sid, s_num in id_to_slot.items():
                 if s_num == slot_num:
                      session_id_for_slot = sid
                      # Get data only if it exists in the active_sessions snapshot
                      session_data = active_sessions.get(sid)
                      break

            if session_id_for_slot and session_data:
                # Active session found for this slot
                creation_time = session_data.get('creation_time')

                # --- Determine State Text (Prioritize Complete) ---
                state_text = "Active" # Default state
                if session_data.get('processing_complete'):
                    state_text = "Complete"
                elif session_id_for_slot == processing_id:
                    state_text = "Processing"
                elif session_id_for_slot in waiting_ids:
                    state_text = "Waiting"
                elif session_data.get('button_released'): # If button released but not yet complete (timeout case)
                    if state_text == "Active":
                         state_text = "Stopping"

                if session_data.get('stop_requested') and state_text != "Complete":
                     state_text += " (StopReq)"

                # --- Determine other text fields ---
                id_text = f"{session_id_for_slot:.1f}" # Shorten ID display
                stop_req_text = "Y" if session_data.get('stop_requested') else "N"
                buffered_text = str(session_data.get('buffered_transcripts_count', 0))
                timeout_text = str(session_data.get('timeout_count', 0))
                buffer_ms_text = str(session_data.get('buffer_duration_ms', '-'))

                # --- Placeholder for Timings --- >
                session_time_text = "-"
                button_time_text = "-"
                mic_time_text = "-"
                dg_conn_time_text = "-"
                conn_latency_text = "-"
                wait_proc_time_text = "-"
                # --- End Placeholder --- >

                # --- NEW: Calculate and Format Timings --- >
                def format_duration(start_time, end_time, current_time):
                    if start_time is None: return "-"
                    # If end_time is set, calculate fixed duration
                    if end_time is not None:
                        duration = end_time - start_time
                        return f"{duration:.1f}s"
                    # If start_time is set but end_time is not, calculate running time
                    else:
                        running_duration = current_time - start_time
                        return f"{running_duration:.1f}s..."

                def format_latency(start_time, end_time):
                    if start_time is None: return "Connecting..." # Indicate attempt in progress
                    # If start_time is set, but end_time is not, it's still connecting
                    if end_time is None:
                        return "Connecting..."
                    duration = end_time - start_time
                    return f"{duration:.1f}s"

                # Session Time
                session_time_text = format_duration(creation_time, session_data.get('session_end_time'), current_monotonic_time)

                # Button Press Time
                button_time_text = format_duration(creation_time, session_data.get('button_release_time'), current_monotonic_time)

                # Mic Time
                mic_time_text = format_duration(session_data.get('mic_start_time'), session_data.get('mic_stop_time'), current_monotonic_time)

                # DG Connection Time
                dg_conn_time_text = format_duration(session_data.get('dg_conn_established_time'), session_data.get('dg_conn_closed_time'), current_monotonic_time)

                # Connection Latency
                conn_latency_text = format_latency(session_data.get('dg_conn_start_attempt_time'), session_data.get('dg_conn_established_time'))

                # Wait Processing Time
                wait_proc_time_text = format_duration(session_data.get('wait_start_time'), session_data.get('wait_end_time'), current_monotonic_time)
                # --- END Timing Calculation --- >

                # Update using the helper function (defined outside loop)
                update_label_if_changed(slot_num, "id", id_text)
                update_label_if_changed(slot_num, "state", state_text)
                update_label_if_changed(slot_num, "stopreq", stop_req_text)
                update_label_if_changed(slot_num, "buffered", buffered_text)
                update_label_if_changed(slot_num, "timeouts", timeout_text)
                update_label_if_changed(slot_num, "sessiontime", session_time_text)
                update_label_if_changed(slot_num, "buttontime", button_time_text)
                update_label_if_changed(slot_num, "mictime", mic_time_text)
                update_label_if_changed(slot_num, "dgconntime", dg_conn_time_text)
                update_label_if_changed(slot_num, "connlatency", conn_latency_text)
                update_label_if_changed(slot_num, "buffer(ms)", buffer_ms_text)
                update_label_if_changed(slot_num, "waitproctime", wait_proc_time_text)

            elif session_id_for_slot: # ID exists (likely waiting) but no full data yet
                # Clear labels for empty slots if they haven't been cleared before
                empty_text = '-' # Use '-' for empty slots
                if slot_num not in self.last_displayed_values: self.last_displayed_values[slot_num] = {} # Ensure dict exists
                # Check if already cleared to avoid redundant config calls
                if self.last_displayed_values[slot_num].get("id") != empty_text:
                    for header in self.headers:
                        if header != "Slot": # Don't clear the slot number itself
                            try:
                                label_key = header.lower().replace(" ", "") # Assuming simple mapping
                                update_label_if_changed(slot_num, label_key, empty_text)
                            except KeyError:
                                pass # Label might not exist
                            except Exception as e:
                                logging.warning(f"Error clearing label {slot_num}-{header}: {e}")

        # --- NEW: Update global stats --- >
        # Initialize global dict if needed
        if 'global' not in self.last_displayed_values: self.last_displayed_values['global'] = {}

        def update_global_label_if_changed(label_key, new_text):
            last_text = self.last_displayed_values['global'].get(label_key)
            if new_text != last_text:
                try:
                    self.labels['global'][label_key].config(text=new_text)
                    self.last_displayed_values['global'][label_key] = new_text
                except KeyError:
                    logging.warning(f"Monitor UI: Global label key '{label_key}' not found.")
                except tk.TclError as e:
                    if "application has been destroyed" not in str(e):
                        logging.warning(f"Monitor UI: TclError updating global {label_key}: {e}")

        # Successful Stops
        update_global_label_if_changed('successful_stops', str(total_stops))
        # Min Duration
        min_dur_text = "N/A"
        if min_duration is not None:
            min_dur_text = f"{min_duration * 1000:.1f} ms"
        update_global_label_if_changed('min_stop_duration', min_dur_text)
        # Max Duration
        max_duration_text = f"{max_duration:.1f}s" if max_duration is not None else "N/A"
        update_global_label_if_changed('max_stop_duration', max_duration_text)
        # Final Missed
        update_global_label_if_changed('final_missed', str(final_missed_count))
        # --- END NEW ---

    def _cleanup_tk(self):
        """Safely destroys the Tkinter window."""
        logging.debug("Executing SessionMonitor _cleanup_tk.")
        if self.root:
            try:
                self.root.destroy()
                logging.info("SessionMonitor root window destroyed.")
                self.root = None
            except Exception as e:
                logging.warning(f"Error destroying SessionMonitor root: {e}")

# Example Usage (If run directly, for testing)
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    test_q = queue.Queue()
    monitor = SessionMonitor(test_q, max_sessions=4)
    monitor.start()

    # Simulate some state updates
    def simulate_updates():
        time.sleep(2)
        print("Sending update 1")
        test_q.put(("update_state", {
            'active_sessions': {
                12345.6: {'is_processing_allowed': True, 'stop_requested': False, 'buffered_transcripts': [], 'processing_complete': False},
                67890.1: {'is_processing_allowed': False, 'stop_requested': False, 'buffered_transcripts': ['a', 'b'], 'processing_complete': False}
            },
            'processing_id': 12345.6,
            'waiting_ids': [67890.1]
        }))
        time.sleep(3)
        print("Sending update 2")
        test_q.put(("update_state", {
            'active_sessions': {
                 67890.1: {'is_processing_allowed': True, 'stop_requested': False, 'buffered_transcripts': [], 'processing_complete': False},
                 99999.9: {'is_processing_allowed': False, 'stop_requested': False, 'buffered_transcripts': ['x'], 'processing_complete': False}
            },
            'processing_id': 67890.1,
            'waiting_ids': [99999.9]
        }))
        time.sleep(3)
        monitor.stop()

    sim_thread = threading.Thread(target=simulate_updates)
    sim_thread.start()

    # Keep main thread alive while monitor runs
    monitor.thread.join()
    print("Monitor test finished.") 