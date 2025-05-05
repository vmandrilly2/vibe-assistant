# action_executor.py
import asyncio
import logging
from typing import List, Any, Optional
from pynput import keyboard # Needed for keyboard.Key etc.

# Assuming access to GVM, KeyboardSimulator, OpenAIManager, constants, i18n
# from global_variables_manager import GlobalVariablesManager
# from keyboard_simulator import KeyboardSimulator
# from openai_manager import OpenAIManager
from constants import (
    STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS,
    STATE_OUTPUT_ACTION_QUEUE, # Alternative queue if UI confirmation is off
    STATE_SESSION_HISTORY_TEMPLATE,
    STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE, # For AI query context
    STATE_OUTPUT_TYPING_QUEUE, # For placing translation results
    STATE_UI_AI_RESPONSE_DISPLAY, # For placing AI query results
    PYNPUT_KEY_MAP # Need this from constants for mapping action names like "enter"
)
from i18n import _ # For potential localized action names?

logger = logging.getLogger(__name__)

class ActionExecutor:
    """Executes actions based on recognized commands or confirmed UI choices."""

    def __init__(self, gvm: Any, keyboard_simulator: Any, openai_manager: Any):
        self.gvm = gvm
        self.keyboard_simulator = keyboard_simulator
        self.openai_manager = openai_manager
        self._stop_event = asyncio.Event()
        self._active_task: Optional[asyncio.Task] = None
        # Determine which GVM state key to watch for actions
        # This could be based on config (e.g., action_confirm_enabled)
        self._action_source_key = STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS # Default if UI enabled
        # TODO: Add logic to switch source based on config

    async def init(self):
        """Initializes the executor."""
        action_confirm_enabled = await self.gvm.get("config.modules.action_confirm_enabled", False)
        if action_confirm_enabled:
             self._action_source_key = STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS
             logger.info("ActionExecutor initialized. Watching UI confirmed actions.")
        else:
             # If UI confirmation is off, actions might come directly from detector
             # Need a state key for this, e.g., output.action_queue
             # Let's assume STATE_OUTPUT_ACTION_QUEUE is set by DictationTextManager if UI is off
             self._action_source_key = STATE_OUTPUT_ACTION_QUEUE 
             logger.info("ActionExecutor initialized. Watching direct action queue.")
        # Ensure the initial state is an empty list
        await self.gvm.set(self._action_source_key, []) 
        return True

    async def _execute_action(self, session_id: str, action: str):
        """Determines action type and executes it."""
        logger.info(f"Executing action '{action}' for session {session_id}")
        action_lower = action.lower().strip()
        
        # 1. Simple Key Presses (using PYNPUT_KEY_MAP from constants)
        if action_lower in PYNPUT_KEY_MAP:
            key_obj = PYNPUT_KEY_MAP[action_lower]
            if isinstance(key_obj, (keyboard.Key, keyboard.KeyCode)): # Check if it's a valid key object
                 logger.debug(f"Action '{action}' identified as simple key press: {key_obj}")
                 await self.keyboard_simulator.press_release_key(key_obj)
                 return
            else:
                 # Handle single characters if they exist in the map
                 if isinstance(key_obj, str) and len(key_obj) == 1:
                     logger.debug(f"Action '{action}' identified as single character type: {key_obj}")
                     await self.keyboard_simulator.type_text(key_obj)
                     return
                 else:
                      logger.warning(f"Mapped key for action '{action}' is not a valid Key/KeyCode/Char: {key_obj}")

        # 2. Deletion ("backspace", potentially "delete word", "delete all")
        elif action_lower == "backspace": # Simple backspace for now
            logger.debug(f"Action '{action}' identified as backspace.")
            await self.keyboard_simulator.press_release_key(keyboard.Key.backspace)
            # TODO: Implement word/segment deletion based on session history from GVM
            # history_key = STATE_SESSION_HISTORY_TEMPLATE.format(session_id=session_id)
            # history = await self.gvm.get(history_key, [])
            # if history:
            #     last_typed = history.pop()
            #     num_backspaces = len(last_typed)
            #     logger.debug(f"Deleting last typed '{last_typed}' ({num_backspaces} backspaces)")
            #     for _ in range(num_backspaces): await self.keyboard_simulator.press_release_key(keyboard.Key.backspace)
            #     await self.gvm.set(history_key, history)

        # 3. AI Translation (Example trigger: "translate to [language]")
        elif action_lower.startswith("translate to "):
            target_lang = action_lower.replace("translate to ", "").strip()
            logger.debug(f"Action '{action}' identified as translation request to '{target_lang}'.")
            if not target_lang:
                 logger.warning("Translation action detected, but no target language specified.")
                 return
                 
            # Need the text that triggered this action (how to get context?)
            # Assume the relevant text is the last final segment for the session
            source_text_key = STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE.format(session_id=session_id)
            source_text = await self.gvm.get(source_text_key, "")
            
            if not source_text:
                 logger.warning(f"Cannot perform translation for session {session_id}, no source text found.")
                 return
                 
            source_lang = await self.gvm.get("config.general.source_language", "en-US")
            model = await self.gvm.get("config.translation.model", "gpt-3.5-turbo") # Get model from config
            
            translated_text = await self.openai_manager.get_translation(
                text=source_text,
                source_lang=source_lang,
                target_lang=target_lang,
                model=model
            )
            if translated_text:
                 # Queue the translated text for typing
                 typing_queue_key = STATE_OUTPUT_TYPING_QUEUE
                 current_queue = await self.gvm.get(typing_queue_key, [])
                 current_queue.append(translated_text + " ") # Add trailing space
                 await self.gvm.set(typing_queue_key, current_queue)
                 logger.info(f"Queued translated text for typing: '{translated_text[:50]}...'")
            else:
                 logger.error(f"Translation failed for session {session_id}.")
                 # Error state might already be set by openai_manager

        # 4. AI Query (Example trigger: "ask ai")
        elif action_lower == "ask ai": # Or similar trigger phrase
            logger.debug(f"Action '{action}' identified as AI query request.")
            # Get the full text segment that included the command
            query_text_key = STATE_SESSION_FINAL_TRANSCRIPT_SEGMENT_TEMPLATE.format(session_id=session_id)
            query_text = await self.gvm.get(query_text_key, "")
            
            if not query_text:
                 logger.warning(f"Cannot perform AI query for session {session_id}, no query text found.")
                 return
            
            model = await self.gvm.get("config.openai.query_model", "gpt-4o-mini") # Use a different model? 
            ai_response = await self.openai_manager.get_ai_query_response(query=query_text, model=model)
            
            if ai_response:
                 # Place response in GVM state for display, not typing queue
                 await self.gvm.set(STATE_UI_AI_RESPONSE_DISPLAY, ai_response)
                 logger.info(f"AI query response received and placed in GVM state.")
            else:
                 logger.error(f"AI query failed for session {session_id}.")
                 await self.gvm.set(STATE_UI_AI_RESPONSE_DISPLAY, _("AI query failed.")) # Show error in UI

        # 5. Other potential actions (e.g., "cancel", custom scripts)
        elif action_lower == "cancel":
             logger.info(f"Cancel action detected for session {session_id}. Currently no-op.")
             # Might clear queues, stop processing, etc.
             pass

        else:
            logger.warning(f"Unknown action requested: '{action}' for session {session_id}")

    async def run_loop(self):
        """Monitors the action source in GVM and executes actions."""
        logger.info(f"ActionExecutor run_loop starting. Watching GVM key: '{self._action_source_key}'")
        self._stop_event.clear()
        last_processed_actions = []

        while not self._stop_event.is_set():
            try:
                # Wait for the action list/queue state to change
                await self.gvm.wait_for_change(self._action_source_key)
                
                # Get the current list of actions
                # Use pop(0) like a queue if it's STATE_OUTPUT_ACTION_QUEUE?
                # If it's STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS, it might just contain the single confirmed action.
                # Let's assume it contains a list of actions to be processed.
                current_actions_list = await self.gvm.get(self._action_source_key, [])
                
                if current_actions_list: # Check if list is not empty
                    # Process only new actions compared to last time? Or process all?
                    # Simple approach: process the first item and remove it.
                    action_to_process = current_actions_list.pop(0) # Treat as FIFO queue
                    
                    # --- Need Session Context --- >
                    # How do we know which session this action belongs to?
                    # The action source state needs to include the session_id.
                    # Example modification: STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS becomes
                    # a list of tuples: [(session_id, action), ...]
                    # Let's assume for now the action source provides a tuple:
                    if isinstance(action_to_process, tuple) and len(action_to_process) == 2:
                        session_id, action_str = action_to_process
                        logger.debug(f"Dequeued action: '{action_str}' for session '{session_id}'")
                        await self._execute_action(session_id, action_str)
                    else:
                         # Fallback if format is unexpected (e.g., just the action string)
                         logger.warning(f"Processing action '{action_to_process}' without session context. Execution might be limited.")
                         # Attempt execution without session ID (some actions might work)
                         await self._execute_action("unknown_session", str(action_to_process))

                    # Update the GVM state with the remaining actions
                    await self.gvm.set(self._action_source_key, current_actions_list)
                    # < --- End Session Context Handling --- 
                else:
                     # List is empty, wait again
                     pass 

            except asyncio.CancelledError:
                logger.info("ActionExecutor run_loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in ActionExecutor run_loop: {e}", exc_info=True)
                # Avoid tight loop on error
                await asyncio.sleep(1)

        logger.info("ActionExecutor run_loop finished.")

    async def cleanup(self):
        """Cleans up the executor."""
        logger.info("ActionExecutor cleaning up...")
        self._stop_event.set()
        if self._active_task and not self._active_task.done():
             self._active_task.cancel()
             try: await self._active_task
             except asyncio.CancelledError: pass
        logger.info("ActionExecutor cleanup finished.") 