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
from i18n import load_translations, _

# --- Configuration Handling (Mirrors vibe_app.py logic initially) ---
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
  "general": {
    "min_duration_sec": 0.5,
    "selected_language": "en-US",
    "target_language": None, # Default: No translation
    "openai_model": "gpt-4.1-nano", # Default model
    "active_mode": "Dictation", # Added default active mode
    "recent_source_languages": [], # Added for tracking
    "recent_target_languages": []  # Added for tracking
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
                # --- Merge with defaults for missing keys/sections (similar to vibe_app.py) --- >
                for section, defaults in DEFAULT_CONFIG.items():
                    if section not in loaded_config:
                        loaded_config[section] = defaults
                        logging.debug(f"Systray added missing section: {section}")
                    elif isinstance(defaults, dict):
                        for key, default_value in defaults.items():
                            if key not in loaded_config[section]:
                                loaded_config[section][key] = default_value
                                logging.debug(f"Systray added missing key: {section}.{key}")
                # Ensure recent lists exist even if loading an old config
                if "recent_source_languages" not in loaded_config.get("general", {}):
                    loaded_config.setdefault("general", {})["recent_source_languages"] = []
                    logging.debug("Systray added missing recent_source_languages")
                if "recent_target_languages" not in loaded_config.get("general", {}):
                     loaded_config.setdefault("general", {})["recent_target_languages"] = []
                     logging.debug("Systray added missing recent_target_languages")

                return loaded_config
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Error reading/decoding {CONFIG_FILE}: {e}. Using default config.")
            return DEFAULT_CONFIG
        except Exception as e:
             logging.error(f"Unexpected error loading config for systray: {e}. Using default config.")
             return DEFAULT_CONFIG

# --- Global State for UI ---
config = load_config()
# --- Load Initial Translations --- >
load_translations(config.get("general", {}).get("selected_language"))
logging.info(f"Systray initial translations loaded for language: {i18n.get_current_language()}")
# --- End Load Initial Translations --- >
config_reload_event = threading.Event() # Used to signal main app to reload
exit_app_event = None # Placeholder for the event from main app

# --- Define Language Lists ---

# Preferred languages for SOURCE menu
# --- REMOVED PREFERRED LISTS - Will be dynamic now ---
# PREFERRED_SOURCE_LANGUAGES = {
#     "en-US": "English (US)",
#     "fr-FR": "French",
#     # Add other frequently used SOURCE languages here
# }

# Preferred languages for TARGET menu (can include None)
# PREFERRED_TARGET_LANGUAGES = {
#     None: "Aucune (Dictée seulement)", # Keep None easily accessible
#     "en-US": "English (US)",
#     "fr-FR": "French",
#     "ko-KR": "Korean",
#     "ja-JP": "Japanese",
#     # Add other frequently used TARGET languages here
# }

# Comprehensive list (ensure codes are valid for Deepgram/OpenAI)
# Add more as needed, cross-reference with Deepgram/OpenAI documentation
ALL_LANGUAGES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "fr-FR": "French",
    "es-ES": "Spanish",
    "de-DE": "German",
    "it-IT": "Italian",
    "pt-PT": "Portuguese",
    "pt-BR": "Portuguese (Brazil)",
    "ru-RU": "Russian",
    "zh": "Chinese (Mandarin)", # Or zh-CN? Check docs
    "ko-KR": "Korean",
    "ja-JP": "Japanese",
    "hi-IN": "Hindi",
    "ar": "Arabic", # Or specific dialect codes?
    "nl-NL": "Dutch",
    # Add many more here...
}

# --- NEW: Native Language Names Dictionary ---
# Store the name of the language *in that language*.
# Use ALL_LANGUAGES as a basis, add native names where known.
# Fallback will be the English name from ALL_LANGUAGES if native is missing.
NATIVE_LANGUAGE_NAMES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "fr-FR": "Français",
    "es-ES": "Español",
    "de-DE": "Deutsch",
    "it-IT": "Italiano",
    "pt-PT": "Português",
    "pt-BR": "Português (Brasil)",
    "ru-RU": "Русский",
    "zh": "中文 (普通话)", # Simplified Chinese (Mandarin)
    "ko-KR": "한국어",
    "ja-JP": "日本語",
    "hi-IN": "हिन्दी",
    "ar": "العربية",
    "nl-NL": "Nederlands",
    # Add more as needed
}


