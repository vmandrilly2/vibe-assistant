import pystray
from pystray import MenuItem as item, Menu as menu
from PIL import Image, ImageDraw
import threading
import logging
import json
import os
import sys
from functools import partial # Import partial for cleaner callbacks

# --- Configuration Handling (Mirrors vibe_app.py logic initially) ---
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
  "general": {
    "min_duration_sec": 0.5,
    "selected_language": "en-US"
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
  }
}

def load_config():
    """Loads configuration from JSON file, creates default if not found."""
    if not os.path.exists(CONFIG_FILE):
        logging.warning(f"{CONFIG_FILE} not found. Creating default config.")
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            return DEFAULT_CONFIG
        except IOError as e:
            logging.error(f"Unable to create default config file {CONFIG_FILE}: {e}")
            return DEFAULT_CONFIG # Return default anyway
    else:
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded_config = json.load(f)
                logging.info(f"Loaded configuration from {CONFIG_FILE} for systray.")
                # TODO: Add validation/merging with defaults
                return loaded_config
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Error reading/decoding {CONFIG_FILE}: {e}. Using default config.")
            return DEFAULT_CONFIG
        except Exception as e:
             logging.error(f"Unexpected error loading config for systray: {e}. Using default config.")
             return DEFAULT_CONFIG

# --- Global State for UI ---
config = load_config()
config_reload_event = threading.Event() # Used to signal main app to reload
exit_app_event = None # Placeholder for the event from main app

# Define valid options
BUTTON_OPTIONS = ["left", "right", "middle", "x1", "x2"]
COMMAND_BUTTON_OPTIONS = BUTTON_OPTIONS + [None] # Add None option for command button
MODIFIER_OPTIONS = ["shift", "ctrl", "alt", None] # Add None option for modifier
LANGUAGE_OPTIONS = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "fr-FR": "French",
    "es-ES": "Spanish",
    "de-DE": "German",
    # Add more languages as needed (ensure Deepgram supports them)
}

