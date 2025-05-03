import pystray
from pystray import MenuItem as item, Menu as menu
from PIL import Image, ImageDraw
import threading
import logging
import json
import os
import sys
from functools import partial # Import partial for cleaner callbacks

# --- i18n Import --- >
import i18n
from i18n import load_translations, _, ALL_DICTATION_REPLACEMENTS, get_current_language

# --- Constants Import --- (Assuming constants.py exists)
try:
    from constants import (
        ALL_LANGUAGES, NATIVE_LANGUAGE_NAMES, ALL_LANGUAGES_TARGET,
        MODE_DICTATION, MODE_COMMAND, AVAILABLE_MODES,
        PYNPUT_BUTTON_MAP, PYNPUT_MODIFIER_MAP
    )
except ImportError:
    logging.error("Systray failed to import constants. Using fallback definitions.")
    # Add fallback constants here if necessary
    ALL_LANGUAGES = {"en-US": "English (US)", "fr-FR": "French"}
    NATIVE_LANGUAGE_NAMES = {"en-US": "English (US)", "fr-FR": "Français"}
    ALL_LANGUAGES_TARGET = {None: "None"}
    MODE_DICTATION = "Dictation"
    MODE_COMMAND = "Command"
    AVAILABLE_MODES = {MODE_DICTATION: "Dictation Mode", MODE_COMMAND: "Command Mode"}
    PYNPUT_BUTTON_MAP = {} # Simplified fallback
    PYNPUT_MODIFIER_MAP = {} # Simplified fallback


# --- Configuration File Path ---
CONFIG_FILE = "config.json"
# DEFAULT_CONFIG remains the same, used by load_config if file missing

# --- Default Config Definition (Keep for initial creation/comparison) ---
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
# --- End Default Config ---

def load_config():
    """Loads configuration from JSON file, creates default if not found.
       Still needed for building the menu representation.
    """
    if not os.path.exists(CONFIG_FILE):
        logging.warning(f"Systray: {CONFIG_FILE} not found. Creating default config.")
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
            return DEFAULT_CONFIG
        except IOError as e:
            logging.error(f"Systray: Unable to create default config file {CONFIG_FILE}: {e}")
            return DEFAULT_CONFIG # Return default anyway
    else:
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
            # Perform merge with defaults ONLY for menu building robustness
            # The main ConfigManager handles the canonical merge
            from copy import deepcopy
            default_copy = deepcopy(DEFAULT_CONFIG)
            for section, defaults in default_copy.items():
                if section not in loaded_config:
                    loaded_config[section] = defaults
                elif isinstance(defaults, dict):
                    if not isinstance(loaded_config.get(section), dict):
                        loaded_config[section] = defaults # Reset if not dict
                    else:
                        for key, default_value in defaults.items():
                            if key not in loaded_config.get(section, {}):
                                loaded_config[section][key] = default_value
            logging.info(f"Systray loaded configuration from {CONFIG_FILE} for menu build.")
            return loaded_config
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Systray: Error reading/decoding {CONFIG_FILE}: {e}. Using default for menu build.")
            return DEFAULT_CONFIG
        except Exception as e:
             logging.error(f"Systray: Unexpected error loading config: {e}. Using default for menu build.")
             return DEFAULT_CONFIG

# --- Global State for UI ---
# Load config initially just for the first menu build
config = load_config()
# --- Load Initial Translations --- >
load_translations(config.get("general", {}).get("selected_language"))
logging.info(f"Systray initial translations loaded for language: {i18n.get_current_language()}")
# --- End Load Initial Translations --- >
config_reload_event = threading.Event() # Used to signal main app to reload
exit_app_event = None # Placeholder for the event from main app

# Define valid options for triggers (moved from constants.py if needed, but prefer import)
BUTTON_OPTIONS = ["left", "right", "middle", "x1", "x2"]
COMMAND_BUTTON_OPTIONS = BUTTON_OPTIONS + [None]
MODIFIER_OPTIONS = ["shift", "ctrl", "alt", None]

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

# --- REMOVED save_config() ---