# Calculate 'Other' SOURCE languages dynamically
# Sort alphabetically by language name for the 'Other' menu
# --- REMOVED OTHER LISTS - Will be calculated in build_menu ---
# OTHER_SOURCE_LANGUAGES = {
#     k: v for k, v in sorted(ALL_LANGUAGES.items(), key=lambda item: item[1])
#     if k not in PREFERRED_SOURCE_LANGUAGES
# }

# Calculate 'Other' TARGET languages dynamically (excluding None and preferred ones)
# Sort alphabetically by language name for the 'Other' menu
# OTHER_TARGET_LANGUAGES = {
#     k: v for k, v in sorted(ALL_LANGUAGES.items(), key=lambda item: item[1])
#     if k not in PREFERRED_TARGET_LANGUAGES # Exclude preferred targets (None is handled separately)
# }


# Define valid options for triggers (no change)
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

    # --- Update Recent List when language is changed --- >
    MAX_RECENT_LANGS = 10 # Max languages to store in recent list
    if setting_key == 'selected_language':
        recent_list_key = "recent_source_languages"
        recent_list = config['general'].get(recent_list_key, [])
        if value in recent_list: recent_list.remove(value)
        recent_list.insert(0, value)
        config['general'][recent_list_key] = recent_list[:MAX_RECENT_LANGS]
        logging.debug(f"Systray updated recent source: {config['general'][recent_list_key]}")
    elif setting_key == 'target_language':
        recent_list_key = "recent_target_languages"
        recent_list = config['general'].get(recent_list_key, [])
        if value in recent_list: recent_list.remove(value)
        recent_list.insert(0, value)
        config['general'][recent_list_key] = recent_list[:MAX_RECENT_LANGS]
        logging.debug(f"Systray updated recent target: {config['general'][recent_list_key]}")
    # --- End Update Recent List ---

    # --- Reload translations if source language changed BEFORE saving --- >
    if setting_key == 'selected_language':
        logging.info(f"Systray source language changed to {value}. Reloading translations.")
        old_lang = i18n.get_current_language()
        logging.debug(f"Language BEFORE load_translations: {old_lang}")
        load_translations(value)
        new_lang = i18n.get_current_language()
        logging.debug(f"Language AFTER load_translations: {new_lang} (requested {value})")
        # Log a sample translation retrieval
        sample_key = 'systray.menu.exit'
        sample_translation = _(sample_key)
        logging.debug(f"Sample translation for '{sample_key}' AFTER load in callback ({new_lang}): '{sample_translation}'")

    save_config()
    # Need to rebuild menu here because the recent items have changed
    # We cannot rely only on checked state as the items themselves change
    icon.menu = build_menu()
    icon.update_menu()

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
    # --- Reload translations in systray --- >
    load_translations(config.get("general", {}).get("selected_language"))
    logging.info(f"Systray reloaded translations for language: {i18n.get_current_language()}")
    # --- End Reload --- >
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

# --- Callback for Mode Selection ---
def update_mode_setting_callback(icon, item, value):
    """Callback to update the active mode in config."""
    logging.debug(f"Updating active mode: {value}")
    if "general" not in config:
        config["general"] = {}
    config["general"]["active_mode"] = value
    save_config() # Save the updated config
    # No need to rebuild menu here, checked state handles visual update

