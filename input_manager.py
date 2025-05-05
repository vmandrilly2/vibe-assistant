# input_manager.py
import asyncio
import logging
from pynput import mouse, keyboard
from pynput.mouse import Button
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener
from threading import Thread
import time

logger = logging.getLogger(__name__)

# Mapping from string names to pynput Button objects
PYNPUT_BUTTON_MAP = {
    "left": mouse.Button.left,
    "right": mouse.Button.right,
    "middle": mouse.Button.middle,
    # Add x1, x2 if needed
}

class InputManager:
    """Listens for keyboard and mouse events and updates GVM state."""

    def __init__(self, gvm):
        logger.debug("--- InputManager.__init__ called ---")
        self.gvm = gvm
        self.mouse_listener = None
        self.keyboard_listener = None
        self._stop_event = asyncio.Event() # Use asyncio event for signaling stop
        self._listener_thread = None
        self._target_button = None
        self._is_pressed = False # Internal state to track button press
        logger.debug("--- InputManager.__init__ finished ---")

    async def init(self):
        """Initializes listeners based on config from GVM."""
        logger.debug("--- InputManager.init started ---")
        trigger_key_name = await self.gvm.get("config.general.trigger_key", "middle")
        logger.debug(f"--- InputManager.init got trigger_key_name: {trigger_key_name} ---")
        self._target_button = PYNPUT_BUTTON_MAP.get(trigger_key_name)

        if not self._target_button:
            logger.error(f"Invalid trigger key name '{trigger_key_name}' in config. InputManager disabled.")
            return False # Indicate failure
        
        logger.info(f"InputManager initialized. Trigger button: {trigger_key_name}")
        logger.debug("--- InputManager.init finished (success) ---")
        return True

    def _run_listeners_sync(self):
        """Synchronous method to run pynput listeners in a separate thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Initialize listeners within the new thread's event loop context
            logger.debug("Initializing pynput listeners in listener thread...")
            self.mouse_listener = mouse.Listener(
                on_click=lambda x, y, button, pressed: loop.call_soon_threadsafe(self._on_click_sync, x, y, button, pressed)
            )
            # Add Keyboard listener if needed for modifiers or other keys
            # self.keyboard_listener = KeyboardListener(...) 
            logger.debug("Listeners initialized. Starting mouse listener...")
            self.mouse_listener.start()
            logger.info("Mouse listener started in dedicated thread.")
            # Keep thread alive while stop event is not set
            while not self._stop_event.is_set():
                 time.sleep(0.1)
                 
        except Exception as e:
            logger.error(f"Error in listener thread: {e}", exc_info=True)
        finally:
            if self.mouse_listener:
                self.mouse_listener.stop()
                logger.info("Mouse listener stopped.")
            # Stop keyboard listener if started
            loop.close() # Clean up the event loop for this thread
            logger.info("Input listener thread finished.")

    def _on_click_sync(self, x, y, button, pressed):
        """Callback executed by pynput listener thread, schedules async handler."""
        # This method is called by pynput, which might be in a different thread.
        # We need to schedule the async operation in the main event loop.
        if button == self._target_button:
            asyncio.run_coroutine_threadsafe(self._handle_click_async(pressed), self.gvm.get_main_loop()) # Assuming GVM provides access to the main loop

    async def _handle_click_async(self, pressed: bool):
        """Asynchronous handler for target button clicks."""
        if pressed and not self._is_pressed:
            self._is_pressed = True
            logger.debug(f"Trigger button pressed.")
            await self.gvm.set("input.dictation_key_pressed", True)
        elif not pressed and self._is_pressed:
            self._is_pressed = False
            logger.debug(f"Trigger button released.")
            await self.gvm.set("input.dictation_key_pressed", False)

    async def run_loop(self):
        """Starts the listener thread and waits for stop signal."""
        if not self._target_button:
            logger.warning("InputManager cannot run, trigger button not set.")
            return

        logger.info("InputManager run_loop starting...")
        self._stop_event.clear()
        self._listener_thread = Thread(target=self._run_listeners_sync, daemon=True, name="InputListenerThread")
        self._listener_thread.start()

        # Keep the run_loop alive until stop is signaled
        await self._stop_event.wait()
        logger.info("InputManager run_loop stopping.")


    async def cleanup(self):
        """Stops the listeners and cleans up."""
        logger.info("InputManager cleaning up...")
        self._stop_event.set() # Signal the listener thread to stop
        if self._listener_thread and self._listener_thread.is_alive():
             self._listener_thread.join(timeout=1.0) # Wait briefly for thread exit
             if self._listener_thread.is_alive():
                  logger.warning("Input listener thread did not exit gracefully.")
        self.mouse_listener = None
        self.keyboard_listener = None
        self._listener_thread = None
        logger.info("InputManager cleanup finished.")

# Note: This implementation runs pynput listeners in a separate thread
# because pynput's listeners are typically blocking. Callbacks from the
# listener thread then use `asyncio.run_coroutine_threadsafe` to schedule
# the actual GVM state updates in the main asyncio event loop.
# The GVM needs a method `get_main_loop()` or similar to provide the correct loop. 