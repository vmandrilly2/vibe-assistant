import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, Set, Callable, Coroutine
import os # Import os

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
# API Clients
from deepgram import DeepgramClient, DeepgramClientOptions # Import Deepgram client
from openai import AsyncOpenAI # Import OpenAI client
from config_manager import ConfigManager
# Import module types for isinstance checks if necessary, or use Any
from constants import (
    CONFIG_MODULES_PREFIX, # Used for identifying module enablement config
    STATE_APP_STATUS # Added import
)
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
        self._modules_running: Dict[str, bool] = {} # Stores running status from lifecycle perspective
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
        listeners_to_notify = []
        value_listeners_to_notify = []
        satisfied_value_listeners_for_removal = set()

        async with self._lock:
            old_value = self._state.get(key)
            if old_value != value:
                # Update state first
                self._state[key] = value
                logger.debug(f"GVM State SET: {key} = {value}")

                # Collect listeners to notify *under lock*
                if key in self._listeners:
                    listeners_to_notify = list(self._listeners[key])
                    self._listeners[key].clear() # Clear under lock

                if key in self._value_listeners:
                    listeners_for_key = list(self._value_listeners[key]) # Copy for safe iteration below
                    for target_value, event in listeners_for_key:
                        if value == target_value:
                            value_listeners_to_notify.append(event) # Collect event to notify
                            satisfied_value_listeners_for_removal.add((target_value, event)) # Mark for removal

                    # Remove satisfied listeners from the main dict *under lock*
                    if satisfied_value_listeners_for_removal:
                        self._value_listeners[key].difference_update(satisfied_value_listeners_for_removal)
                        if not self._value_listeners[key]:
                            del self._value_listeners[key] # Clean up empty set

        # --- Notify listeners *outside* the lock ---
        if listeners_to_notify:
            logger.debug(f"GVM: Notifying {len(listeners_to_notify)} change listeners for key '{key}' (outside lock).")
            for event in listeners_to_notify:
                try:
                    event.set()
                except Exception as e: # Catch potential errors during set()
                    logger.error(f"Error setting change event for key {key}: {e}")
            logger.debug(f"GVM: Finished notifying change listeners for key '{key}' (outside lock).")

        if value_listeners_to_notify:
            logger.debug(f"GVM: Notifying {len(value_listeners_to_notify)} value listeners for key '{key}' (outside lock).")
            for event in value_listeners_to_notify:
                try:
                    event.set()
                except Exception as e: # Catch potential errors during set()
                    logger.error(f"Error setting value event for key {key}: {e}")
            logger.debug(f"GVM: Finished notifying value listeners for key '{key}' (outside lock).")

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
        self._lifecycle_iteration = 0
        while not self._shutdown_event.is_set():
            # logger.debug(f"Lifecycle loop iteration {self._lifecycle_iteration} starting...") # Commented out repetitive log
            modules_to_start = []
            modules_to_stop = []
            try:
                # --- Section 1: Read state under lock --- 
                # logger.debug("Lifecycle acquiring lock...") # Commented out
                async with self._lock:
                    # logger.debug("Lifecycle lock acquired. Getting config.modules...") # Commented out
                    all_module_configs = self._state.get("config.modules", {}) 
                    # logger.debug(f"Got config.modules directly: {list(all_module_configs.keys())}") # Commented out

                    for module_name_key, is_enabled in all_module_configs.items():
                        if not module_name_key.endswith("_enabled"): continue
                        
                        module_base_name = module_name_key.replace("_enabled", "")
                        task = self._module_tasks.get(f"module_{module_base_name}") # Use the key format used in _start_module
                        # Check both task status and the explicit running flag
                        task_running = task and not task.done()
                        explicitly_running = self._modules_running.get(module_base_name, False)
                        is_running = task_running or explicitly_running 

                        if is_enabled and not is_running:
                            modules_to_start.append(module_base_name)
                        elif not is_enabled and is_running:
                            modules_to_stop.append(module_base_name)
                
                # logger.debug("Lifecycle lock released.") # Commented out
                # --- Section 2: Perform actions without lock --- 
                if modules_to_stop:
                     logger.debug(f"Stopping modules: {modules_to_stop}") # Keep logs for actual actions
                     for module_name in modules_to_stop:
                          await self._stop_module(module_name) # Stop actions outside lock
                
                if modules_to_start:
                     logger.debug(f"Starting modules: {modules_to_start}") # Keep logs for actual actions
                     for module_name in modules_to_start:
                          await self._start_module(module_name) # Start actions outside lock

                # --- Section 3: Wait --- 
                # logger.debug("Lifecycle actions complete. Waiting for next iteration...") # Commented out
                await asyncio.sleep(1) 

            except asyncio.CancelledError:
                logger.info("Module lifecycle manager task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in module lifecycle manager: {e}", exc_info=True)
                await asyncio.sleep(5) # Avoid tight loop on error
            self._lifecycle_iteration += 1
        logger.debug("GVM ModuleLifecycleManager task finished.")
        # --- End original code ---
        # return # Just return immediately for this test - REMOVED

    async def _instantiate_module(self, module_name: str) -> Any:
        """Instantiates a module, creating API clients as needed."""
        logger.info(f"Attempting to instantiate module: {module_name}")
        main_loop = self.get_main_loop() 
        
        if module_name == "input_manager":
            # Pass main_loop to InputManager constructor
            return InputManager(self, main_loop)
        elif module_name == "background_audio_recorder":
             # Pass main_loop to BackgroundAudioRecorder constructor
             return BackgroundAudioRecorder(self, main_loop)
        elif module_name == "stt_manager":
             # Create Deepgram client here
             api_key = os.getenv("DEEPGRAM_API_KEY")
             if not api_key:
                  logger.error("DEEPGRAM_API_KEY not found in environment variables.")
                  return None # Cannot instantiate without key
             try:
                  dg_client_config: DeepgramClientOptions = DeepgramClientOptions(
                       verbose=logging.DEBUG # Adjust verbosity as needed
                  )
                  dg_client = DeepgramClient(api_key, dg_client_config)
                  logger.info("Deepgram client created.")
             except Exception as e:
                  logger.error(f"Failed to create Deepgram client: {e}", exc_info=True)
                  return None
             
             audio_recorder = self._modules.get("background_audio_recorder") 
             if not audio_recorder:
                  logger.warning(f"Cannot instantiate STTManager: BackgroundAudioRecorder module is not loaded or disabled.")
                  # Clean up dg_client? Not strictly necessary here as it's local.
                  return None
             # Pass the CLIENT instance, not GVM
             return STTManager(dg_client, audio_recorder, self) # Pass GVM separately if needed for state
        elif module_name == "dictation_text_manager":
             return DictationTextManager(self)
        elif module_name == "action_executor":
             # Ensure dependencies are loaded/instantiated first
             keyboard_sim = self._modules.get("keyboard_simulator")
             if not keyboard_sim:
                  logger.info("Instantiating dependency: keyboard_simulator for action_executor")
                  keyboard_sim = await self._instantiate_module("keyboard_simulator")
                  if keyboard_sim: self._modules["keyboard_simulator"] = keyboard_sim
                  else: logger.error("Failed to instantiate dependency: keyboard_simulator"); return None
             
             openai_man = self._modules.get("openai_manager")
             if not openai_man:
                  logger.info("Instantiating dependency: openai_manager for action_executor")
                  openai_man = await self._instantiate_module("openai_manager")
                  if openai_man: self._modules["openai_manager"] = openai_man
                  else: logger.error("Failed to instantiate dependency: openai_manager"); return None

             # Now dependencies should exist in self._modules
             # keyboard_sim = self._modules.get("keyboard_simulator")
             # openai_man = self._modules.get("openai_manager")
             # if not keyboard_sim or not openai_man: # This check should ideally not be needed now
             #      logger.error("Cannot instantiate ActionExecutor: KeyboardSimulator or OpenAIManager not found after attempting instantiation.")
             #      return None
             return ActionExecutor(self, keyboard_sim, openai_man)
        elif module_name == "action_ui_manager":
             return ActionUIManager(self, main_loop) 
        elif module_name == "keyboard_simulator":
             return KeyboardSimulator() 
        elif module_name == "openai_manager":
             # Create OpenAI client here
             api_key = os.getenv("OPENAI_API_KEY")
             if not api_key:
                  logger.error("OPENAI_API_KEY not found in environment variables.")
                  return None # Cannot instantiate without key
             try:
                  openai_client = AsyncOpenAI(api_key=api_key)
                  logger.info("OpenAI client created.")
             except Exception as e:
                  logger.error(f"Failed to create OpenAI client: {e}", exc_info=True)
                  return None
             # Pass the CLIENT instance, not GVM
             return OpenAIManager(openai_client) # Pass client
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
                 self._modules[module_name] = module_instance # Store instance regardless of run_loop

            # Call module's init
            if hasattr(module_instance, "init") and asyncio.iscoroutinefunction(module_instance.init):
                await module_instance.init()

            # Check if the module has an async run_loop method
            run_loop_method = getattr(module_instance, "run_loop", None)
            if asyncio.iscoroutinefunction(run_loop_method):
                logging.info(f"Module '{module_name}' run_loop task started.")
                self._modules_running[module_name] = True # Mark as running
                self._module_tasks[f"module_{module_name}"] = asyncio.create_task(
                    run_loop_method(), name=f"Module_{module_name}_run_loop"
                )
            else:
                logger.warning(f"Module '{module_name}' does not have an async run_loop method.")
                # Mark passive/utility modules as 'running' so lifecycle doesn't restart them
                self._modules_running[module_name] = True

        except Exception as e:
            logger.error(f"Error starting module {module_name}: {e}", exc_info=True)
            # Ensure partial setup is cleaned up if needed

    async def _stop_module(self, module_name: str) -> None:
        """Stops a module's run_loop and calls its cleanup."""
         # Needs lock acquired before calling
        logger.info(f"Stopping module: {module_name}")
        task_key = f"module_{module_name}"
        task = self._module_tasks.pop(task_key, None)
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
                task_key_for_check = f"module_{module_name}" # Reconstruct key for checking
                if task_key_for_check in self._module_tasks and self._module_tasks[task_key_for_check] is task:
                    del self._module_tasks[task_key_for_check]
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
        await self.set(STATE_APP_STATUS, "running") # Set app status to running
        
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