# --- Functions to build the menu dynamically ---
def build_mode_menu():
    """Builds the Mode selection submenu."""
    general_cfg = config.get("general", {})
    current_mode = general_cfg.get("active_mode", "Dictation") # Default to Dictation if missing

    # --- Get AVAILABLE_MODES from vibe_app constants if possible, or define locally ---
    # For now, let's define it locally, mirroring vibe_app.py and status_indicator.py
    # Consider a shared constants file later.
    AVAILABLE_MODES = {
        "Dictation": "Dictation Mode",
        "Command": "Command Mode", # Renamed from Keyboard
        # Add "Command" later if desired
    }

    mode_items = []
    for mode_name, display_name in AVAILABLE_MODES.items():
        # --- Use translation key for display name --- >
        translated_name = _(f"mode_names.{mode_name}", default=display_name)
        mode_items.append(
            item(
                translated_name,
                partial(update_mode_setting_callback, value=mode_name),
                checked=lambda item, m=mode_name: current_mode == m,
                radio=True
            )
        )
    return menu(*mode_items)

def build_language_source_menu():
    """Builds the Langue Source submenu (extracted logic)."""
    MAX_RECENT_DISPLAY = 3 # How many recent languages to show directly
    general_cfg = config.get("general", {})
    current_source_lang = general_cfg.get("selected_language") # Get current source language
    recent_source_codes = [code for code in general_cfg.get("recent_source_languages", []) if code != current_source_lang][:MAX_RECENT_DISPLAY] # Filter out current

    # --- Helper to create item with check --- >
    def create_lang_item(lang_type, code):
        setting_key = 'selected_language' # Hardcoded for source
        # --- MODIFIED: Use NATIVE name for source language --- >
        # Fallback chain: Native Name -> English Name (from ALL_LANGUAGES) -> Code itself
        english_name = ALL_LANGUAGES.get(code, code) # Get English name or code as fallback
        native_name = NATIVE_LANGUAGE_NAMES.get(code, english_name) # Get native name, fallback to English/code
        # --- Use native_name directly, no translation ---
        # --- END MODIFIED --- >
        return item(
            native_name, # Use the (potentially native) name
            partial(update_general_setting_callback, setting_key=setting_key, value=code),
            checked=lambda item, c=code: general_cfg.get(setting_key) == c,
            radio=True
        )

    source_lang_items = []
    # Add recent SOURCE languages first (already filtered)
    for code in recent_source_codes:
        source_lang_items.append(create_lang_item('source', code))

    # Define 'Other' source languages (filter out current source lang)
    # Sort based on English name for consistency in the "Other" list
    other_source_langs = {
        k: v for k, v in sorted(ALL_LANGUAGES.items(), key=lambda item: item[1])
        if k not in recent_source_codes and k != current_source_lang # Exclude recent AND current
    }

    # Build 'Other' source submenu
    if other_source_langs:
        other_source_submenu_items = []
        for code, english_name in other_source_langs.items(): # Iterate using English names for sorting
            # --- MODIFIED: Use NATIVE name for display --- >
            native_name = NATIVE_LANGUAGE_NAMES.get(code, english_name) # Get native name, fallback to English/code
            # --- Use native_name directly, no translation ---
            other_source_submenu_items.append(
                item(
                    native_name, # Use the (potentially native) name
                    partial(update_general_setting_callback, setting_key='selected_language', value=code),
                    checked=lambda item, c=code: general_cfg.get("selected_language") == c, # Check against the actual current lang
                    radio=True
                )
            )
            # --- END MODIFIED --- >
        other_source_submenu = menu(*other_source_submenu_items)
        if recent_source_codes: # Add separator only if recent items exist
             source_lang_items.append(menu.SEPARATOR)
        # --- Translate "Other languages" --- >
        source_lang_items.append(item(_('systray.menu.other_languages', default='Autres langues'), other_source_submenu))
    elif not recent_source_codes:
        # Fallback if only the current language was available (and thus filtered out)
        # --- Translate "No other languages" --- >
        source_lang_items.append(item(_('systray.menu.no_other_languages', default="No other languages available"), None, enabled=False))

    return menu(*source_lang_items)

