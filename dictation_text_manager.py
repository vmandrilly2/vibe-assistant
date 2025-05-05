# dictation_text_manager.py
import asyncio
import logging
import re
from typing import List, Tuple, Dict, Set

# Assuming access to GVM and i18n functions
# from global_variables_manager import GlobalVariablesManager
from i18n import get_action_keywords, get_replacements, _
from constants import (
    STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE,
    STATE_SESSION_RECOGNIZED_ACTIONS_TEMPLATE,
    STATE_OUTPUT_TYPING_QUEUE,
    STATE_SESSION_HISTORY_TEMPLATE, # Needed for context?
    CONFIG_MODULES_PREFIX,
    STATE_INPUT_DICTATION_KEY_PRESSED,
    STATE_OUTPUT_ACTION_QUEUE
)

logger = logging.getLogger(__name__)

class ActionDetector:
    """Detects action keywords within transcribed text segments."""

    def __init__(self, gvm):
        self.gvm = gvm
        self.action_keywords: Dict[str, Dict[str, Set[str]]] = {} # lang -> {action: {keywords}}
        self.replacements: Dict[str, Dict[str, str]] = {} # lang -> {word: replacement}
        self._current_lang = "en" # Default, updated from GVM

    async def load_language_data(self):
        """Loads keywords and replacements for the current language from GVM config."""
        self._current_lang = await self.gvm.get("config.general.source_language", "en-US")
        # Normalize lang code if needed (e.g., 'en-US' -> 'en') for keyword/replacement lookup
        base_lang = self._current_lang.split('-')[0].lower()
        
        # Use i18n helper functions (assuming they handle loading/caching)
        self.action_keywords = get_action_keywords(base_lang)
        self.replacements = get_replacements(base_lang)
        logger.info(f"ActionDetector loaded language data for: {base_lang}")
        # logger.debug(f"Loaded Actions: {self.action_keywords}")
        # logger.debug(f"Loaded Replacements: {self.replacements}")

    async def detect_and_extract(self, text: str, session_id: str) -> Tuple[str, List[str]]:
        """Detects actions in text, updates GVM state, returns remaining text and detected actions."""
        if not text:
            return "", []

        await self.load_language_data() # Ensure keywords are up-to-date
        
        detected_actions = []
        remaining_text = text.strip()
        words = remaining_text.lower().split() # Process in lowercase

        if not words:
             return "", []

        # Check from the end of the phrase for actions
        # Simple check: look if the last word(s) match any action keyword
        processed_words = list(words)
        
        # TODO: Enhance logic to handle multi-word keywords and find best match
        last_word = processed_words[-1]
        found_action = None
        
        for action, keywords in self.action_keywords.items():
            if last_word in keywords:
                 # Basic match on last word
                 found_action = action
                 logger.info(f"Detected action '{found_action}' based on last word '{last_word}'")
                 # Remove the action word from the list
                 processed_words.pop()
                 remaining_text = " ".join(processed_words).strip()
                 detected_actions.append(found_action)
                 # TODO: Handle multiple actions? For now, assumes one action at the end.
                 break # Stop after first match (from end)
        
        if detected_actions:
            # Update GVM state with the detected actions
            action_state_key = STATE_SESSION_RECOGNIZED_ACTIONS_TEMPLATE.format(session_id=session_id)
            # Append new actions to existing list in GVM state
            current_actions = await self.gvm.get(action_state_key, [])
            current_actions.extend(detected_actions)
            await self.gvm.set(action_state_key, current_actions)
            logger.debug(f"Updated GVM state '{action_state_key}' with actions: {detected_actions}")

        # --- Apply Replacements (on remaining text) --- >
        # Simple word-for-word replacement for now
        final_words = []
        for word in remaining_text.split(): # Split again after potential action removal
             replacement = self.replacements.get(word.lower()) 
             final_words.append(replacement if replacement is not None else word)
        final_text = " ".join(final_words)
        # logger.debug(f"Text after replacements: '{final_text}'")
        # < --- End Replacements --- 

        return final_text, detected_actions

