import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, Set, Callable, Coroutine

# --- Pre-import Modules ---
from input_manager import InputManager
from background_audio_recorder import BackgroundAudioRecorder
from stt_manager import STTManager
from dictation_text_manager import DictationTextManager
from action_executor import ActionExecutor
from action_ui_manager import ActionUIManager
from keyboard_simulator import KeyboardSimulator
from openai_manager import OpenAIManager
from interim_text_ui_manager import InterimTextUIManager
from mic_ui_manager import MicUIManager
from session_monitor_ui import SessionMonitorUI
from systray_ui import SystrayUIManager
# -------------------------

logger = logging.getLogger(__name__)

class GlobalVariablesManager:
    """
    Central engine and state repository for the application.
    Manages shared state, module lifecycles, and reactive logic.
    """
    def __init__(self, config_manager):
        logger.debug("Initializing GlobalVariablesManager...") # Log start
        self._state: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        # For wait_for_change: key -> set of asyncio.Event
        self._listeners: Dict[str, Set[asyncio.Event]] = defaultdict(set)
        # For wait_for_value: key -> set of (value, asyncio.Event)
        self._value_listeners: Dict[str, Set[tuple[Any, asyncio.Event]]] = defaultdict(set)
        self._config_manager = config_manager
        self._modules: Dict[str, Any] = {} # Stores module instances
        self._module_tasks: Dict[str, asyncio.Task] = {} # Stores running module tasks
        self._shutdown_event = asyncio.Event()
        # TODO: Add GVM.UI sub-module instance if needed
        logger.debug("GlobalVariablesManager initialization complete.") # Log end

    # Original async version - RE-ENABLING
    async def load_initial_config(self) -> None:
        """Loads the initial configuration into the GVM state."""
        async with self._lock:
            try:
                config_data = self._config_manager.load_config()
                # Store config keys directly or under a 'config.' prefix
                # Example: Storing under 'config.' prefix
                for key, value in config_data.items():
                    self._state[f"config.{key}"] = value
                logger.info("Initial configuration loaded into GVM state.")
                # Notify listeners about config load if necessary
                # await self._notify_listeners("config") # Commented out: Notify for broad 'config' changes
            except Exception as e:
                logger.error(f"Failed to load initial configuration: {e}")
                # Handle error appropriately, maybe set a 'status.error' state

    async def get(self, key: str, default: Any = None) -> Any:
        """Gets a value from the shared state."""
        async with self._lock:
            # Implement nested key access if needed (e.g., 'dict.key')
            return self._state.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        """Sets a value in the shared state and notifies listeners."""
        async with self._lock:
            old_value = self._state.get(key)
            if old_value != value:
                self._state[key] = value
                logger.debug(f"GVM State SET: {key} = {value}")
                await self._notify_listeners(key)
                await self._check_value_listeners(key, value)

    async def _notify_listeners(self, key: str) -> None:
        """Notifies all listeners waiting for changes to the specific key."""
        # Needs lock acquired before calling
        if key in self._listeners:
            events_to_notify = list(self._listeners[key]) # Copy to avoid modification during iteration
            self._listeners[key].clear() # Events are one-shot
            for event in events_to_notify:
                event.set()
            logger.debug(f"Notified {len(events_to_notify)} listeners for key '{key}' change.")

    async def _check_value_listeners(self, key: str, value: Any) -> None:
        """Checks and notifies listeners waiting for a specific value."""
        # Needs lock acquired before calling
        if key in self._value_listeners:
            satisfied_listeners = set()
            listeners_for_key = list(self._value_listeners[key]) # Copy for safe iteration
            for target_value, event in listeners_for_key:
                if value == target_value:
                    event.set()
                    satisfied_listeners.add((target_value, event))
                    logger.debug(f"Notified listener waiting for key '{key}' == {target_value}")

            # Remove satisfied listeners
            if satisfied_listeners:
                self._value_listeners[key].difference_update(satisfied_listeners)
                if not self._value_listeners[key]:
                    del self._value_listeners[key] # Clean up empty set

    async def wait_for_change(self, key: str) -> None:
        """Waits until the specified key changes value."""
        event = asyncio.Event()
        async with self._lock:
            self._listeners[key].add(event)
        logger.debug(f"Listener added: Waiting for change on key '{key}'")
        await event.wait()
        logger.debug(f"Listener notified: Change detected on key '{key}'")

    async def wait_for_value(self, key: str, value: Any) -> None:
        """Waits until the specified key reaches the target value."""
        async with self._lock:
            current_value = self._state.get(key)
            if current_value == value:
                logger.debug(f"wait_for_value: Key '{key}' already has value {value}. Returning immediately.")
                return # Value already matches

            event = asyncio.Event()
            self._value_listeners[key].add((value, event))

        logger.debug(f"Listener added: Waiting for key '{key}' to become {value}")
        await event.wait()
        logger.debug(f"Listener notified: Key '{key}' reached value {value}")

    def get_main_loop(self) -> asyncio.AbstractEventLoop:
        """Returns the running asyncio event loop."""
        # Note: This assumes the GVM is instantiated and run within an active asyncio loop.
        try:
            return asyncio.get_running_loop()
        except RuntimeError as e:
            logger.error(f"Could not get running event loop: {e}. Is the GVM running within asyncio.run()?", exc_info=True)
            # Depending on requirements, might raise the error or return None/handle differently
            raise

    # --- Module Lifecycle Management (Stubs - Needs Implementation) ---

    async def _manage_module_lifecycles(self) -> None:
        """Continuously monitors config state and manages module start/stop."""
        logger.debug("GVM ModuleLifecycleManager task started.") # Log task start
        # This loop will run as part of the main GVM run loop
        # Example logic (needs refinement based on actual config keys):
        loop_count = 0
        while not self._shutdown_event.is_set():
            logger.debug(f"Lifecycle loop iteration {loop_count} starting...")
            modules_to_start = []
            modules_to_stop = []
            try:
                # --- Section 1: Read state under lock --- 
                logger.debug("Lifecycle acquiring lock...")
                async with self._lock:
                    logger.debug("Lifecycle lock acquired. Getting config.modules...")
                    all_module_configs = self._state.get("config.modules", {}) 
                    logger.debug(f"Got config.modules directly: {list(all_module_configs.keys())}")

                    for module_name_key, is_enabled in all_module_configs.items():
                        if not module_name_key.endswith("_enabled"): continue
                        
                        module_base_name = module_name_key.replace("_enabled", "")
                        task = self._module_tasks.get(module_base_name)
                        is_running = task and not task.done()
                        logger.debug(f"Module '{module_base_name}': Enabled={is_enabled}, IsRunning={is_running}")

                        if is_enabled and not is_running:
                            modules_to_start.append(module_base_name)
                        elif not is_enabled and is_running:
                            modules_to_stop.append(module_base_name)
                
                logger.debug("Lifecycle lock released.")
                # --- Section 2: Perform actions without lock --- 
                if modules_to_stop:
                     logger.debug(f"Stopping modules: {modules_to_stop}")
                     for module_name in modules_to_stop:
                          await self._stop_module(module_name) # Stop actions outside lock
                
                if modules_to_start:
                     logger.debug(f"Starting modules: {modules_to_start}")
                     for module_name in modules_to_start:
                          await self._start_module(module_name) # Start actions outside lock

                # --- Section 3: Wait --- 
                logger.debug("Lifecycle actions complete. Waiting for next iteration...")
                await asyncio.sleep(1) 

            except asyncio.CancelledError:
                logger.info("Module lifecycle manager task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in module lifecycle manager: {e}", exc_info=True)
                await asyncio.sleep(5) # Avoid tight loop on error
            loop_count += 1
        logger.debug("GVM ModuleLifecycleManager task finished.")
        # --- End original code ---
        # return # Just return immediately for this test - REMOVED

    async def _instantiate_module(self, module_name: str) -> Any:
        """Instantiates a module from pre-imported classes."""
        logger.info(f"Attempting to instantiate module: {module_name}")
        main_loop = self.get_main_loop() # Get loop once
        
        if module_name == "input_manager":
            return InputManager(self)
        elif module_name == "background_audio_recorder":
             return BackgroundAudioRecorder(self)
        elif module_name == "stt_manager":
             audio_recorder = self._modules.get("background_audio_recorder") 
             if not audio_recorder:
                  logger.warning(f"Cannot instantiate STTManager: BackgroundAudioRecorder module is not loaded or disabled.")
                  return None
             return STTManager(self, audio_recorder)
        elif module_name == "dictation_text_manager":
             return DictationTextManager(self)
        elif module_name == "action_executor":
             keyboard_sim = self._modules.get("keyboard_simulator")
             openai_man = self._modules.get("openai_manager")
             if not keyboard_sim or not openai_man:
                  logger.error("Cannot instantiate ActionExecutor: KeyboardSimulator or OpenAIManager not found.")
                  return None
             return ActionExecutor(self, keyboard_sim, openai_man)
        elif module_name == "action_ui_manager":
             return ActionUIManager(self, main_loop)
        elif module_name == "keyboard_simulator":
             return KeyboardSimulator() 
        elif module_name == "openai_manager":
             return OpenAIManager(self)
        elif module_name == "interim_text_ui_manager":
             return InterimTextUIManager(self, main_loop)
        elif module_name == "mic_ui_manager":
             return MicUIManager(self, main_loop)
        elif module_name == "session_monitor_ui":
             return SessionMonitorUI(self, main_loop)
        elif module_name == "systray_ui":
             return SystrayUIManager(self, main_loop)
        # ... other potential modules ...
        else:
            logger.warning(f"No instantiation logic found for module: {module_name}")
            return None

    async def _start_module(self, module_name: str) -> None:
        """Initializes and starts a module's run_loop."""
        # Needs lock acquired before calling
        if module_name in self._module_tasks and not self._module_tasks[module_name].done():
            logger.debug(f"Module '{module_name}' is already running.")
            return

        logger.info(f"Starting module: {module_name}")
        try:
            module_instance = self._modules.get(module_name)
            if not module_instance:
                 module_instance = await self._instantiate_module(module_name)
                 if not module_instance:
                     logger.error(f"Failed to instantiate module: {module_name}")
                     return
                 self._modules[module_name] = module_instance

            # Call module's init
            if hasattr(module_instance, "init") and asyncio.iscoroutinefunction(module_instance.init):
                await module_instance.init()

            # Start run_loop task
            if hasattr(module_instance, "run_loop") and asyncio.iscoroutinefunction(module_instance.run_loop):
                task = asyncio.create_task(module_instance.run_loop(), name=f"{module_name}_run_loop")
                self._module_tasks[module_name] = task
                task.add_done_callback(lambda t: self._module_task_done_callback(module_name, t))
                logger.info(f"Module '{module_name}' run_loop task started.")
            else:
                 logger.warning(f"Module '{module_name}' does not have an async run_loop method.")

        except Exception as e:
            logger.error(f"Error starting module {module_name}: {e}", exc_info=True)
            # Ensure partial setup is cleaned up if needed

    async def _stop_module(self, module_name: str) -> None:
        """Stops a module's run_loop and calls its cleanup."""
         # Needs lock acquired before calling
        logger.info(f"Stopping module: {module_name}")
        task = self._module_tasks.pop(module_name, None)
        if task and not task.done():
            task.cancel()
            try:
                await task # Wait for cancellation to complete
            except asyncio.CancelledError:
                logger.debug(f"Module '{module_name}' run_loop task cancelled successfully.")
            except Exception as e:
                 logger.error(f"Error during task cancellation for module {module_name}: {e}", exc_info=True)

        module_instance = self._modules.get(module_name)
        if module_instance and hasattr(module_instance, "cleanup") and asyncio.iscoroutinefunction(module_instance.cleanup):
            try:
                await module_instance.cleanup()
                logger.info(f"Module '{module_name}' cleanup called.")
            except Exception as e:
                logger.error(f"Error during cleanup for module {module_name}: {e}", exc_info=True)
        # Optionally remove module instance from self._modules if it should be fully re-created
        # del self._modules[module_name]

    def _module_task_done_callback(self, module_name: str, task: asyncio.Task) -> None:
        """Callback when a module's run_loop task finishes."""
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Module '{module_name}' task exited with exception: {exc}", exc_info=exc)
                # Optionally try restarting the module or set an error state
                # Ensure the task is removed from _module_tasks if it wasn't already
                if module_name in self._module_tasks and self._module_tasks[module_name] is task:
                    del self._module_tasks[module_name]
            elif task.cancelled():
                logger.info(f"Module '{module_name}' task was cancelled.")
            else:
                logger.info(f"Module '{module_name}' task completed.")
        except Exception as e:
            logger.error(f"Error in module task done callback for {module_name}: {e}", exc_info=True)


    # --- Reactive Logic (Stubs - Needs Implementation) ---

    async def _run_reactive_logic(self) -> None:
        """Monitors specific state keys and triggers actions based on changes."""
        logger.debug("Starting GVM reactive logic task loop...") # Log start
        # This loop runs as part of the main GVM run loop
        while not self._shutdown_event.is_set():
            try:
                # Example: Monitor recognized_actions for Action UI trigger
                # This requires iterating through sessions, which complicates state structure
                # Awaiting specific changes might be better here
                await asyncio.sleep(0.1) # Placeholder polling

                # TODO: Implement logic based on target_system_design.md
                # - Monitor `sessions.{id}.recognized_actions` -> set `ui.confirmation.request`
                # - Monitor `ui.confirmation.confirmed_actions` -> set `output.action_queue` (or directly call ActionExecutor?)

            except asyncio.CancelledError:
                logger.info("Reactive logic task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in reactive logic: {e}", exc_info=True)
                await asyncio.sleep(5)

    # --- Main Execution Loop ---

    async def run(self) -> None:
        """Starts the GVM, including module management and reactive logic."""
        logger.info("Starting GlobalVariablesManager...")
        await self.load_initial_config()
        
        # --- Restore original task creation and wait --- 
        logger.debug("Creating GVM background tasks...")
        lifecycle_task = asyncio.create_task(self._manage_module_lifecycles(), name="GVM_ModuleLifecycleManager")
        # Restore reactive task if needed
        # reactive_task = asyncio.create_task(self._run_reactive_logic(), name="GVM_ReactiveLogic") 
        logger.debug(f"GVM background tasks created: lifecycle={lifecycle_task.get_name()}") 

        logger.debug("GVM entering main wait loop (_shutdown_event.wait())...")
        await self._shutdown_event.wait()
        logger.debug("GVM main wait loop exited.")

        logger.info("GVM shutdown initiated.")
        # Cancel running tasks
        # if reactive_task:
        #     reactive_task.cancel()
        lifecycle_task.cancel()
        # Wait for tasks to finish cancelling/cleaning up
        tasks_to_wait_on = [t for t in [lifecycle_task] if t] # Add reactive_task if restored
        if tasks_to_wait_on:
             await asyncio.gather(*tasks_to_wait_on, return_exceptions=True)

        # Final cleanup
        logger.debug("Performing final module cleanup...")
        async with self._lock:
             module_names = list(self._module_tasks.keys())
             logger.debug(f"Modules needing potential cleanup: {module_names}")
             for module_name in module_names:
                  await self._stop_module(module_name)

        logger.info("GlobalVariablesManager stopped.")
        # --- End Restore --- 

    def request_shutdown(self) -> None:
        """Signals the GVM to shut down."""
        logger.info("Shutdown requested.")
        self._shutdown_event.set()

# Example Usage (in main.py)
# async def main():
#     # Setup logging
#     # Create ConfigManager instance
#     gvm = GlobalVariablesManager(config_manager_instance)
#     try:
#         await gvm.run()
#     except KeyboardInterrupt:
#         logger.info("Ctrl+C detected, initiating shutdown.")
#         gvm.request_shutdown()
#     finally:
#          # Ensure graceful shutdown if run() exits unexpectedly
#          if not gvm._shutdown_event.is_set():
#               gvm.request_shutdown()
#          # Optional: A short wait to allow cleanup tasks to complete?
#          # await asyncio.sleep(1)
#
# if __name__ == "__main__":
#    asyncio.run(main()) 