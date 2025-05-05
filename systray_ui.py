# systray_ui.py
import asyncio
import logging
import threading
import time
from typing import Optional, Any

import pystray
from pystray import MenuItem as item, Menu as menu
from PIL import Image, ImageDraw

# Assuming GVM and constants are accessible
# from global_variables_manager import GlobalVariablesManager
from constants import (
    # Import necessary constants like STATE keys for config, APP_NAME etc.
    APP_NAME, MODE_DICTATION, MODE_COMMAND, # Example constants
    CONFIG_MODULES_PREFIX, STATE_APP_STATUS,
)
# Assuming i18n is set up and provides _
from i18n import _, set_language, CURRENT_LANGUAGE # Import variable, not function

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
            # Create placeholder image
            image = create_image(64, 64, 'black', 'white') # Replace with actual icon later

            # Build initial menu (requires async access to GVM, run via threadsafe)
            asyncio.run_coroutine_threadsafe(self._build_and_set_initial_menu(), self.main_loop)
            
            # Wait briefly for initial menu build if needed
            # This might require a threading.Event to signal completion from _build_and_set_initial_menu
            time.sleep(0.5)

            if self._current_menu is None:
                 logger.warning("Systray menu not built before starting icon. Using default exit.")
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
         self._current_menu = await self._build_menu_async()
         logger.debug("Initial systray menu built.")
         # If icon is already running (unlikely here, but good practice), update it
         if self.icon and hasattr(self.icon, 'update_menu'):
              self.icon.menu = self._current_menu
              self.icon.update_menu()

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
        # Example: Get module enable states
        modules_config = await self.gvm.get(CONFIG_MODULES_PREFIX, {})

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
                lambda icon, item, key=module_key: self._toggle_module_callback(key), # Pass key
                checked=lambda item, key=module_key: modules_config.get(key, False), # Check current state
            ))
        
        if module_sub_items:
            menu_items.append(item(_("Modules"), menu(*module_sub_items)))
            menu_items.append(menu.SEPARATOR)

        # --- Language Selection (Simplified Example) ---
        # Needs more complex logic similar to old version if recent/full list is needed
        # current_lang = await self.gvm.get("config.general.source_language", "en-US")
        # menu_items.append(item(f"Lang: {current_lang}", None, enabled=False)) # Placeholder

        # --- Exit Item ---
        menu_items.append(item(_('Exit'), self._on_exit_clicked))

        return menu(*menu_items)

    # --- Callbacks ---
    def _on_exit_clicked(self):
        """Callback when the Exit menu item is clicked."""
        logger.info("Exit requested from systray.")
        # Request shutdown via GVM
        asyncio.run_coroutine_threadsafe(self.gvm.request_shutdown(), self.main_loop)
        # Stop the pystray icon itself (might need to be called from its own thread)
        if self.icon:
             # pystray stop needs to be called from a different thread than the one running it
             # Usually called from a callback handler which runs in a separate thread.
             # If called from GVM shutdown, ensure it's handled correctly.
             self.icon.stop()

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