def build_language_target_menu():
    """Builds the Langue Cible submenu (extracted logic)."""
    MAX_RECENT_DISPLAY = 3 # How many recent languages to show directly
    MAX_RECENT_TARGET_DISPLAY = 7
    general_cfg = config.get("general", {})
    recent_target_codes = general_cfg.get("recent_target_languages", [])[:MAX_RECENT_TARGET_DISPLAY]

    # --- Helper to create item with check --- >
    def create_lang_item(lang_type, code):
        setting_key = 'target_language' # Hardcoded for target
        # --- Translate language name --- >
        default_name = ALL_LANGUAGES.get(code, f"Unknown ({code})") # Keep original lookup as fallback
        name = _(f"language_names.{code}", default=default_name)
        return item(
            name,
            partial(update_general_setting_callback, setting_key=setting_key, value=code),
            checked=lambda item, c=code: general_cfg.get(setting_key) == c,
            radio=True
        )

    target_lang_items = []
    # Always add "None" first
    target_lang_items.append(
        item(
            # --- Translate "None" option --- >
            _(f"language_names.none", default="Aucune (Dictée seulement)"),
            partial(update_general_setting_callback, setting_key='target_language', value=None),
            checked=lambda item: general_cfg.get("target_language") is None,
            radio=True
        )
    )

    # Add recent TARGET languages (excluding None if it was somehow added to recent list)
    for code in recent_target_codes:
        # Don't filter current target, you might want to re-select it from recent
        if code is not None:
             target_lang_items.append(create_lang_item('target', code))

    # Define 'Other' target languages
    other_target_langs = {
        k: v for k, v in sorted(ALL_LANGUAGES.items(), key=lambda item: item[1])
        if k not in recent_target_codes and k is not None # Exclude recent codes and also None from "Other"
        # No need to filter current target here either
    }

    # Build 'Other' target submenu
    if other_target_langs:
        other_target_submenu = menu(*[
            # --- Translate language name --- >
            item(
                _(f"language_names.{code}", default=name),
                partial(update_general_setting_callback, setting_key='target_language', value=code),
                checked=lambda item, c=code: general_cfg.get("target_language") == c,
                radio=True
            ) for code, name in other_target_langs.items()
        ])
        # Add separator if "None" or recent items exist before "Other"
        if target_lang_items: # Checks if the list is not empty (will have at least "None")
             target_lang_items.append(menu.SEPARATOR)
        # --- Translate "Other languages" --- >
        target_lang_items.append(item(_('systray.menu.other_languages', default='Autres langues'), other_target_submenu))
    elif len(target_lang_items) <= 1: # Only "None" is present
        # --- Translate "No other languages" --- >
        target_lang_items.append(item(_('systray.menu.no_other_languages', default="No other languages available"), None, enabled=False))

    return menu(*target_lang_items)

# Renamed from build_general_menu -> builds the content of "Personnalisation"
def build_personalisation_submenu_content():
    general_cfg = config.get("general", {})

    # --- Min Duration (Display only, translated) --- >
    min_dur = general_cfg.get("min_duration_sec", "N/A")
    min_dur_item = item(_('systray.menu.min_duration', default=f'Min Duration (s): {min_dur}', value=min_dur), None, enabled=False)

    # --- OpenAI Model (Display only, translated) --- >
    openai_model = general_cfg.get("openai_model", "N/A")
    model_item = item(_('systray.menu.translation_model', default=f'Translation Model: {openai_model}', value=openai_model), None, enabled=False)

    # --- Return list including submenus for triggers and tooltip ---
    return [
        item(_('systray.menu.triggers', default='Déclencheurs'), menu(*build_triggers_menu())), # Translate submenu title
        item(_('systray.menu.tooltip', default='Info-bulle'), menu(*build_tooltip_menu())), # Translate submenu title
        menu.SEPARATOR,
        min_dur_item,
        model_item
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
        item(_('systray.menu.dictation_button', default='Dictation Button'), dictation_submenu),
        item(_('systray.menu.command_button', default='Command Button'), command_submenu),
        item(_('systray.menu.command_modifier', default='Command Modifier'), modifier_submenu)
    ]

