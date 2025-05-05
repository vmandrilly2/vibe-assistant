import pystray
from pystray import MenuItem as item, Menu as menu
from PIL import Image, ImageDraw
import threading
import logging
import json
import os
import sys
from functools import partial # Import partial for cleaner callbacks

# --- NEW: Import ConfigManager ---
from config_manager import ConfigManager

# --- i18n Import with Fallback --- >
try:
    import i18n
    from i18n import load_translations, _, ALL_DICTATION_REPLACEMENTS, get_current_language
    i18n_systray_enabled = True
except ImportError:
    logging.error("Systray: Module i18n not found. Translations will be disabled for systray.")
    # Define dummy functions/variables
    _ = lambda key, default=None, **kwargs: default if default is not None else key
    load_translations = lambda lang_code: None
    get_current_language = lambda: "en" # Default
    ALL_DICTATION_REPLACEMENTS = {}
    i18n_systray_enabled = False
# --- End i18n Import --- >

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


# --- Global State for UI ---
config_reload_event = threading.Event() # Used to signal main app to reload
exit_app_event = None # Placeholder for the event from main app
# Define the translation function globally (will be assigned in run_systray)
_translate = lambda key, default=None, **kwargs: default if default is not None else key

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

# --- Modified Callbacks (Now accept config_manager) ---

def update_general_setting_callback(icon, item, config_manager: ConfigManager, translate_func, setting_key, value):
    """Callback wrapper - Updates config via manager and signals reload."""
    logging.debug(f"Systray callback: Update general setting: {setting_key} = {value}")

    # --- Use ConfigManager --- >
    config_manager.update(f"general.{setting_key}", value)

    # Handle language updates separately to manage recent lists
    if setting_key in ['selected_language', 'target_language']:
        lang_type = 'source' if setting_key == 'selected_language' else 'target'
        recent_list_key = "general.recent_source_languages" if lang_type == "source" else "general.recent_target_languages"
        MAX_RECENT_LANGS = 10
        recent_list = config_manager.get(recent_list_key, [])
        if value in recent_list: recent_list.remove(value)
        recent_list.insert(0, value)
        config_manager.update(recent_list_key, recent_list[:MAX_RECENT_LANGS])
        # Reload translations if source language changed
        if setting_key == 'selected_language':
            logging.info(f"Systray source language changed to {value}. Reloading translations.")
            load_translations(value)

    config_manager.save() # Save changes
    config_reload_event.set() # Signal main app to reload its ConfigManager
    # Rebuild menu to reflect updated recent lists and potentially new translations
    # Pass config_manager AND translate_func to build_menu
    icon.menu = build_menu(config_manager, translate_func)
    icon.update_menu()

# REMOVED: update_trigger_setting_callback - Triggers are not currently in menu
# REMOVED: update_mode_setting_callback - Handled by general setting callback

# --- NEW: Callback for Module Toggling ---
def _toggle_module_callback(icon, item, config_manager: ConfigManager, translate_func, module_key):
    """Callback to toggle a module's activation state using ConfigManager."""
    full_key = f"modules.{module_key}"
    current_value = config_manager.get(full_key, True) # Default to true if missing? Or use default config?
    new_value = not current_value
    logging.debug(f"Systray callback: Toggling module setting: {full_key} = {new_value}")
    config_manager.update(full_key, new_value)
    config_manager.save()
    # Signal reload - main app will check enabled status
    config_reload_event.set()
    # Check state updates the visual checkbox, no rebuild needed IF build_menu reads from config_manager
    # We might still need to rebuild if the presence of other menus depends on a module
    # Rebuilding is safer for now.
    icon.menu = build_menu(config_manager, translate_func)
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


# --- Menu Building Functions (Now accept config_manager and translate_func) ---

