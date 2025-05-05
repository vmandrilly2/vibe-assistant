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
            current_typing_queue = await self.gvm.get(typing_queue_key, [])
            current_typing_queue.append(text_to_type + " ") 
            await self.gvm.set(typing_queue_key, current_typing_queue)
            logger.debug(f"Added text '{text_to_type} ' to GVM state '{typing_queue_key}'")
            
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
                    await self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, True)
                    logger.debug("DTM detected key press. Getting session ID...")
                    current_session_id = await self.gvm.get("app.current_stt_session_id")
                    if not current_session_id:
                        logger.warning("Dictation key pressed, but no active session ID found yet. Retrying wait...")
                        await asyncio.sleep(0.1) # Brief pause before re-checking
                        continue # Restart outer loop to wait for key/session again
                    
                    logger.info(f"DTM now monitoring session: {current_session_id}")
                    last_segment_processed = None # Reset for new session
                    # Clear last processed segment history for this ID if it exists from a previous run
                    if current_session_id in self._last_processed_segment: del self._last_processed_segment[current_session_id]
                
                # --- Inner loop: Monitor active session --- >
                final_segment_key = STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE.format(session_id=current_session_id)
                
                # Create tasks to wait for segment change OR key release
                segment_change_task = asyncio.create_task(self.gvm.wait_for_change(final_segment_key))
                key_release_task = asyncio.create_task(self.gvm.wait_for_value(STATE_INPUT_DICTATION_KEY_PRESSED, False))
                
                # Wait for either event to happen
                done, pending = await asyncio.wait(
                    [segment_change_task, key_release_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel the pending task to avoid resource leaks
                for task in pending:
                    task.cancel()
                    try: await task # Await cancellation
                    except asyncio.CancelledError: pass

                if key_release_task in done:
                    logger.info(f"DTM detected key release. Stopping monitoring for session: {current_session_id}")
                    # Session ended normally
                    current_session_id = None # Go back to waiting for key press
                    continue # Go to top of outer loop

                if segment_change_task in done:
                    # Check for exceptions in the completed task
                    try: await segment_change_task
                    except Exception as e: logger.error(f"Error waiting for segment change: {e}"); continue
                    
                    # New segment detected
                    logger.debug(f"DTM detected new final segment for session {current_session_id}")
                    current_segment = await self.gvm.get(final_segment_key)
                    if current_segment != last_segment_processed:
                         await self._process_final_segment(current_session_id, current_segment)
                         last_segment_processed = current_segment
                    else:
                         logger.debug("Segment change detected, but value is same as last processed. Ignoring.")
                    # Loop back to wait for the *next* segment change or key release
                    continue
                
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