class DictationTextManager:
    """Processes final text transcripts, handles action detection, and queues text for typing."""

    def __init__(self, gvm):
        self.gvm = gvm
        self.action_detector = ActionDetector(gvm)
        self._last_processed_segment: Dict[str, str] = {} # session_id -> last segment text
        self._stop_event = asyncio.Event()

    async def init(self):
        """Initialize the manager."""
        logger.info("DictationTextManager initialized.")
        # Pre-load initial language data for action detector?
        # await self.action_detector.load_language_data()
        return True

    async def _process_final_segment(self, session_id: str, segment: str):
        """Processes a newly received final transcript segment."""
        if not segment or self._last_processed_segment.get(session_id) == segment:
            # Avoid processing empty or duplicate segments
            return
            
        logger.info(f"Processing final segment for session {session_id}: '{segment}'")
        self._last_processed_segment[session_id] = segment
        
        text_to_type = segment
        detected_actions = [] # List of action strings

        # Check if action detection is enabled in config
        action_detection_enabled = await self.gvm.get(f"{CONFIG_MODULES_PREFIX}.action_detection_enabled", True)

        if action_detection_enabled:
            logger.debug(f"Action detection enabled for session {session_id}. Running detector...")
            text_to_type, detected_actions = await self.action_detector.detect_and_extract(segment, session_id)
            logger.debug(f"Action detector result: Remaining text='{text_to_type}', Actions={detected_actions}")
        else:
            logger.debug(f"Action detection disabled for session {session_id}.")
            # Apply replacements even if action detection is off?
            # TODO: Decide if replacements should run independently
            pass 

        if text_to_type:
            # Queue the remaining text for typing
            typing_queue_key = STATE_OUTPUT_TYPING_QUEUE
            current_queue = await self.gvm.get(typing_queue_key, [])
            current_queue.append(text_to_type + " ") # Modify the fetched list
            
            # Log BEFORE setting
            logger.debug(f"Added text '{text_to_type} ' to GVM state '{typing_queue_key}'") 
            
            # Set the MODIFIED list back - MAKE A COPY to trigger change detection
            await self.gvm.set(typing_queue_key, current_queue.copy()) # <--- Use .copy()
            
            # Optional: Update session history for deletion logic
            history_key = STATE_SESSION_HISTORY_TEMPLATE.format(session_id=session_id)
            current_history = await self.gvm.get(history_key, [])
            current_history.append(text_to_type) # Store word/segment typed
            await self.gvm.set(history_key, current_history)

        if detected_actions:
            # Queue the detected actions with session context
            action_queue_key = STATE_OUTPUT_ACTION_QUEUE
            current_action_queue = await self.gvm.get(action_queue_key, [])
            for action_str in detected_actions:
                 action_tuple = (session_id, action_str)
                 current_action_queue.append(action_tuple)
                 logger.debug(f"Added action {action_tuple} to GVM state '{action_queue_key}'")
            await self.gvm.set(action_queue_key, current_action_queue)

    async def run_loop(self):
        """Monitors the active session for new final transcript segments and processes them."""
        logger.info("DictationTextManager run_loop starting.")
        self._stop_event.clear()
        current_session_id = None
        last_segment_processed = None

        while not self._stop_event.is_set():
            try:
                if not current_session_id:
                    # Wait for a session to become active (key pressed)
                    logger.debug("DTM waiting for dictation key press...")
                    # Wait until the dictation key is pressed
                    await self.gvm.wait_for_value("input.dictation_key_pressed", True)
                    if self._stop_event.is_set(): break # Exit if stopped while waiting

                    logger.debug("DTM detected key press. Waiting for session ID...")

                    # Wait for the STT manager to set the session ID for this activation
                    session_id = None
                    session_id_key = "app.current_stt_session_id"
                    
                    # Get initial value AFTER key press confirmed
                    current_session_id = await self.gvm.get(session_id_key, None) 
                    
                    # Wait for a change OR for it to become non-None if it was None initially
                    if not current_session_id:
                        logger.debug(f"Current session ID is None, waiting for it to be set on key '{session_id_key}'...")
                        # Wait for the *change* event.
                        await self.gvm.wait_for_change(session_id_key) # Just wait for the event
                        session_id = await self.gvm.get(session_id_key, None) # Explicitly get the value AFTER change
                        logger.debug(f"Detected session ID set after wait: {session_id}") # Add log
                    else:
                         # If already set, we MIGHT have caught the state just after STTManager set it.
                         # However, it could also be a *stale* ID from a previous run if GVM wasn't cleared.
                         # To be safer, let's wait for the *next* change to ensure we get the ID for *this* activation.
                         # This assumes STTManager reliably sets it *after* InputManager sets the key press state.
                         logger.debug(f"Session ID already set to '{current_session_id}', waiting for potential change indicating new session...")
                         
                         # Wait for either a change in session ID or key release
                         wait_session_change = asyncio.create_task(self.gvm.wait_for_change(session_id_key))
                         wait_key_release = asyncio.create_task(self.gvm.wait_for_value("input.dictation_key_pressed", False))
                         
                         done, pending = await asyncio.wait(
                             [wait_session_change, wait_key_release], 
                             return_when=asyncio.FIRST_COMPLETED
                         )

                         # Cancel the pending task
                         for task in pending:
                             task.cancel()
                             try: await task # Allow cancellation
                             except asyncio.CancelledError: pass

                         if wait_key_release in done:
                             logger.warning("Key released while waiting for new session ID. Aborting monitoring for this press.")
                             continue # Go back to waiting for key press

                         # If session change task completed:
                         session_id = await self.gvm.get(session_id_key) # Get the newly set value
                         logger.debug(f"Detected session ID change, new ID: {session_id}")
                         if not session_id: # Should not happen if wait_for_change returned a value
                              logger.error("Session ID changed but new value is None/empty. Aborting.")
                              continue


                if not session_id:
                     logger.error("Failed to get a valid session ID after key press. Skipping monitoring.")
                     # Wait for key release before trying again to avoid tight loop if something is wrong
                     await self.gvm.wait_for_value("input.dictation_key_pressed", False)
                     continue

                logger.info(f"DTM now monitoring session: {session_id}")
                
                # Define the specific keys to monitor for this session
                final_segment_key = STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE.format(session_id=session_id)
                key_release_key = "input.dictation_key_pressed"
                session_status_key = f"stt.session.{session_id}.status"

                # Ensure initial state for segment is known (likely empty string)
                await self.gvm.set(final_segment_key, await self.gvm.get(final_segment_key, ""))
                
                # Monitor for changes
                wait_segment = asyncio.create_task(self.gvm.wait_for_change(final_segment_key))
                wait_key_release = asyncio.create_task(self.gvm.wait_for_value(key_release_key, False))
                # We can't directly wait for multiple values, so we wait for change on status
                wait_session_status_change = asyncio.create_task(self.gvm.wait_for_change(session_status_key))

                # Loop while monitoring the current session
                monitoring_session = True
                key_released = False # Track if key has been released
                
                while monitoring_session and not self._stop_event.is_set():
                    
                    # Determine which tasks to wait for
                    tasks_to_wait = [wait_segment]
                    if not key_released:
                        tasks_to_wait.append(wait_key_release)
                    # Always wait for session status change, check value upon completion
                    if wait_session_status_change and not wait_session_status_change.done():
                         tasks_to_wait.append(wait_session_status_change)

                    # Check if any tasks remain
                    if not tasks_to_wait:
                         logger.warning(f"[{session_id}] No valid tasks left to wait for, ending monitoring.")
                         break

                    # --- <<< MODIFICATION START: Added timeout >>> ---
                    done, pending = await asyncio.wait(
                        tasks_to_wait,
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=15.0 # Add a generous timeout to prevent getting stuck indefinitely
                    )
                    # --- <<< MODIFICATION END >>> ---

                    # --- Process completed tasks ---
                    session_ended = False
                    exit_monitoring_loop_immediately = False

                    if not done:
                        # --- <<< MODIFICATION START: Handle timeout >>> ---
                        if key_released or session_end_status in ["disconnected", "error"]:
                             # Timeout occurred *after* a potential end condition was met.
                             # This means the final segment didn't arrive within the grace period.
                             logger.debug(f"[{session_id}] Grace period timeout after key release/session end. Exiting monitoring.")
                             exit_monitoring_loop_immediately = True 
                        else:
                             # General timeout without key release or session end status - less expected
                             logger.warning(f"[{session_id}] Monitoring wait timed out unexpectedly after 15s. Ending monitoring.")
                             exit_monitoring_loop_immediately = True 
                        # --- <<< MODIFICATION END >>> ---
                        
                    if wait_key_release in done:
                         logger.info(f"[{session_id}] DTM detected key release.")
                         key_released = True
                         wait_key_release = None # Task fulfilled
                         # Don't exit yet - start grace period check below

                    if wait_segment in done:
                        final_text = await self.gvm.get(final_segment_key)
                        logger.debug(f"[{session_id}] Detected final segment change: '{final_text}'")
                        await self._process_final_segment(session_id, final_text)
                        # Re-arm the wait for the *next* segment change (or maybe not?)
                        # If the session is already considered ended, we might not need to re-arm.
                        # Let's keep it simple for now and always re-arm.
                        wait_segment = asyncio.create_task(self.gvm.wait_for_change(final_segment_key))

                    if wait_session_status_change in done:
                        try:
                            current_status = await self.gvm.get(session_status_key)
                            logger.debug(f"[{session_id}] Session status changed to: {current_status}")
                            if current_status in ["disconnected", "error"]:
                                 logger.info(f"[{session_id}] DTM detected session end via status '{current_status}'.")
                                 session_ended = True
                                 wait_session_status_change = None # Terminal state, stop waiting for status
                                 # Don't exit yet - start grace period check below
                            else:
                                 logger.debug(f"[{session_id}] Session status changed to non-terminal state '{current_status}', re-arming wait.")
                                 wait_session_status_change = asyncio.create_task(self.gvm.wait_for_change(session_status_key))
                        except Exception as e:
                             logger.error(f"[{session_id}] Error checking session status after change: {e}")
                             exit_monitoring_loop_immediately = True

                    # --- Exit Condition Check ---
                    # Exit immediately if stop event is set or a timeout/error occurred unexpectedly
                    if self._stop_event.is_set() or exit_monitoring_loop_immediately:
                         logger.debug(f"[{session_id}] Exiting monitoring loop immediately (stop={self._stop_event.is_set()}, immediate_exit={exit_monitoring_loop_immediately})")
                         monitoring_session = False
                    
                    # If key released or session ended, start/continue grace period for final segment
                    elif key_released or session_ended:
                         logger.debug(f"[{session_id}] Key released or session ended. Entering/continuing 2s grace period for final segment...")
                         grace_tasks = [wait_segment] if wait_segment and not wait_segment.done() else []
                         if not grace_tasks:
                              logger.debug(f"[{session_id}] No final segment task to wait for during grace period. Exiting.")
                              monitoring_session = False
                         else:
                              done_grace, pending_grace = await asyncio.wait(
                                   grace_tasks, 
                                   timeout=2.0, # Short grace period
                                   return_when=asyncio.FIRST_COMPLETED
                              )
                              if wait_segment in done_grace:
                                   final_text = await self.gvm.get(final_segment_key)
                                   logger.info(f"[{session_id}] Final segment received during grace period: '{final_text}'")
                                   await self._process_final_segment(session_id, final_text)
                                   # No need to re-arm segment wait here
                              else:
                                   logger.debug(f"[{session_id}] Grace period ended. No further final segment received.")
                                   # Cancel potentially pending wait_segment from grace period
                                   for task in pending_grace: task.cancel()
                              
                              monitoring_session = False # Exit after grace period regardless of outcome
                              
                    # --- Cancel remaining pending tasks if loop is exiting --- 
                    if not monitoring_session:
                        logger.debug(f"[{session_id}] Cleaning up monitoring tasks before exiting inner loop (monitoring={monitoring_session}).")
                        # Combine original pending tasks and any still-active tasks
                        all_pending_tasks = list(pending) 
                        if wait_segment and not wait_segment.done(): all_pending_tasks.append(wait_segment)
                        if wait_key_release and not wait_key_release.done(): all_pending_tasks.append(wait_key_release)
                        if wait_session_status_change and not wait_session_status_change.done(): all_pending_tasks.append(wait_session_status_change)
                        
                        cancelled_tasks = []
                        for task in all_pending_tasks:
                             if task and not task.done():
                                  task.cancel()
                                  cancelled_tasks.append(task)
                        if cancelled_tasks:
                             logger.debug(f"[{session_id}] Awaiting cancellation of {len(cancelled_tasks)} tasks...")
                             try: 
                                  await asyncio.gather(*cancelled_tasks, return_exceptions=True)
                             except asyncio.CancelledError: 
                                  pass # Expected
                             logger.debug(f"[{session_id}] Task cancellation complete.")
                        break # Ensure exit from while loop

                # After exiting the inner monitoring loop
                logger.info(f"DTM finished monitoring session: {session_id}")
                session_id = None # Reset session ID so the outer loop waits for next press
                self._last_processed_segment.pop(session_id, None) # Clean up last segment cache

            except asyncio.CancelledError:
                logger.info("DictationTextManager run_loop cancelled.")
                break
            except Exception as e:
                 logger.error(f"Error in DictationTextManager run_loop: {e}", exc_info=True)
                 current_session_id = None # Reset session on error
                 await asyncio.sleep(1) # Wait longer on error
        
        logger.info("DictationTextManager run_loop finished.")

    async def cleanup(self):
        """Cleans up the manager."""
        logger.info("DictationTextManager cleaning up...")
        self._stop_event.set()
        logger.info("DictationTextManager cleanup finished.") 