def build_mode_menu(config_manager: ConfigManager, translate_func):
    """Builds the mode selection submenu."""
    # Use translate_func for translation
    menu_title = translate_func("systray.menu.mode", default="Mode")
    current_mode = config_manager.get("general.active_mode", MODE_DICTATION)
    mode_items = []
    for mode_code, mode_name in AVAILABLE_MODES.items():
        # Translate mode name
        translated_name = translate_func(f"mode_names.{mode_code}", default=mode_name)
        mode_items.append(
            item(
                translated_name,
                partial(update_general_setting_callback, setting_key="active_mode", value=mode_code, config_manager=config_manager, translate_func=translate_func), # Pass translate_func
                checked=lambda item, mode=mode_code: config_manager.get("general.active_mode") == mode, # Check against manager
                radio=True
            )
        )
    return item(menu_title, menu(*mode_items))

def build_language_source_menu(config_manager: ConfigManager, translate_func):
    """Builds the source language selection submenu."""
    # Use translate_func for translation
    menu_title = translate_func("systray.menu.source_language", default="Source Language")
    current_lang = config_manager.get("general.selected_language", "en-US")
    recent_langs = config_manager.get("general.recent_source_languages", [])[:5] # Limit displayed recent

    def create_lang_item(code):
        # Use NATIVE name for source display
        english_name = ALL_LANGUAGES.get(code, code)
        display_name = NATIVE_LANGUAGE_NAMES.get(code, english_name)
        return item(
            display_name,
            partial(update_general_setting_callback, setting_key="selected_language", value=code, config_manager=config_manager, translate_func=translate_func), # Pass translate_func
            checked=lambda item, lang_code=code: config_manager.get("general.selected_language") == lang_code, # Check against manager
            radio=True
        )

    lang_items = []
    added_codes = set() # Keep track of codes added to the menu

    # Add current language first if not in recent
    if current_lang not in recent_langs and current_lang in ALL_LANGUAGES:
        lang_items.append(create_lang_item(current_lang))
        added_codes.add(current_lang)

    # Add recent languages
    for lang_code in recent_langs:
        # Add if valid and not already added
        if lang_code in ALL_LANGUAGES and lang_code not in added_codes:
            lang_items.append(create_lang_item(lang_code))
            added_codes.add(lang_code)

    # Add separator if needed
    if lang_items: lang_items.append(menu.SEPARATOR)

    # Add remaining languages alphabetically
    sorted_langs = sorted(ALL_LANGUAGES.items(), key=lambda x: x[1]) # Sort by English name for consistency
    for code, _ in sorted_langs:
        # Add if not already in the menu
        if code not in added_codes:
            lang_items.append(create_lang_item(code))
            added_codes.add(code) # Mark as added

    return item(menu_title, menu(*lang_items))


def build_language_target_menu(config_manager: ConfigManager, translate_func):
    """Builds the target language selection submenu."""
    # Use translate_func for translation
    menu_title = translate_func("systray.menu.target_language", default="Target Language (Translate To)")
    current_target_lang = config_manager.get("general.target_language") # Can be None
    recent_targets = config_manager.get("general.recent_target_languages", [])[:7] # Limit displayed recent

    def create_lang_item(code): # Code can be None
        # Use translated name for target display
        default_name = ALL_LANGUAGES_TARGET.get(code, code)
        display_name = translate_func(f"language_names.{code if code is not None else 'none'}", default=default_name)
        return item(
            display_name,
            partial(update_general_setting_callback, setting_key="target_language", value=code, config_manager=config_manager, translate_func=translate_func), # Pass translate_func
            checked=lambda item, lang_code=code: config_manager.get("general.target_language") == lang_code, # Check against manager
            radio=True
        )

    lang_items = []
    added_codes = set() # Keep track of codes added to the menu

    # Add "None" option first
    lang_items.append(create_lang_item(None))
    added_codes.add(None)

    # Add current target language next if set and not None and not in recent
    if current_target_lang is not None and current_target_lang not in recent_targets and current_target_lang in ALL_LANGUAGES_TARGET:
        lang_items.append(create_lang_item(current_target_lang))
        added_codes.add(current_target_lang)

    # Add recent target languages (excluding None if present)
    for lang_code in recent_targets:
        # Add if valid and not already added
        if lang_code is not None and lang_code in ALL_LANGUAGES_TARGET and lang_code not in added_codes:
            lang_items.append(create_lang_item(lang_code))
            added_codes.add(lang_code)


    # Add separator if needed
    if len(lang_items) > 1: lang_items.append(menu.SEPARATOR)

    # Add remaining languages alphabetically (excluding None)
    # Sort by English name for consistency
    sorted_langs = sorted([(k, v) for k, v in ALL_LANGUAGES_TARGET.items() if k is not None], key=lambda x: x[1])
    for code, _ in sorted_langs:
        # Add if not already in the menu
        if code not in added_codes:
            lang_items.append(create_lang_item(code))
            added_codes.add(code) # Mark as added

    return item(menu_title, menu(*lang_items))