def _update_config_file(section, key, value):
    """Loads the current config, updates a specific key, saves it back."""
    try:
        # 1. Load current config from file
        current_config = load_config() # Use the existing load function
        # 2. Update the value
        if section not in current_config: current_config[section] = {}
        if key not in current_config[section]: current_config[section] = {} # Ensure section exists
        # Handle nested keys if needed (e.g., section.key.subkey) - Simplified for now
        current_config[section][key] = value
        # 3. Save back to file
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, indent=2, ensure_ascii=False)
        logging.debug(f"Systray updated {section}.{key} = {value} in {CONFIG_FILE}")
        return True
    except (IOError, json.JSONDecodeError) as e:
        logging.error(f"Systray: Error updating config file for {section}.{key}: {e}")
        return False
    except Exception as e:
        logging.error(f"Systray: Unexpected error updating config file: {e}")
        return False

def _update_recent_languages(lang_type, value):
    """Updates the recent language list in the config file."""
    try:
        current_config = load_config()
        setting_key = 'selected_language' if lang_type == 'source' else 'target_language'
        recent_list_key = "recent_source_languages" if lang_type == "source" else "recent_target_languages"
        MAX_RECENT_LANGS = 10

        if "general" not in current_config: current_config["general"] = {}
        current_config["general"][setting_key] = value # Update the selected language

        recent_list = current_config["general"].get(recent_list_key, [])
        if value in recent_list: recent_list.remove(value)
        recent_list.insert(0, value)
        current_config["general"][recent_list_key] = recent_list[:MAX_RECENT_LANGS]

        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, indent=2, ensure_ascii=False)
        logging.debug(f"Systray updated recent languages and {setting_key} in {CONFIG_FILE}")
        return True
    except Exception as e:
        logging.error(f"Systray: Error updating recent languages in config file: {e}")
        return False


# --- Modified Callbacks ---

def update_general_setting_callback(icon, item, setting_key, value):
    """Callback wrapper - Updates config file and signals reload."""
    logging.debug(f"Systray callback: Update general setting: {setting_key} = {value}")

    # Handle language updates separately to manage recent lists
    if setting_key in ['selected_language', 'target_language']:
        lang_type = 'source' if setting_key == 'selected_language' else 'target'
        if _update_recent_languages(lang_type, value):
            # Reload translations if source language changed
            if setting_key == 'selected_language':
                logging.info(f"Systray source language changed to {value}. Reloading translations.")
                load_translations(value)
            config_reload_event.set() # Signal main app to reload its ConfigManager
            # Rebuild menu to reflect updated recent lists and potentially new translations
            icon.menu = build_menu()
            icon.update_menu()
    else:
        # Handle other general settings (like active_mode)
        if _update_config_file("general", setting_key, value):
            config_reload_event.set() # Signal main app to reload
            # Check state might be sufficient for these, but rebuild for safety?
            # Rebuild is safer if the display text depends on the setting (though unlikely here)
            # icon.menu = build_menu() # Optional rebuild
            # icon.update_menu() # Optional update

def update_trigger_setting_callback(icon, item, setting_key, value):
    """Callback wrapper for trigger settings."""
    logging.debug(f"Systray callback: Update trigger setting: {setting_key} = {value}")
    if _update_config_file("triggers", setting_key, value):
        config_reload_event.set() # Signal main app to reload
        # Check state should be sufficient here, no menu rebuild needed

def update_mode_setting_callback(icon, item, value):
    """Callback to update the active mode."""
    logging.debug(f"Systray callback: Update active mode: {value}")
    # Use the general setting update logic
    update_general_setting_callback(icon, item, "active_mode", value)
    # No separate signaling or menu rebuild needed here, handled by general callback

def update_module_setting_callback(icon, item, module_key):
    """Callback to toggle a module's activation state."""
    # 1. Load current config to get the current value
    current_config = load_config()
    current_value = current_config.get("modules", {}).get(module_key, True) # Default to true
    new_value = not current_value
    logging.debug(f"Systray callback: Toggling module setting: {module_key} = {new_value}")
    # 2. Update the file
    if _update_config_file("modules", module_key, new_value):
        logging.info(f"Module '{module_key}' set to {new_value}. Redémarrage de l'application requis pour appliquer.")
        config_reload_event.set() # Signal main app to reload (though restart is needed)
        # Check state handles visual update, no rebuild needed


# --- Menu Callback Functions ---
def on_exit_clicked(icon, item):
    logging.info("Exit requested from systray menu.")
    if exit_app_event:
        logging.debug("Setting exit_app_event.")
        exit_app_event.set() # Signal the main application to exit
    else:
        logging.warning("exit_app_event not set in systray_ui.")
    icon.stop() # Stop the systray icon itself


