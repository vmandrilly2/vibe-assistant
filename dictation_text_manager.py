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
    CONFIG_MODULES_PREFIX
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
        detected_actions = []

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
            # Add space if needed, assuming ActionExecutor handles final spacing
            current_queue.append(text_to_type + " ") 
            await self.gvm.set(typing_queue_key, current_queue)
            logger.debug(f"Added text '{text_to_type} ' to GVM state '{typing_queue_key}'")
            
            # Optional: Update session history for deletion logic
            history_key = STATE_SESSION_HISTORY_TEMPLATE.format(session_id=session_id)
            current_history = await self.gvm.get(history_key, [])
            current_history.append(text_to_type) # Store word/segment typed
            await self.gvm.set(history_key, current_history)

        # Note: Detected actions are already in GVM state via ActionDetector.
        # The ActionExecutor will monitor that state.

    async def run_loop(self):
        """Monitors GVM state for new final transcript segments and processes them."""
        logger.info("DictationTextManager run_loop starting.")
        self._stop_event.clear()
        # TODO: How to efficiently watch for changes across *all* sessions?
        # Watching individual session segment keys is inefficient if many sessions exist.
        # Option 1: GVM emits a general "new_final_segment" event with session_id/data.
        # Option 2: Periodically scan session states (less ideal).
        # Option 3: Use a dedicated queue in GVM state pushed to by STTManager.
        
        # --- Using Polling (Less Ideal) --- >
        last_check_sessions = {} # Store last seen segment for each session
        while not self._stop_event.is_set():
            try:
                # Get all session data (potentially large!)
                all_sessions_data = await self.gvm.get("sessions", {})
                active_sessions = list(all_sessions_data.keys())
                
                for session_id in active_sessions:
                     final_segment_key = STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE.format(session_id=session_id)
                     current_segment = await self.gvm.get(final_segment_key)
                     last_seen_segment = last_check_sessions.get(session_id)
                     
                     if current_segment and current_segment != last_seen_segment:
                          logger.debug(f"Detected new final segment for session {session_id}")
                          await self._process_final_segment(session_id, current_segment)
                          last_check_sessions[session_id] = current_segment # Update last seen
                          
                # Clean up old sessions from check dict
                current_check_keys = list(last_check_sessions.keys())
                for checked_id in current_check_keys:
                     if checked_id not in active_sessions:
                          del last_check_sessions[checked_id]
                          # Clear last processed segment history too
                          if checked_id in self._last_processed_segment: del self._last_processed_segment[checked_id]
                          
                await asyncio.sleep(0.1) # Poll interval
            except asyncio.CancelledError:
                logger.info("DictationTextManager run_loop cancelled.")
                break
            except Exception as e:
                 logger.error(f"Error in DictationTextManager run_loop: {e}", exc_info=True)
                 await asyncio.sleep(1) # Wait longer on error
        # < --- End Polling --- 
        logger.info("DictationTextManager run_loop finished.")

    async def cleanup(self):
        """Cleans up the manager."""
        logger.info("DictationTextManager cleaning up...")
        self._stop_event.set()
        logger.info("DictationTextManager cleanup finished.") 