def build_tooltip_menu():
    alpha = config.get("tooltip", {}).get("alpha", "N/A")
    bg = config.get("tooltip", {}).get("bg_color", "N/A")
    fg = config.get("tooltip", {}).get("fg_color", "N/A")
    font = config.get("tooltip", {}).get("font_family", "N/A")
    size = config.get("tooltip", {}).get("font_size", "N/A")

    # TODO: Add actions to change these settings
    return [
        item(_('systray.menu.tooltip_transparency', default=f'Transparency: {alpha}', value=alpha), None, enabled=False),
        item(_('systray.menu.tooltip_background', default=f'Background: {bg}', value=bg), None, enabled=False),
        item(_('systray.menu.tooltip_text_color', default=f'Text Color: {fg}', value=fg), None, enabled=False),
        item(_('systray.menu.tooltip_font', default=f'Font: {font}', value=font), None, enabled=False),
        item(_('systray.menu.tooltip_font_size', default=f'Font Size: {size}', value=size), None, enabled=False)
    ]

def build_menu():
    """Builds the main systray menu structure, showing current source language."""
    current_build_lang = i18n.get_current_language()
    logging.debug(f"Starting build_menu. Current i18n lang: {current_build_lang}")

    # --- Get Current Source Language Name for Display ---
    general_cfg = config.get("general", {})
    current_source_lang_code = general_cfg.get("selected_language", "N/A")
    # Use native name logic
    english_name = ALL_LANGUAGES.get(current_source_lang_code, current_source_lang_code)
    native_name = NATIVE_LANGUAGE_NAMES.get(current_source_lang_code, english_name)
    current_source_lang_display_name = native_name if current_source_lang_code != "N/A" else "N/A"

    # --- Combine base label with current language ---
    source_lang_label_base = _('systray.menu.source_language', default='Langue Source')
    source_lang_menu_text = f"{source_lang_label_base}: {current_source_lang_display_name}"

    return menu(
        item(_('systray.menu.mode', default='Mode'), build_mode_menu()),
        # --- Use the combined text for the menu item --- >
        item(source_lang_menu_text, build_language_source_menu()),
        item(_('systray.menu.target_language', default='Langue Cible'), build_language_target_menu()),
        menu.SEPARATOR,
        item(_('systray.menu.customization', default='Personnalisation'), menu(*build_personalisation_submenu_content())),
        menu.SEPARATOR,
        item(_('systray.menu.reload_config', default='Recharger Config'), on_reload_config_clicked),
        item(_('systray.menu.exit', default='Quitter'), on_exit_clicked)
    )

# --- Main Systray Function ---
def run_systray(exit_event_arg):
    """Runs the systray icon loop with config reload watching."""
    global exit_app_event, config, icon # Add config and icon to globals used here
    exit_app_event = exit_event_arg
    logging.info(f"Initializing systray UI... Exit event set: {exit_app_event is not None}")

    try:
        image = create_image(64, 64, 'black', 'red')
        icon_title = _('systray.title', default="Vibe Assistant")
        icon = pystray.Icon("vibe_assistant", image, icon_title, menu=build_menu())

        logging.info("Starting systray icon detached...")
        icon.run_detached() # Use run_detached instead of run

        # --- Watcher Loop ---
        while not exit_app_event.is_set():
            # Wait for the config reload event, with a timeout to allow checking exit event
            event_set = config_reload_event.wait(timeout=1.0) # Wait for 1 second

            if event_set:
                logging.info("Systray detected config reload event.")
                config_reload_event.clear() # Clear the event

                # Reload config and translations within systray thread
                try:
                    config = load_config() # Reload config to get new language
                    new_lang = config.get("general", {}).get("selected_language")
                    logging.info(f"Systray reloading translations for: {new_lang}")
                    load_translations(new_lang)

                    # Rebuild and update the menu
                    icon.menu = build_menu()
                    icon.update_menu()
                    logging.info("Systray menu rebuilt and updated.")

                    # Update icon title too, in case it uses translations
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
        # Ensure icon is stopped even on error
        if 'icon' in locals() and icon and icon.visible:
            try: icon.stop()
            except: pass # Ignore errors stopping potentially broken icon

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