def build_modules_menu(config_manager: ConfigManager, translate_func):
    """Builds the module enable/disable submenu using ConfigManager."""
    # Use translate_func for translation
    menu_title = translate_func("systray.menu.modules", default="Modules")
    module_items = []
    modules_config = config_manager.get("modules", {})

    # Sort modules by key for consistent order
    sorted_module_keys = sorted(modules_config.keys())

    for module_key in sorted_module_keys:
        # Example: module_key = "tooltip_enabled" -> display_name = "Tooltip"
        # Attempt to get a friendlier name from translations or derive it
        base_module_name = module_key.replace("_enabled", "")
        display_key = f"module_names.{base_module_name}"
        # Simple fallback: Capitalize the base name
        default_display_name = base_module_name.replace("_", " ").capitalize()
        display_name = translate_func(display_key, default=default_display_name)

        module_items.append(
            item(
                display_name,
                partial(_toggle_module_callback, module_key=module_key, config_manager=config_manager, translate_func=translate_func), # Pass translate_func
                checked=lambda item, key=module_key: config_manager.get(f"modules.{key}", True), # Check against manager
                # radio=False by default, acts as checkbox
            )
        )
    # Add info note about restart? Or handle dynamically?
    # module_items.append(menu.SEPARATOR)
    # module_items.append(item(translate_func("systray.note.restart_required", default="(Restart may be needed)"), None, enabled=False))

    return item(menu_title, menu(*module_items))


def build_menu(config_manager: ConfigManager, translate_func):
    """Builds the main systray menu using ConfigManager."""
    # Use translate_func for translations
    menu_items = [
        build_mode_menu(config_manager, translate_func),
        menu.SEPARATOR,
        build_language_source_menu(config_manager, translate_func),
        build_language_target_menu(config_manager, translate_func),
        menu.SEPARATOR,
        build_modules_menu(config_manager, translate_func), # Add modules submenu
        menu.SEPARATOR,
        item(
            translate_func("systray.menu.exit", default="Exit"),
            on_exit_clicked
        )
        # REMOVED: Reload Config item
    ]
    logging.debug("Systray: Menu rebuilt.")
    return menu(*menu_items)


# --- Modified Main Function ---

