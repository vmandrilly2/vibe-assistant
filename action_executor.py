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

        # 2. Deletion ("backspace", "delete_word")
        elif action_lower == "backspace":
            logger.debug(f"Action '{action}' identified as backspace.")
            await self.keyboard_simulator.press_release_key(keyboard.Key.backspace)
        elif action_lower == "delete_word":
            logger.debug(f"Action '{action}' identified as delete word.")
            # Get history for the relevant session
            history_key = STATE_SESSION_HISTORY_TEMPLATE.format(session_id=session_id)
            history = await self.gvm.get(history_key, [])
            if history:
                # Simple approach: delete characters back to the last space
                last_typed_segment = history[-1] # Get the last typed segment
                if last_typed_segment:
                     # Find the last space, or the beginning if no space
                     last_space_index = last_typed_segment.rfind(' ')
                     chars_to_delete = len(last_typed_segment) - (last_space_index + 1)
                     if chars_to_delete > 0:
                          logger.debug(f"Deleting last word ('{last_typed_segment[last_space_index+1:]}') - {chars_to_delete} backspaces")
                          for _ in range(chars_to_delete):
                               await self.keyboard_simulator.press_release_key(keyboard.Key.backspace)
                               await asyncio.sleep(0.01) # Small delay between keys
                          # Update history (remove deleted part)
                          history[-1] = last_typed_segment[:last_space_index+1].rstrip() # Keep space before word
                          await self.gvm.set(history_key, history) 
                     else:
                          logger.debug("Last segment ended with space or was empty, only performing single backspace.")
                          await self.keyboard_simulator.press_release_key(keyboard.Key.backspace) 
                else:
                    # History entry was empty, just backspace once
                    await self.keyboard_simulator.press_release_key(keyboard.Key.backspace)
            else:
                 logger.warning(f"Cannot delete word for session {session_id}, history is empty.")
                 await self.keyboard_simulator.press_release_key(keyboard.Key.backspace) # Fallback?

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
        """Monitors typing and action queues in GVM and executes tasks."""
        typing_queue_key = STATE_OUTPUT_TYPING_QUEUE
        action_queue_key = STATE_OUTPUT_ACTION_QUEUE

        logger.info(f"ActionExecutor run_loop starting. Watching keys: '{typing_queue_key}', '{action_queue_key}'")
        self._stop_event.clear()

        # Ensure queues exist in GVM state
        await self.gvm.set(typing_queue_key, await self.gvm.get(typing_queue_key, []))
        await self.gvm.set(action_queue_key, await self.gvm.get(action_queue_key, []))

        while not self._stop_event.is_set():
            try:
                # Check queues *before* waiting to process any backlog
                typing_queue = await self.gvm.get(typing_queue_key, [])
                action_queue = await self.gvm.get(action_queue_key, [])

                # Log retrieved queue contents
                logger.debug(f"Retrieved typing_queue: {typing_queue}")
                logger.debug(f"Retrieved action_queue: {action_queue}")

                processed_item = False
                if typing_queue:
                    # Check type before popping
                    if not isinstance(typing_queue, list):
                        logger.error(f"Typing queue is not a list: {typing_queue}. Resetting.")
                        await self.gvm.set(typing_queue_key, [])
                        continue # Skip processing this iteration

                    if len(typing_queue) == 0:
                         logger.warning("Typing queue check passed but list is empty. Skipping pop.") # Should not happen if `if typing_queue:` works
                         continue 

                    try:
                        text_to_type = typing_queue.pop(0)
                    except IndexError:
                         logger.error(f"IndexError popping from typing_queue (length {len(typing_queue)}). Queue: {typing_queue}")
                         # Update GVM state to reflect the potentially corrupted queue
                         await self.gvm.set(typing_queue_key, typing_queue)
                         continue

                    logger.debug(f"Dequeued text for typing: '{text_to_type}'")
                    if text_to_type and isinstance(text_to_type, str):
                         logger.debug(f"Attempting to type text: '{text_to_type}'") # Log before calling simulator
                         await self.keyboard_simulator.type_text(text_to_type)
                    else:
                         logger.warning(f"Dequeued item is not a valid string: {text_to_type}")
                    await self.gvm.set(typing_queue_key, typing_queue) # Update GVM
                    processed_item = True # Indicate we processed something

                if action_queue:
                    action_tuple = action_queue.pop(0)
                    if isinstance(action_tuple, tuple) and len(action_tuple) == 2:
                        session_id, action_str = action_tuple
                        logger.debug(f"Dequeued action: '{action_str}' for session '{session_id}'")
                        await self._execute_action(session_id, action_str)
                    else:
                         logger.warning(f"Dequeued invalid item from action queue: {action_tuple}")
                    await self.gvm.set(action_queue_key, action_queue) # Update GVM
                    processed_item = True # Indicate we processed something

                # If we processed an item, loop back immediately to check queues again
                if processed_item:
                    await asyncio.sleep(0.01) # Small yield to prevent overly tight loop
                    continue 

                # If queues were empty, wait for changes
                logger.debug(f"ActionExecutor top of loop. Waiting on keys: {typing_queue_key}, {action_queue_key}")
                
                # Create wait tasks for GVM changes
                wait_typing_queue = asyncio.create_task(self.gvm.wait_for_change(typing_queue_key))
                wait_action_queue = asyncio.create_task(self.gvm.wait_for_change(action_queue_key))
                tasks = {wait_typing_queue, wait_action_queue}

                # --- Log before wait ---
                logger.debug(f"ActionExecutor: About to wait for changes. Tasks: {tasks}")

                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED
                )

                # --- Log after wait ---
                logger.debug(f"ActionExecutor: Wait completed. Done tasks: {done}, Pending tasks: {pending}")

                # Cancel pending tasks to avoid resource leaks
                for task in pending:
                    if task: 
                        logger.debug(f"ActionExecutor: Cancelling pending task {task}")
                        task.cancel()
                        try:
                            await task # Allow cancellation to propagate
                        except asyncio.CancelledError:
                            logger.debug(f"ActionExecutor: Pending task {task} successfully cancelled.")
                        except Exception as e:
                            logger.error(f"ActionExecutor: Error awaiting cancelled task {task}: {e}", exc_info=True)

                # Check completed tasks for errors
                for task in done:
                    if task and task.exception():
                         logger.error(f"Error in ActionExecutor wait task: {task.exception()}", exc_info=task.exception())

                # --- Process Queues ---
                # Always attempt to process both queues after any change
                typing_queue_key = STATE_OUTPUT_TYPING_QUEUE
                action_queue_key = STATE_OUTPUT_ACTION_QUEUE

                # Get current state of queues *after* wait returns
                typing_queue = await self.gvm.get(typing_queue_key, [])
                action_queue = await self.gvm.get(action_queue_key, [])

                # Log retrieved queue contents
                logger.debug(f"Retrieved typing_queue: {typing_queue}")
                logger.debug(f"Retrieved action_queue: {action_queue}")

            except asyncio.CancelledError:
                logger.info("ActionExecutor run_loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in ActionExecutor run_loop: {e}", exc_info=True)
                await asyncio.sleep(1)
            finally:
                # Ensure any created tasks within the loop (like wait_typing_queue, wait_action_queue)
                # are handled by the `pending` cancellation logic within the try block if an error occurs before that.
                # The original pending task cancellation loop after asyncio.wait handles the normal case.
                # No specific variables from the loop's direct scope (like typing_queue_task) need cleanup here.
                pass

        logger.info("ActionExecutor run_loop finished.")

    async def cleanup(self):
        """Cleans up the executor."""
        logger.info("ActionExecutor cleaning up...")
        self._stop_event.set()
        if self._active_task and not self._active_task.done():
             self._active_task.cancel()
        #      try: await self._active_task
        #      except asyncio.CancelledError: pass
        # logger.info("ActionExecutor cleanup finished.") 