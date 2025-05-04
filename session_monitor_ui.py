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
            headers = ["Slot", "ID", "State", "Processing", "StopReq", "Buffered", "Timeouts", "ActiveProc", "MicOn"]
            for col, header in enumerate(headers):
                tk.Label(main_frame, text=header, font=("Segoe UI", 9, "bold")).grid(row=0, column=col, padx=3, pady=2, sticky="w")

            # Data Rows (placeholders)
            self.labels = {}
            for i in range(self.max_sessions):
                slot_num = i + 1
                self.labels[slot_num] = {}
                tk.Label(main_frame, text=f"Slot {slot_num}:").grid(row=slot_num, column=0, padx=3, sticky="w")
                self.labels[slot_num]["id"] = tk.Label(main_frame, text="-", anchor="w", justify="left", width=15)
                self.labels[slot_num]["id"].grid(row=slot_num, column=1, padx=3, sticky="w")
                self.labels[slot_num]["state"] = tk.Label(main_frame, text="Idle", anchor="w", width=15)
                self.labels[slot_num]["state"].grid(row=slot_num, column=2, padx=3, sticky="w")
                self.labels[slot_num]["processing"] = tk.Label(main_frame, text="-", anchor="w", width=5)
                self.labels[slot_num]["processing"].grid(row=slot_num, column=3, padx=3, sticky="w")
                self.labels[slot_num]["stop_req"] = tk.Label(main_frame, text="-", anchor="w", width=5)
                self.labels[slot_num]["stop_req"].grid(row=slot_num, column=4, padx=3, sticky="w")
                self.labels[slot_num]["buffered"] = tk.Label(main_frame, text="-", anchor="w", width=5)
                self.labels[slot_num]["buffered"].grid(row=slot_num, column=5, padx=3, sticky="w")
                self.labels[slot_num]["timeouts"] = tk.Label(main_frame, text="-", anchor="w", width=5)
                self.labels[slot_num]["timeouts"].grid(row=slot_num, column=6, padx=3, sticky="w")
                # --- NEW Columns ---
                self.labels[slot_num]["active_proc"] = tk.Label(main_frame, text="-", anchor="w", width=5)
                self.labels[slot_num]["active_proc"].grid(row=slot_num, column=7, padx=3, sticky="w")
                self.labels[slot_num]["mic_on"] = tk.Label(main_frame, text="-", anchor="w", width=5)
                self.labels[slot_num]["mic_on"].grid(row=slot_num, column=8, padx=3, sticky="w")
                # --- END NEW ---

            main_frame.grid_columnconfigure(1, weight=1) # Allow ID column to expand
            main_frame.grid_columnconfigure(2, weight=1) # Allow State column to expand

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
            self.root.after(100, self._check_queue) # Update display fairly frequently

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
        id_to_slot = {sid: i + 1 for i, sid in enumerate(active_sessions.keys())}
        
        # Add waiting IDs to the map if not already there (assign temporary slots)
        next_slot = len(id_to_slot) + 1
        for wid in waiting_ids:
            if wid not in id_to_slot:
                 id_to_slot[wid] = next_slot
                 next_slot += 1
                 if next_slot > self.max_sessions: break # Don't display more than max

        # Update labels for each slot
        for slot_num in range(1, self.max_sessions + 1):
            session_id_for_slot = None
            session_data = None

            # Find which session ID belongs to this slot
            for sid, s_num in id_to_slot.items():
                 if s_num == slot_num:
                      session_id_for_slot = sid
                      session_data = active_sessions.get(sid) # Get data if it's an active session
                      break
            
            if session_id_for_slot and session_data:
                # Active session found for this slot
                state = "Active" # Base state
                if session_id_for_slot == processing_id:
                    state = "Processing"
                elif session_id_for_slot in waiting_ids:
                    state = "Waiting"
                
                if session_data.get('stop_requested'): state += " (StopReq)"
                if session_data.get('processing_complete'): state = "Complete"

                # Extract data safely
                id_text = str(session_id_for_slot) # Use full ID for now
                state_text = state
                processing_text = "Y" if session_data.get('is_processing_allowed') else "N"
                stop_req_text = "Y" if session_data.get('stop_requested') else "N"
                buffered_text = str(len(session_data.get('buffered_transcripts', [])))

                # --- NEW: Get timeout count --- >
                timeout_text = str(session_data.get('timeout_count', 0))
                # --- END NEW ---

                # --- NEW: Get Monitor Flags --- >
                active_proc_text = "Y" if session_data.get('is_active_processor') else "N"
                mic_on_text = "Y" if session_data.get('is_microphone_active') else "N"
                # --- END NEW ---

                # --- ADD LOGGING ---
                logging.debug(f"_update_display: Slot {slot_num} (ID: {session_id_for_slot}), State: {state_text}, MicFlag: {session_data.get('is_microphone_active')}, Setting MicLabel: {mic_on_text}")
                # --- END LOGGING ---

                self.labels[slot_num]["id"].config(text=id_text)
                self.labels[slot_num]["state"].config(text=state_text)
                self.labels[slot_num]["processing"].config(text=processing_text)
                self.labels[slot_num]["stop_req"].config(text=stop_req_text)
                self.labels[slot_num]["buffered"].config(text=buffered_text)
                # --- NEW: Update timeout label --- >
                self.labels[slot_num]["timeouts"].config(text=timeout_text)
                # --- END NEW ---

                # --- NEW: Update Monitor Flags --- >
                self.labels[slot_num]["active_proc"].config(text=active_proc_text)
                self.labels[slot_num]["mic_on"].config(text=mic_on_text)
                # --- END NEW ---

            elif session_id_for_slot in waiting_ids: # Session is waiting (might not have full data)
                 self.labels[slot_num]["id"].config(text=str(session_id_for_slot))
                 self.labels[slot_num]["state"].config(text="Waiting (Init)")
                 self.labels[slot_num]["processing"].config(text="-")
                 self.labels[slot_num]["stop_req"].config(text="-")
                 self.labels[slot_num]["buffered"].config(text="-")
                 self.labels[slot_num]["timeouts"].config(text="-")
                 self.labels[slot_num]["active_proc"].config(text="-")
                 # Keep mic_on as potentially last known state or clear? Let's clear for waiting.
                 self.labels[slot_num]["mic_on"].config(text="-")

            else:
                # Slot is empty
                self.labels[slot_num]["id"].config(text="-")
                self.labels[slot_num]["state"].config(text="Idle")
                self.labels[slot_num]["processing"].config(text="-")
                self.labels[slot_num]["stop_req"].config(text="-")
                self.labels[slot_num]["buffered"].config(text="-")
                # --- NEW: Clear timeout for idle --- >
                self.labels[slot_num]["timeouts"].config(text="-")
                # --- END NEW ---

                # --- NEW: Clear Monitor Flags for idle --- >
                self.labels[slot_num]["active_proc"].config(text="-")
                self.labels[slot_num]["mic_on"].config(text="-")
                # --- END NEW ---

        # --- NEW: Update global stats --- >
        # Successful Stops
        self.labels['global']['successful_stops'].config(text=str(total_stops))
        # Min Duration
        min_dur_text = "N/A"
        if min_duration is not None:
            min_dur_text = f"{min_duration * 1000:.1f} ms"
        self.labels['global']['min_stop_duration'].config(text=min_dur_text)
        # Max Duration
        max_duration_text = f"{max_duration:.3f}s" if max_duration is not None else "N/A"
        self.labels['global']['max_stop_duration'].config(text=max_duration_text)
        # Final Missed
        self.labels['global']['final_missed'].config(text=str(final_missed_count))
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