def run_systray(exit_event_arg, config_manager_arg: ConfigManager):
    """Runs the system tray icon application loop."""
    # Define local translation function based on import success
    local_translate_func = None
    try:
        # Attempt to import the real i18n components
        from i18n import load_translations as i18n_load_translations
        from i18n import _ as i18n_translate_func
        local_translate_func = i18n_translate_func
        logging.info("Systray: Successfully imported i18n components.")
    except ImportError:
        logging.error("Systray: Module i18n not found. Translations will be disabled for systray.")
        # Assign dummy lambda function
        local_translate_func = lambda key, default=None, **kwargs: default if default is not None else key
        i18n_load_translations = lambda lang_code: None # Dummy load function

    global exit_app_event, config_manager # Make config_manager accessible to callbacks via partials
    exit_app_event = exit_event_arg
    config_manager = config_manager_arg # Store the passed manager

    logging.info("Initializing systray UI...")
    if not exit_app_event:
        logging.error("Systray exit event not provided! Cannot guarantee clean shutdown.")
    else:
        logging.debug(f"Systray UI exit event reference set: {exit_app_event}")

    # --- Load Initial Translations using ConfigManager and Correct Loader ---
    initial_language = config_manager.get("general.selected_language", "en-US")
    i18n_load_translations(initial_language) # Use the potentially dummy loader
    logging.info(f"Systray initial translations loaded (using {'real' if local_translate_func == i18n_translate_func else 'dummy'} loader).")
    # --- End Load Initial Translations ---

    # Create placeholder icon
    icon_image = create_image(64, 64, 'black', 'white')

    # Build initial menu using ConfigManager and the determined translate func
    initial_menu = build_menu(config_manager, local_translate_func)

    # Create the pystray icon object
    icon = pystray.Icon("vibe_assistant", icon_image, "Vibe Assistant", initial_menu)

    # --- Define how to update the icon when config reloads ---
    def setup(icon_obj):
        logging.debug("Systray icon setup running.")
        icon_obj.visible = True
        # Watch the config_reload_event
        def watch_reload():
            nonlocal local_translate_func # Ensure we use the correct func in the watcher too
            logging.debug("Systray reload watcher thread started.") # Added start log
            while not exit_app_event.is_set():
                # Check reload event with a very short timeout
                reload_triggered = config_reload_event.wait(timeout=1)
                if exit_app_event.is_set(): # Check exit event again after wait
                    break

                if reload_triggered:
                    logging.info("Systray detected config reload event. Rebuilding menu.")
                    try:
                        # Reload translations in case language changed
                        new_lang = config_manager.get("general.selected_language")
                        # Determine which load_translations to use (might have changed if i18n was fixed/broken)
                        try:
                            from i18n import load_translations as current_load_translations
                            from i18n import _ as current_translate_func
                            local_translate_func = current_translate_func # Update local func ref
                        except ImportError:
                             current_load_translations = lambda lang_code: None
                             local_translate_func = lambda key, default=None, **kwargs: default if default is not None else key
                        current_load_translations(new_lang)
                        # Rebuild and update menu, passing the potentially updated translate func
                        icon_obj.menu = build_menu(config_manager, local_translate_func)
                        icon_obj.update_menu()
                        logging.info("Systray menu updated after config reload.")
                        config_reload_event.clear() # Clear the event
                    except Exception as e:
                         logging.error(f"Systray error rebuilding menu after reload: {e}", exc_info=True)
            logging.info("Systray reload watcher thread exiting.")

        reload_thread = threading.Thread(target=watch_reload, daemon=True)
        reload_thread.start()

    # --- Start the icon ---
    logging.info("Starting systray icon detached...")
    try:
        # Run the icon loop in a separate thread if needed, or use icon.run() directly
        # If run_systray is already in its own thread, icon.run() is fine.
        icon.run(setup=setup) # Pass the setup function
    except Exception as e:
        logging.error(f"Error running systray icon: {e}", exc_info=True)
    finally:
        logging.info("Systray icon run loop finished.")
        icon.stop() # Ensure icon resources are cleaned up
        if not exit_app_event.is_set():
             exit_app_event.set() # Ensure main loop exits if systray crashes


# Example if running systray standalone (for testing)
if __name__ == '__main__':
    print("Running systray_ui directly for testing...")
    logging.basicConfig(level=logging.DEBUG)
    test_exit_event = threading.Event()
    # Create a dummy ConfigManager for testing
    test_config_manager = ConfigManager()
    # Run in a thread so we can stop it
    systray_test_thread = threading.Thread(target=run_systray, args=(test_exit_event, test_config_manager), daemon=True)
    systray_test_thread.start()
    try:
        while systray_test_thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopping systray test...")
        test_exit_event.set()
        systray_test_thread.join(timeout=2.0)
    print("Systray test finished.") 