def on_reload_config_clicked(icon, item):
    global config # Need to update the local config used for menu building
    logging.info("Reload config requested from systray menu.")
    # Reload local config for menu building
    config = load_config()
    # Reload translations based on potentially changed language
    load_translations(config.get("general", {}).get("selected_language"))
    logging.info(f"Systray reloaded translations for language: {i18n.get_current_language()}")
    # Signal main app to reload its ConfigManager
    config_reload_event.set()
    # Rebuild the menu to reflect the reloaded config
    icon.menu = build_menu()
    icon.update_menu()


# --- Functions to build the menu dynamically (Largely unchanged, reads local 'config') ---
def build_mode_menu():
    """Builds the Mode selection submenu."""
    general_cfg = config.get("general", {})
    current_mode = general_cfg.get("active_mode", "Dictation")

    mode_items = []
    for mode_name, display_name in AVAILABLE_MODES.items():
        translated_name = _(f"mode_names.{mode_name}", default=display_name)
        mode_items.append(
            item(
                translated_name,
                partial(update_mode_setting_callback, value=mode_name),
                checked=lambda item, m=mode_name: config.get("general", {}).get("active_mode") == m, # Check reloaded config
                radio=True
            )
        )
    return menu(*mode_items)

def build_language_source_menu():
    """Builds the Langue Source submenu."""
    MAX_RECENT_DISPLAY = 3
    general_cfg = config.get("general", {})
    current_source_lang = general_cfg.get("selected_language")
    # Ensure recent list exists and is a list
    recent_source_languages_raw = general_cfg.get("recent_source_languages", [])
    if not isinstance(recent_source_languages_raw, list):
         logging.warning("Systray: recent_source_languages is not a list, using empty.")
         recent_source_languages_raw = []
    recent_source_codes = [code for code in recent_source_languages_raw if code != current_source_lang][:MAX_RECENT_DISPLAY]

    def create_lang_item(code):
        setting_key = 'selected_language'
        english_name = ALL_LANGUAGES.get(code, code)
        native_name = NATIVE_LANGUAGE_NAMES.get(code, english_name)
        return item(
            native_name,
            partial(update_general_setting_callback, setting_key=setting_key, value=code),
            # Check against the *current* config state when menu is shown
            checked=lambda item, c=code: config.get("general", {}).get(setting_key) == c,
            radio=True
        )

    source_lang_items = [create_lang_item(code) for code in recent_source_codes]

    other_source_langs = {
        k: v for k, v in sorted(ALL_LANGUAGES.items(), key=lambda item: item[1])
        if k not in recent_source_codes and k != current_source_lang
    }

    if other_source_langs:
        other_source_submenu_items = []
        for code, english_name in other_source_langs.items():
            native_name = NATIVE_LANGUAGE_NAMES.get(code, english_name)
            other_source_submenu_items.append(
                item(
                    native_name,
                    partial(update_general_setting_callback, setting_key='selected_language', value=code),
                    checked=lambda item, c=code: config.get("general", {}).get("selected_language") == c,
                    radio=True
                )
            )
        other_source_submenu = menu(*other_source_submenu_items)
        if recent_source_codes: source_lang_items.append(menu.SEPARATOR)
        source_lang_items.append(item(_('systray.menu.other_languages', default='Autres langues'), other_source_submenu))
    elif not recent_source_codes:
        source_lang_items.append(item(_('systray.menu.no_other_languages', default="No other languages available"), None, enabled=False))

    return menu(*source_lang_items)


