# systray_ui.py
import asyncio
import logging
import threading
import time
from typing import Optional, Any
import functools # Import functools

import pystray
from pystray import MenuItem as item, Menu as menu
from PIL import Image, ImageDraw
import os # For path joining

# Assuming GVM and constants are accessible
# from global_variables_manager import GlobalVariablesManager
from constants import (
    # Import necessary constants like STATE keys for config, APP_NAME etc.
    APP_NAME, MODE_DICTATION, MODE_COMMAND, # Example constants
    CONFIG_MODULES_PREFIX, STATE_APP_STATUS, CONFIG_GENERAL_PREFIX, # Added CONFIG_GENERAL_PREFIX
)
# Assuming i18n is set up and provides _
from i18n import _, set_language, CURRENT_LANGUAGE, SUPPORTED_LANGUAGES # Import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

# --- Helper Functions ---
def create_image(width, height, color1, color2):
    # Simple placeholder image generation
    image = Image.new('RGB', (width, height), color1)
    dc = ImageDraw.Draw(image)
    dc.rectangle((width // 2, 0, width, height // 2), fill=color2)
    dc.rectangle((0, height // 2, width // 2, height), fill=color2)
    return image

class SystrayUIManager:
    """Manages the system tray icon and menu using pystray."""

    def __init__(self, gvm: Any, main_loop: asyncio.AbstractEventLoop):
        logger.info("SystrayUIManager initialized.")
        self.gvm = gvm
        self.main_loop = main_loop
        self.icon: Optional[pystray.Icon] = None
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event() # Use threading event for pystray thread
        self._current_menu = None # Store the current menu object
        self._initial_menu_built_event = asyncio.Event() # Event to signal initial menu is ready

    async def init(self) -> bool:
        """Initializes the Systray manager."""
        logger.info("SystrayUIManager initialized.")
        # Load initial config/state needed for the menu build
        # No specific init needed for pystray itself here
        return True

    def _run_pystray(self):
        """Runs the pystray icon loop. To be executed in a separate thread."""
        logger.debug("Systray UI pystray thread started.")
        try:
            # Load actual icon from assets folder in workspace root
            # Get the workspace root directory (assuming the script is run from within the workspace)
            # For robustness, this might need to be passed in or determined more reliably.
            # Assuming current working directory is workspace root for simplicity here.
            icon_path = os.path.join("assets", "microphone-icon.png")
            try:
                image = Image.open(icon_path)
                logger.info(f"Successfully loaded icon from {icon_path}")
            except FileNotFoundError:
                logger.error(f"Icon file not found at {icon_path}. Using placeholder.")
                image = create_image(64, 64, 'black', 'red') # Placeholder on error
            except Exception as e:
                logger.error(f"Error loading icon {icon_path}: {e}. Using placeholder.")
                image = create_image(64, 64, 'black', 'red')

            # Build initial menu (requires async access to GVM, run via threadsafe)
            # Clear the event before starting the build
            self._initial_menu_built_event.clear()
            asyncio.run_coroutine_threadsafe(self._build_and_set_initial_menu(), self.main_loop)
            
            # Wait for the initial menu to be built
            # This wait happens in the pystray thread, so it's okay to block here.
            # However, the asyncio event must be set from the main_loop.
            # We need a way for this thread to wait for an asyncio event set in another loop.
            # A simple time.sleep might be replaced by a threading.Event if direct asyncio event wait is tricky across threads.
            # For now, let's use a timeout on a loop.
            wait_start_time = time.monotonic()
            menu_wait_timeout = 5.0 # seconds
            while not self._initial_menu_built_event.is_set():
                if time.monotonic() - wait_start_time > menu_wait_timeout:
                    logger.warning(f"Timeout waiting for initial menu build after {menu_wait_timeout}s.")
                    break
                time.sleep(0.1) # Poll the event state

            if self._current_menu is None:
                 logger.warning("Systray menu not built before starting icon (or timed out). Using default exit menu.")
                 self._current_menu = menu(item(_('Exit'), self._on_exit_clicked))

            self.icon = pystray.Icon(APP_NAME, image, APP_NAME, self._current_menu)
            self.icon.run()

        except Exception as e:
             logger.error(f"Error in pystray thread: {e}", exc_info=True)
        finally:
             logger.info("Systray UI pystray thread finished.")
             self.icon = None # Clear icon reference

    async def _build_and_set_initial_menu(self):
         """Builds the menu asynchronously and sets it."""
         try:
            self._current_menu = await self._build_menu_async()
            logger.debug("Initial systray menu built.")
            # If icon is already running (unlikely here, but good practice), update it
            if self.icon and hasattr(self.icon, 'update_menu'):
                 self.icon.menu = self._current_menu
                 self.icon.update_menu()
         except Exception as e:
            logger.error(f"Error building initial menu: {e}", exc_info=True)
            # Ensure a default menu exists if build fails
            if self._current_menu is None:
                 self._current_menu = menu(item(_('Exit (Build Failed)'), self._on_exit_clicked))
         finally:
            # Signal that the menu build attempt is complete, regardless of success
            self.main_loop.call_soon_threadsafe(self._initial_menu_built_event.set)

    async def _rebuild_menu_async(self):
         """Rebuilds the menu and updates the running icon."""
         logger.debug("Rebuilding systray menu...")
         new_menu = await self._build_menu_async()
         self._current_menu = new_menu
         if self.icon and hasattr(self.icon, 'update_menu'):
              self.icon.menu = self._current_menu
              self.icon.update_menu()
              logger.debug("Systray menu updated.")
         else:
              logger.warning("Cannot update menu, pystray icon not running or doesn't support update.")

    # --- Menu Building (Async) ---
    async def _build_menu_async(self) -> menu:
        """Builds the pystray menu by fetching current state from GVM."""
        # Fetch necessary states from GVM asynchronously
        current_app_status = await self.gvm.get(STATE_APP_STATUS, "loading")
        modules_config = await self.gvm.get(CONFIG_MODULES_PREFIX, {})
        current_lang_code = await self.gvm.get(f"{CONFIG_GENERAL_PREFIX}.language", "en")

        menu_items = []
        
        # --- Example: Status Indicator ---
        menu_items.append(item(f"Status: {current_app_status}", None, enabled=False)) # Display only
        menu_items.append(menu.SEPARATOR)

        # --- Example: Module Toggles (Dynamically built) ---
        module_sub_items = []
        # Sort modules for consistent order
        # Assuming modules_config looks like {"module_name_enabled": True/False, ...}
        sorted_module_keys = sorted([k for k in modules_config.keys() if k.endswith('_enabled')])

        for module_key in sorted_module_keys:
            is_enabled = modules_config[module_key]
            # Extract user-friendly name from key
            module_name = module_key.replace('_enabled', '').replace('_', ' ').title()
            
            module_sub_items.append(item(
                f"{module_name}", # Use translated name if available
                functools.partial(self._toggle_module_callback, module_config_key=module_key), # Use partial
                checked=lambda item, key=module_key: modules_config.get(key, False),
            ))
        
        if module_sub_items:
            menu_items.append(item(_("Modules"), menu(*module_sub_items)))
            menu_items.append(menu.SEPARATOR)

        # --- Language Selection ---
        lang_sub_items = []
        for lc in SUPPORTED_LANGUAGES:
            lang_name = _(lc, default_text=lc) # Get translated lang name if available, else use code
            
            # Define a factory function for the callback to ensure lc is captured correctly
            def make_callback(lang_code_to_set):
                def callback(): # pystray expects a callable with no args for the action, or icon & item
                    self._on_language_selected(lang_code_to_set)
                return callback

            lang_sub_items.append(item(
                lang_name,
                make_callback(lc),
                checked=lambda menu_item_arg, lang_code_for_check=lc: lang_code_for_check == current_lang_code,
                radio=True 
            ))

        if lang_sub_items:
            menu_items.append(item(_("Language"), menu(*lang_sub_items)))
            menu_items.append(menu.SEPARATOR)

        # --- Exit Item ---
        menu_items.append(item(_('Exit'), self._on_exit_clicked))

        return menu(*menu_items)

    # --- Callbacks ---
    def _on_exit_clicked(self):
        """Callback when the Exit menu item is clicked."""
        logger.info("Exit requested from systray.")
        # Request shutdown via GVM
        asyncio.run_coroutine_threadsafe(self.gvm.request_shutdown(), self.main_loop)
        if self.icon:
             self.icon.stop()

    def _on_language_selected(self, lang_code: str):
        """Callback when a language is selected from the menu."""
        logger.info(f"Language selection triggered for: {lang_code}")
        asyncio.run_coroutine_threadsafe(
            self._async_set_language(lang_code),
            self.main_loop
        )

    async def _async_set_language(self, lang_code: str):
        """Async task to set language in GVM and i18n, then rebuild menu."""
        current_gvm_lang = await self.gvm.get(f"{CONFIG_GENERAL_PREFIX}.language", CURRENT_LANGUAGE)
        if current_gvm_lang == lang_code:
            logger.debug(f"Language {lang_code} is already set. No change needed.")
            return

        logger.info(f"Setting application language to {lang_code} via systray.")
        await self.gvm.set(f"{CONFIG_GENERAL_PREFIX}.language", lang_code)
        
        # Call i18n.set_language in the main event loop thread
        # This assumes i18n.set_language is thread-safe or benign if called this way.
        # If i18n.set_language has significant blocking I/O, it should be made async or run in an executor.
        self.main_loop.call_soon_threadsafe(set_language, lang_code)
        
        # It might take a moment for set_language to apply and GVM to notify watchers
        # of the config change. A small delay before rebuild, or rely on GVM notification.
        await asyncio.sleep(0.1) # Small delay to allow GVM update to propagate if needed
        await self._rebuild_menu_async()

    def _toggle_module_callback(self, module_config_key: str):
        """Handles toggling a module's enabled state via GVM."""
        logger.debug(f"Systray toggle requested for module key: {module_config_key}")
        # Schedule the async task to update GVM state
        asyncio.run_coroutine_threadsafe(
            self._async_toggle_module(module_config_key),
            self.main_loop
        )

    async def _async_toggle_module(self, module_config_key: str):
         """Async part of toggling module state and rebuilding menu."""
         current_value = await self.gvm.get(f"{CONFIG_MODULES_PREFIX}.{module_config_key}", False)
         new_value = not current_value
         logger.info(f"Setting GVM state {CONFIG_MODULES_PREFIX}.{module_config_key} to {new_value}")
         await self.gvm.set(f"{CONFIG_MODULES_PREFIX}.{module_config_key}", new_value)
         # Config change should trigger GVM's module lifecycle manager.
         # Rebuild the menu to reflect the change visually.
         await self._rebuild_menu_async()

    # --- Run Loop and Cleanup ---
    async def run_loop(self):
        """Starts the pystray thread and monitors for relevant GVM changes."""
        logger.info("SystrayUIManager run_loop starting.")
        if not (self.thread and self.thread.is_alive()):
             self._stop_event.clear()
             self.thread = threading.Thread(target=self._run_pystray, daemon=True, name="SystrayThread")
             self.thread.start()
        else:
            logger.warning("Systray thread already running.")

        # Monitor GVM state changes that require a menu rebuild
        # Example: Monitor config changes or specific status updates
        while not self._stop_event.is_set():
            try:
                # Wait specifically for changes relevant to the menu
                # This is more efficient than rebuilding on every minor change.
                # Example: Wait for changes in the modules config prefix
                await self.gvm.wait_for_change(CONFIG_MODULES_PREFIX) # Needs GVM to notify on prefix changes
                logger.debug("Detected change potentially affecting systray menu. Rebuilding.")
                await self._rebuild_menu_async()

            except asyncio.CancelledError:
                 logger.info("SystrayUIManager run_loop cancelled.")
                 break
            except Exception as e:
                logger.error(f"Error in SystrayUIManager run_loop: {e}", exc_info=True)
                await asyncio.sleep(5) # Avoid tight loop on error

        await self.cleanup()
        logger.info("SystrayUIManager run_loop finished.")

    async def cleanup(self):
        """Stops the pystray icon and thread."""
        logger.info("SystrayUIManager cleaning up...")
        self._stop_event.set() # Signal run_loop to exit
        if self.icon:
            logger.debug("Stopping pystray icon...")
            # Stop may need to be called from a different thread.
            # If this cleanup is triggered by GVM shutdown, it should be fine.
            try:
                 self.icon.stop()
            except Exception as e:
                 logger.warning(f"Error stopping pystray icon: {e}")
        if self.thread and self.thread.is_alive():
            logger.debug("Waiting for systray thread to join...")
            self.thread.join(timeout=1.0)
            if self.thread.is_alive():
                logger.warning("Systray thread did not stop gracefully.")
        self.thread = None
        self.icon = None
        logger.info("SystrayUIManager cleanup finished.")

# Note: Requires GVM to have get_main_loop().
# Assumes GVM can notify on prefix changes (e.g., "config.modules.*").
# Menu building logic is simplified; language selection needs more work if required. 