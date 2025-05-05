import json
import logging
import os
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

class ConfigManager:
    """Manages loading and saving of the application configuration file."""

    def __init__(self, config_path="config.json"):
        logger.info("--- ConfigManager.__init__ called ---") # Log start
        self.config_path = config_path
        self._config_data: Dict[str, Any] = {}
        # Use a standard lock for file I/O, as it might be called from different contexts
        self._file_lock = threading.Lock()
        self.load_config() # Load initial config on instantiation

    def load_config(self) -> Dict[str, Any]:
        """Loads configuration from the JSON file."""
        logger.info("--- ConfigManager.load_config called ---") # Log start
        with self._file_lock:
            try:
                if os.path.exists(self.config_path):
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        self._config_data = json.load(f)
                    logger.info(f"Configuration loaded from {self.config_path}")
                else:
                    # logger.warning(f"Configuration file not found at {self.config_path}. Using empty config.") # Commented out for testing
                    logger.debug("Calling _get_default_config...")
                    self._config_data = self._get_default_config()
                    logger.debug("Calling save_config with default data...")
                    self.save_config(self._config_data) # Save defaults if not found
                    logger.debug("Returned from save_config call.")
            except json.JSONDecodeError:
                logger.error(f"Error decoding JSON from {self.config_path}. Loading default config.", exc_info=True)
                self._config_data = self._get_default_config()
            except Exception as e:
                logger.error(f"Failed to load configuration: {e}", exc_info=True)
                self._config_data = self._get_default_config()
        return self._config_data.copy() # Return a copy

    def save_config(self, config_data: Dict[str, Any]) -> None:
        """Saves the provided configuration data to the JSON file."""
        logger.debug(f"Attempting to save config to {self.config_path}...") # Log start
        with self._file_lock:
            try:
                # Create directory if it doesn't exist
                os.makedirs(os.path.dirname(self.config_path) or '.', exist_ok=True)
                logger.debug(f"Directory check/creation done for {self.config_path}.") # Log dir check
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(config_data, f, indent=4)
                logger.info(f"Configuration saved successfully to {self.config_path}") # Log success
                self._config_data = config_data # Update internal cache
            except Exception as e:
                logger.error(f"Failed to save configuration: {e}", exc_info=True)
        logger.debug(f"save_config method finished for {self.config_path}.") # Log end

    def get_config(self) -> Dict[str, Any]:
        """Returns a copy of the current in-memory configuration."""
        # No lock needed for read if load/save handles synchronization
        return self._config_data.copy()

    def _get_default_config(self) -> Dict[str, Any]:
        """Returns a default configuration structure."""
        logger.debug("Generating default config structure...") # Log start
        # Define default settings here
        config = {
            "general": {
                "source_language": "en-US",
                "trigger_key": "middle",
                "initial_mode": "Dictation"
            },
            "modules": {
                "global_variables_ui_enabled": False,
                "input_manager_enabled": True,
                "background_audio_recorder_enabled": False,
                "stt_manager_enabled": True,
                "dictation_text_manager_enabled": True,
                "action_confirm_enabled": False,
                "action_executor_enabled": True,
                "keyboard_simulator_enabled": True, # Considered a utility, but might need init
                "openai_manager_enabled": True, # For translation/AI features
                "interim_text_ui_manager_enabled": True,
                "mic_ui_manager_enabled": True,
                "session_monitor_ui_enabled": True,
                "systray_ui_enabled": True
            },
            "translation": {
                "enabled": False,
                "target_language": "en",
                "model": "gpt-3.5-turbo" # Example model
            },
            "ui": {
                "tooltip_alpha": 0.85,
                "tooltip_bg": "lightyellow",
                "tooltip_fg": "black"
                # Add other UI-specific settings
            }
            # Add other sections as needed (e.g., Deepgram keys, OpenAI keys)
        }
        logger.debug("Default config structure generated.") # Log end
        return config

# Note: This adapted version removes the direct GVM update logic,
# as the GVM will now pull the config using load_config.
# The save operation might be triggered externally or via a GVM state change if needed. 