# --- Helper Functions ---
def create_image(width, height, color1, color2):
    """Creates a simple placeholder image for the systray icon."""
    image = Image.new('RGB', (width, height), color1)
    dc = ImageDraw.Draw(image)
    dc.rectangle(
        (width // 2, 0, width, height // 2),
        fill=color2)
    dc.rectangle(
        (0, height // 2, width // 2, height),
        fill=color2)
    return image

def save_config():
    """Saves the current config state back to the JSON file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logging.info(f"Configuration saved to {CONFIG_FILE}.")
        config_reload_event.set() # Signal that config has changed
        # config_reload_event.clear() # REMOVE - Let the main app clear it after processing
        # Note: We might need a more robust signaling mechanism later
    except IOError as e:
        logging.error(f"Error saving config file {CONFIG_FILE}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error saving config: {e}")

def update_general_setting_callback(icon, item, setting_key, value):
    """Callback wrapper called by pystray, passes specific args."""
    logging.debug(f"Updating general setting: {setting_key} = {value}")
    if "general" not in config:
        config["general"] = {}
    config["general"][setting_key] = value
    save_config()

# --- Menu Callback Functions ---
def on_exit_clicked(icon, item):
    logging.info("Exit requested from systray menu.")
    if exit_app_event:
        logging.debug("Setting exit_app_event.")
        exit_app_event.set() # Signal the main application to exit
    else:
        logging.warning("exit_app_event not set in systray_ui.")
    icon.stop() # Stop the systray icon itself
    icon.menu = build_menu()
    icon.update_menu()

def on_reload_config_clicked(icon, item):
    global config
    logging.info("Reload config requested from systray menu.")
    config = load_config()
    # Signal main app to also reload its internal config state
    config_reload_event.set()
    # Rebuild the menu to reflect the reloaded config
    icon.menu = build_menu()
    icon.update_menu()

def update_trigger_setting_callback(icon, item, setting_key, value):
    """Callback wrapper called by pystray for trigger settings."""
    logging.debug(f"Updating trigger setting: {setting_key} = {value}")
    if "triggers" not in config:
        config["triggers"] = {}
    config["triggers"][setting_key] = value
    save_config() # Save the updated config
    # No need to rebuild menu here, checked state handles visual update
    # Rebuilding the whole menu on every click can be slow/flickery
    # icon.menu = build_menu()
    # icon.update_menu()

# --- Functions to build the menu dynamically ---
def build_general_menu():
    # --- Language Submenu ---
    current_lang = config.get("general", {}).get("selected_language", "en-US")
    lang_items = []
    for code, name in LANGUAGE_OPTIONS.items():
        lang_items.append(
            item(
                name, # Display friendly name
                partial(update_general_setting_callback, setting_key='selected_language', value=code),
                checked=lambda item, c=code: config.get("general", {}).get("selected_language") == c,
                radio=True
            )
        )
    lang_submenu = menu(*lang_items)

    # --- Min Duration (Example: Display only for now) ---
    min_dur = config.get("general", {}).get("min_duration_sec", "N/A")
    # TODO: Add action to change min_duration (would likely need a text input dialog)
    min_dur_item = item(f'Min Duration (s): {min_dur}', None, enabled=False)

    return [
        item('Language', lang_submenu),
        min_dur_item
    ]

def build_triggers_menu():
    # --- Dictation Button Submenu ---
    current_dict_btn = config.get("triggers", {}).get("dictation_button", "middle")
    dictation_items = []
    for btn in BUTTON_OPTIONS:
        dictation_items.append(
            item(
                btn.capitalize(),
                partial(update_trigger_setting_callback, setting_key='dictation_button', value=btn),
                checked=lambda item, b=btn: config.get("triggers", {}).get("dictation_button") == b,
                radio=True
            )
        )
    dictation_submenu = menu(*dictation_items)

    # --- Command Button Submenu ---
    current_cmd_btn = config.get("triggers", {}).get("command_button")
    command_items = []
    for btn in COMMAND_BUTTON_OPTIONS:
        btn_str = str(btn) if btn is not None else "None"
        command_items.append(
            item(
                btn_str.capitalize(),
                partial(update_trigger_setting_callback, setting_key='command_button', value=btn),
                checked=lambda item, b=btn: config.get("triggers", {}).get("command_button") == b,
                radio=True
            )
        )
    command_submenu = menu(*command_items)

    # --- Command Modifier Submenu ---
    current_cmd_mod = config.get("triggers", {}).get("command_modifier")
    modifier_items = []
    for mod in MODIFIER_OPTIONS:
        mod_str = str(mod) if mod is not None else "None"
        modifier_items.append(
            item(
                mod_str.capitalize(),
                partial(update_trigger_setting_callback, setting_key='command_modifier', value=mod),
                checked=lambda item, m=mod: config.get("triggers", {}).get("command_modifier") == m,
                radio=True
            )
        )
    modifier_submenu = menu(*modifier_items)

    return [
        item('Dictation Button', dictation_submenu),
        item('Command Button', command_submenu),
        item('Command Modifier', modifier_submenu)
    ]

def build_tooltip_menu():
    alpha = config.get("tooltip", {}).get("alpha", "N/A")
    bg = config.get("tooltip", {}).get("bg_color", "N/A")
    fg = config.get("tooltip", {}).get("fg_color", "N/A")
    font = config.get("tooltip", {}).get("font_family", "N/A")
    size = config.get("tooltip", {}).get("font_size", "N/A")

    # TODO: Add actions to change these settings
    return [
        item(f'Transparency: {alpha}', None),
        item(f'Background: {bg}', None),
        item(f'Text Color: {fg}', None),
        item(f'Font: {font}', None),
        item(f'Font Size: {size}', None)
    ]

def build_menu():
    """Builds the main systray menu structure."""
    return menu(
        item('General', menu(*build_general_menu())),
        item('Triggers', menu(*build_triggers_menu())),
        item('Tooltip', menu(*build_tooltip_menu())),
        menu.SEPARATOR,
        item('Reload Config', on_reload_config_clicked),
        item('Exit', on_exit_clicked)
    )

# --- Main Systray Function ---
def run_systray(exit_event_arg):
    """Runs the systray icon loop."""
    global exit_app_event # Allow modification of the global placeholder
    exit_app_event = exit_event_arg # Store the passed event
    logging.info(f"Initializing systray UI... Exit event set: {exit_app_event is not None}")

    try:
        # Create a placeholder icon image
        # You might want to replace this with an actual .ico file later
        image = create_image(64, 64, 'black', 'red')

        icon = pystray.Icon("vibe_assistant", image, "Vibe Assistant", menu=build_menu())
        logging.info("Running systray icon...")
        icon.run() # This blocks until icon.stop() is called
        logging.info("Systray icon stopped.")

    except Exception as e:
        logging.error(f"Error running systray: {e}", exc_info=True)

# --- Entry Point (if run directly, for testing) ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    # Example of how the main app might use the event:
    test_exit_event = threading.Event()
    def config_watcher():
        while not test_exit_event.is_set(): # Exit watcher when main app exits
            if config_reload_event.wait(timeout=0.5): # Wait for event
                logging.info("Main App detected config reload event!")
                # Here the main app would reload its own config
            # Add a check for app exit condition
            # time.sleep(0.1) # Don't poll too fast
        logging.info("Config watcher loop exiting.")

    # Start the watcher thread (for demo purposes)
    watcher_thread = threading.Thread(target=config_watcher, daemon=True)
    watcher_thread.start()

    run_systray(test_exit_event) # Pass the event
    logging.info("Systray finished.") 