def build_language_target_menu():
    """Builds the Langue Cible submenu."""
    MAX_RECENT_TARGET_DISPLAY = 7
    general_cfg = config.get("general", {})
    # Ensure recent list exists and is a list
    recent_target_languages_raw = general_cfg.get("recent_target_languages", [])
    if not isinstance(recent_target_languages_raw, list):
         logging.warning("Systray: recent_target_languages is not a list, using empty.")
         recent_target_languages_raw = []
    recent_target_codes = recent_target_languages_raw[:MAX_RECENT_TARGET_DISPLAY]


    def create_lang_item(code):
        setting_key = 'target_language'
        default_name = ALL_LANGUAGES_TARGET.get(code, f"Unknown ({code})")
        name = _(f"language_names.{code}", default=default_name)
        return item(
            name,
            partial(update_general_setting_callback, setting_key=setting_key, value=code),
            checked=lambda item, c=code: config.get("general", {}).get(setting_key) == c, # Check current config
            radio=True
        )

    target_lang_items = []
    # Add "None" first if not in recent (or always add it?) - Always add None first.
    target_lang_items.append(
        item(
            _(f"language_names.none", default="Aucune (Dictée seulement)"),
            partial(update_general_setting_callback, setting_key='target_language', value=None),
            checked=lambda item: config.get("general", {}).get("target_language") is None,
            radio=True
        )
    )

    processed_for_recent = {None} # Keep track of what's added to avoid duplicates if None is recent
    for code in recent_target_codes:
        if code not in processed_for_recent:
             target_lang_items.append(create_lang_item(code))
             processed_for_recent.add(code)

    other_target_langs = {
        k: v for k, v in sorted(ALL_LANGUAGES.items(), key=lambda item: item[1])
        # Ensure we don't add languages already added in the recent list or None
        if k not in processed_for_recent
    }

    if other_target_langs:
        other_target_submenu = menu(*[
            item(
                _(f"language_names.{code}", default=name),
                partial(update_general_setting_callback, setting_key='target_language', value=code),
                checked=lambda item, c=code: config.get("general", {}).get("target_language") == c,
                radio=True
            ) for code, name in other_target_langs.items()
        ])
        # Add separator only if recent items were added after "None"
        if len(target_lang_items) > 1:
             target_lang_items.append(menu.SEPARATOR)
        target_lang_items.append(item(_('systray.menu.other_languages', default='Autres langues'), other_target_submenu))
    elif len(target_lang_items) <= 1: # Only "None" is present
        target_lang_items.append(item(_('systray.menu.no_other_languages', default="No other languages available"), None, enabled=False))

    return menu(*target_lang_items)


def build_modules_menu():
    """Builds the Modules enable/disable submenu."""
    module_cfg = config.get("modules", {}) # Read from local config copy
    module_items_map = {
        "tooltip_enabled": _("systray.modules.tooltip", default="Afficher l'info-bulle"),
        "status_indicator_enabled": _("systray.modules.status_indicator", default="Afficher l'indicateur"),
        "action_confirm_enabled": _("systray.modules.action_confirm", default="Confirmer les actions"),
        "translation_enabled": _("systray.modules.translation", default="Activer la traduction"),
        "command_interpretation_enabled": _("systray.modules.command_interpretation", default="Interpréter les commandes"),
        "audio_buffer_enabled": _("systray.modules.audio_buffer", default="Activer le tampon audio")
    }
    module_items = []
    for module_key, display_text in module_items_map.items():
        module_items.append(
            item(
                display_text,
                partial(update_module_setting_callback, module_key=module_key),
                checked=lambda item, k=module_key: config.get("modules", {}).get(k, True), # Check current config
            )
        )
    return menu(*module_items)


