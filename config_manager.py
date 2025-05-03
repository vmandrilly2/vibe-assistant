import json
import os
import logging
import threading
from copy import deepcopy # To return copies of nested dicts

# Define constants related to configuration file
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
  "general": {
    "min_duration_sec": 0.5,
    "selected_language": "en-US",
    "target_language": None,
    "openai_model": "gpt-4.1-nano",
    "active_mode": "Dictation",
    "recent_source_languages": [],
    "recent_target_languages": []
  },
  "triggers": {
    "dictation_button": "middle",
    "command_button": None,
    "command_modifier": None
  },
  "tooltip": {
    "alpha": 0.85,
    "bg_color": "lightyellow",
    "fg_color": "black",
    "font_family": "Arial",
    "font_size": 10
  },
  "modules": {
    "tooltip_enabled": True,
    "status_indicator_enabled": True,
    "action_confirm_enabled": True,
    "translation_enabled": True,
    "command_interpretation_enabled": False,
    "audio_buffer_enabled": True
  }
}

class ConfigManager:
    """Manages loading, accessing, and saving application configuration."""

    def __init__(self, config_file=CONFIG_FILE):
        """Initializes the ConfigManager and loads the configuration."""
        self.config_file = config_file
        self._config = {}
        self._lock = threading.Lock() # Protects access to self._config during load/save
        self.reload() # Load initial config

    def _load_config_from_file(self):
        """Loads configuration from the JSON file, merging with defaults."""
        loaded_config = {}
        if not os.path.exists(self.config_file):
            logging.warning(f"{self.config_file} not found. Creating default config.")
            # Create a deep copy to avoid modifying the original DEFAULT_CONFIG
            loaded_config = deepcopy(DEFAULT_CONFIG)
            try:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(loaded_config, f, indent=2, ensure_ascii=False)
            except IOError as e:
                logging.error(f"Unable to create default config file {self.config_file}: {e}")
                # Still return the default config even if saving failed
        else:
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                # --- Merge with defaults for missing keys/sections ---
                default_copy = deepcopy(DEFAULT_CONFIG)
                for section, defaults in default_copy.items():
                    if section not in loaded_config:
                        loaded_config[section] = defaults
                        logging.debug(f"ConfigManager: Added missing section: {section}")
                    elif isinstance(defaults, dict):
                        if not isinstance(loaded_config.get(section), dict):
                            logging.warning(f"ConfigManager: Config section '{section}' is not a dictionary. Resetting to default.")
                            loaded_config[section] = defaults
                        else:
                            # Merge keys within the section
                            for key, default_value in defaults.items():
                                # Use .get() for safer access within the loaded section
                                if loaded_config.get(section, {}).get(key) is None:
                                     # Ensure key exists if section does, even if value is None in file
                                     if key not in loaded_config[section]:
                                          loaded_config[section][key] = default_value
                                          logging.debug(f"ConfigManager: Added missing key: {section}.{key}")
                                # Handle case where key is entirely missing
                                elif key not in loaded_config.get(section, {}):
                                    loaded_config[section][key] = default_value
                                    logging.debug(f"ConfigManager: Added missing key: {section}.{key}")


            except json.JSONDecodeError as e:
                logging.error(f"Error decoding {self.config_file}: {e}. Using default config.")
                loaded_config = deepcopy(DEFAULT_CONFIG)
            except IOError as e:
                logging.error(f"Unable to read config file {self.config_file}: {e}. Using default config.")
                loaded_config = deepcopy(DEFAULT_CONFIG)
            except Exception as e:
                logging.error(f"Unexpected error loading config: {e}. Using default config.")
                loaded_config = deepcopy(DEFAULT_CONFIG)

        logging.info(f"ConfigManager loaded configuration from {self.config_file}")
        return loaded_config

    def reload(self):
        """Reloads the configuration from the file."""
        logging.info("ConfigManager: Reloading configuration...")
        with self._lock:
            self._config = self._load_config_from_file()
        logging.info("ConfigManager: Configuration reloaded.")
        # Optional: Implement a notification mechanism here if needed later

    def get(self, key_path: str, default=None):
        """
        Gets a configuration value using a dot-separated key path.

        Args:
            key_path: The dot-separated path to the key (e.g., "general.selected_language").
            default: The value to return if the key is not found.

        Returns:
            The configuration value or the default. Returns a deep copy for mutable types (dict, list).
        """
        with self._lock:
            try:
                value = self._config
                for key in key_path.split('.'):
                    if isinstance(value, dict):
                        value = value[key]
                    else:
                        # Trying to access a key on a non-dict value
                        raise KeyError(f"Invalid key path: '{key_path}', segment '{key}' accessed on non-dict.")
                # Return a deep copy for dictionaries or lists to prevent callers
                # from modifying the internal state unintentionally.
                return deepcopy(value) if isinstance(value, (dict, list)) else value
            except (KeyError, TypeError) as e:
                # logging.debug(f"Config key '{key_path}' not found or invalid path: {e}. Returning default: {default}")
                return default

    def get_section(self, section_name: str) -> dict:
        """
        Gets an entire configuration section as a dictionary.

        Args:
            section_name: The name of the section (e.g., "tooltip").

        Returns:
            A deep copy of the configuration section dictionary, or an empty dict if not found.
        """
        with self._lock:
            section = self._config.get(section_name, {})
            return deepcopy(section) if isinstance(section, dict) else {}

    def update(self, key_path: str, value):
        """
        Updates a configuration value in memory using a dot-separated key path.
        Note: This does NOT automatically save to file. Call save() separately.

        Args:
            key_path: The dot-separated path to the key (e.g., "general.selected_language").
            value: The new value to set.
        """
        with self._lock:
            try:
                keys = key_path.split('.')
                current_level = self._config
                for i, key in enumerate(keys[:-1]): # Iterate up to the second-to-last key
                    if key not in current_level or not isinstance(current_level[key], dict):
                        # If a key is missing or not a dict, create the necessary dict structure
                        current_level[key] = {}
                    current_level = current_level[key]

                final_key = keys[-1]
                current_level[final_key] = value
                logging.debug(f"ConfigManager: Updated '{key_path}' in memory to: {value}")
                # Optionally: Add validation here based on key path or expected type
            except Exception as e:
                logging.error(f"Error updating config key '{key_path}': {e}", exc_info=True)

    def save(self):
        """Saves the current in-memory configuration back to the JSON file."""
        with self._lock:
            # Create a copy to save, ensuring thread safety if reads happen concurrently
            config_to_save = deepcopy(self._config)
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
            logging.info(f"ConfigManager saved configuration to {self.config_file}.")
            # No need to signal reload event here, this *is* the manager.
            # The caller (e.g., vibe_app responding to systray) might signal others.
        except IOError as e:
            logging.error(f"Error saving config file {self.config_file}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error saving config: {e}") 