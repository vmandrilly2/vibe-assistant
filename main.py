import asyncio
import logging
import logging.config
import signal
import sys
import os # Added for LOCALE_DIR
from dotenv import load_dotenv

# --- App Specific Imports ---
from global_variables_manager import GlobalVariablesManager
from config_manager import ConfigManager
from constants import (
    CONFIG_GENERAL_PREFIX, 
    # Add other constants if GVM needs them directly at init
)
# Ensure i18n is imported and set_language can be called early
from i18n import set_language as i18n_set_language, _ 

# Load environment variables from .env file
load_dotenv()

# --- Logger Setup ---
# Get the root logger
logger = logging.getLogger() # Get root logger to set its level initially
# logger = logging.getLogger(__name__) # This would be for a specific module logger

def setup_logging(log_level_str="INFO"):
    """Configures basic logging for the application."""
    numeric_level = getattr(logging, log_level_str.upper(), logging.INFO)
    
    # Define a more detailed format
    log_format = "%(asctime)s %(levelname)s: [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Apply basicConfig to the root logger. 
    # If handlers are already configured (e.g., by a library like pystray), 
    # basicConfig might not have the desired effect on its own without force=True (Python 3.8+).
    # However, for console output, this is usually sufficient.
    logging.basicConfig(level=numeric_level, format=log_format, datefmt=date_format, force=True) # Added force=True for robustness

    # Set specific logger levels for verbose libraries
    logging.getLogger("deepgram").setLevel(logging.INFO)
    logging.getLogger("websockets").setLevel(logging.INFO)
    # Example: logging.getLogger("pystray").setLevel(logging.WARNING) 

    # Log the initial root logger level
    # Note: logger.level will be 0 (NOTSET) if not explicitly set on it, it inherits from parent or uses effective level.
    # logging.root.level will give the actual root level.
    logger.info(f"Root logger effective level set to: {logging.getLevelName(logging.root.getEffectiveLevel())}. Requested: {log_level_str}")


# --- Global Managers Instantiation ---
# ConfigManager needs to be instantiated before GVM if GVM depends on it at init
config_manager = ConfigManager('config.json')
gvm = GlobalVariablesManager(config_manager) # Pass config_manager to GVM

# --- Main Application Logic (Simplified for Restoration) ---
# This is a simplified version focusing on restoring core functionality.
# The original main.py had more complex setup and module management.
# We'll expand this as we verify components.

async def main_async():
    """Asynchronous main function to run the application."""
    # ConfigManager is already instantiated globally, load the config here
    # or ensure it's loaded before GVM needs it if load_initial_config relies on a prior raw load.
    # For simplicity, assuming ConfigManager loads its config at its own __init__ or a dedicated load method.
    # The load_initial_config in GVM will then transfer this to GVM state.
    
    initial_config = config_manager.get_config() # Assuming ConfigManager has a method to get the loaded config
    if not initial_config: # Or if it's empty, try loading it explicitly
        initial_config = config_manager.load_config()

    log_level = initial_config.get("general", {}).get("log_level", "INFO")
    setup_logging(log_level) 

    logger.info("Starting Vibe Assistant application (Restored main)...")
    
    initial_lang = initial_config.get("general", {}).get("language", "en")
    logger.info(f"Setting initial application language to: {initial_lang}")
    i18n_set_language(initial_lang) 

    # GVM is already instantiated globally with config_manager.
    # Now, load the configuration into GVM's state.
    await gvm.load_initial_config() # Pass no arguments

    try:
        logger.info("Calling gvm.run()...") 
        await gvm.run() 
    except asyncio.CancelledError:
        logger.info("Main_async task cancelled.")
    except KeyboardInterrupt: # Should be caught by signal_handler now
        logger.info("KeyboardInterrupt caught in main_async, should be handled by signal handler.")
    finally:
        logger.info("Main_async finished. GVM shutdown should be in progress if not already done.")
        # GVM's shutdown process should handle module cleanup.

async def signal_handler(sig_name_or_event, gvm_instance):
    # This function will now be primarily called by KeyboardInterrupt logic
    logger.warning(f"Shutdown requested via {sig_name_or_event}. Requesting GVM shutdown.")
    await gvm_instance.request_shutdown()

if __name__ == "__main__":
    setup_logging() 
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught in __main__. Initiating graceful shutdown.")
        # Need to run the async signal_handler (now a general shutdown handler)
        # If gvm is still accessible and loop can be retrieved or a new one used for this task
        if 'gvm' in globals() and gvm: # Check if gvm was initialized
            try:
                # Create a new event loop to run the shutdown if the main one is stopped/stopping
                asyncio.run(signal_handler("KeyboardInterrupt", gvm))
            except RuntimeError as e:
                logger.error(f"RuntimeError during KeyboardInterrupt shutdown: {e}. Might be loop issues.")
                # Fallback or force exit if necessary
        else:
            logger.warning("GVM not available for graceful shutdown during KeyboardInterrupt.")
    except Exception as e:
        logger.critical(f"Unhandled exception in __main__: {e}", exc_info=True)
    finally:
        logger.info("=== Application End ===")