def build_menu():
    """Builds the main systray menu."""
    global config # Use the module-level config for building
    # Ensure config is up-to-date before building menu
    config = load_config()
    current_lang_code = config.get("general", {}).get("selected_language", "en-US")
    lang_prefix = current_lang_code.split('-')[0] if current_lang_code else 'en'

    mode_menu = build_mode_menu()
    source_lang_menu = build_language_source_menu()
    target_lang_menu = build_language_target_menu()
    modules_menu = build_modules_menu()

    # --- Build Available Commands Submenu --- >
    default_cmd_title = _("systray.commands.title", default="Voice Commands")
    command_menu_title = f"{default_cmd_title} ({_('systray.commands.error', default='Error')})"
    commands_menu = menu(item(_('systray.commands.error_loading', default="Error loading commands"), None, enabled=False))
    try:
        # Load translations using the *current* language
        load_translations(current_lang_code) # Ensure translations are fresh

        enter_kws_str = _("dictation.enter_keywords", default="")
        escape_kws_str = _("dictation.escape_keywords", default="")
        back_kws_str = _("dictation.backspace_keywords", default="")
        enter_kws = [k.strip() for k in enter_kws_str.split(',') if k.strip()]
        escape_kws = [k.strip() for k in escape_kws_str.split(',') if k.strip()]
        back_kws = [k.strip() for k in back_kws_str.split(',') if k.strip()]

        command_items = []
        enter_label = _("systray.commands.enter", default="Enter")
        escape_label = _("systray.commands.escape", default="Escape")
        backspace_label = _("systray.commands.backspace", default="Backspace")
        replacements_label = _("systray.commands.replacements", default="Replacements")
        none_label = _("systray.commands.none", default="(No commands defined)")
        title_label = default_cmd_title

        if enter_kws: command_items.append(item(f"{enter_label}: {', '.join(enter_kws)}", None, enabled=False))
        if escape_kws: command_items.append(item(f"{escape_label}: {', '.join(escape_kws)}", None, enabled=False))
        if back_kws: command_items.append(item(f"{backspace_label}: {', '.join(back_kws)}", None, enabled=False))

        replacements = ALL_DICTATION_REPLACEMENTS.get(lang_prefix, {})
        if replacements:
            if command_items: command_items.append(menu.SEPARATOR)
            command_items.append(item(replacements_label, None, enabled=False))
            count = 0
            MAX_REPLACEMENTS_SHOWN = 15
            sorted_replacements = sorted(replacements.items())
            for spoken, typed in sorted_replacements:
                command_items.append(item(f"  '{spoken}' -> '{typed}'", None, enabled=False))
                count += 1
                if count >= MAX_REPLACEMENTS_SHOWN:
                     command_items.append(item("  ...", None, enabled=False))
                     break
        if not command_items:
             command_items.append(item(none_label, None, enabled=False))

        commands_menu = menu(*command_items)
        command_menu_title = f"{title_label} ({current_lang_code or 'N/A'})"

    except Exception as e:
        logging.error(f"Systray: Error building command list: {e}")

    # --- Build Main Menu --- >
    main_menu = menu(
        item(_("systray.menu.mode"), mode_menu),
        item(_("systray.menu.source_language"), source_lang_menu),
        item(_("systray.menu.target_language"), target_lang_menu),
        item(command_menu_title, commands_menu),
        item(_("systray.menu.modules", default="Modules"), modules_menu),
        menu.SEPARATOR,
        item(_("systray.menu.reload_config"), on_reload_config_clicked),
        item(_("systray.menu.exit"), on_exit_clicked)
    )
    logging.debug("Systray: Menu rebuilt.")
    return main_menu

# --- Main Systray Function ---
def run_systray(exit_event_arg):
    """Runs the systray icon loop with config reload watching."""
    global exit_app_event, config, icon # Make icon global for callbacks
    exit_app_event = exit_event_arg
    logging.info(f"Initializing systray UI... Exit event set: {exit_app_event is not None}")

    try:
        image = create_image(64, 64, 'black', 'red')
        icon_title = _('systray.title', default="Vibe Assistant")
        icon = pystray.Icon("vibe_assistant", image, icon_title, menu=build_menu())

        logging.info("Starting systray icon detached...")
        icon.run_detached() # Use run_detached

        # --- Watcher Loop ---
        while not exit_app_event.is_set():
            # Wait for the config reload event (signalled by callbacks in this module now)
            event_set = config_reload_event.wait(timeout=1.0)

            if event_set:
                logging.info("Systray detected config reload event (likely set by self).")
                config_reload_event.clear() # Clear the event

                # Reload local config and translations for menu building
                try:
                    config = load_config() # Reload local config copy
                    new_lang = config.get("general", {}).get("selected_language")
                    load_translations(new_lang) # Reload translations

                    # Rebuild and update the menu
                    icon.menu = build_menu()
                    icon.update_menu()
                    logging.info("Systray menu rebuilt and updated.")

                    # Update icon title too
                    new_icon_title = _('systray.title', default="Vibe Assistant")
                    if icon.title != new_icon_title:
                        icon.title = new_icon_title
                        logging.info(f"Systray icon title updated to: {icon.title}")

                except Exception as e:
                    logging.error(f"Error during systray config/menu reload: {e}", exc_info=True)

        # --- Exit ---
        logging.info("Systray watcher loop exiting. Stopping icon...")
        icon.stop()
        logging.info("Systray icon stopped.")

    except Exception as e:
        logging.error(f"Error running systray: {e}", exc_info=True)
        if 'icon' in locals() and icon and icon.visible:
            try: icon.stop()
            except: pass

# --- Entry Point (if run directly, for testing) ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    test_exit_event = threading.Event()
    run_systray(test_exit_event)
    logging.info("Systray finished.") 