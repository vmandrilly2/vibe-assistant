# i18n.py
import json # Import json module
import os
import logging
from typing import Dict, Set, Any # Removed Tuple as it's not used after gettext removal

logger = logging.getLogger(__name__)

LOCALE_DIR = os.path.join(os.path.dirname(__file__), 'locales')

# Update SUPPORTED_LANGUAGES to find .json files and extract lang codes
def _get_supported_languages():
    if not os.path.exists(LOCALE_DIR):
        return ['en'] # Default if locales directory doesn't exist
    supported = []
    for f_name in os.listdir(LOCALE_DIR):
        if f_name.endswith(".json"):
            lang_code = f_name[:-5] # Remove .json extension
            supported.append(lang_code)
    return supported if supported else ['en']

SUPPORTED_LANGUAGES = _get_supported_languages()
CURRENT_LANGUAGE = 'en' # Default language
# Will store loaded JSON data: {key: translation}
current_translations: Dict[str, str] = {}

# --- Keyword/Action Definitions --- 
# Structure: {language_code: {action_name: {set of keywords}, ...}, ...}
ACTION_KEYWORDS: Dict[str, Dict[str, Set[str]]] = {
    'en': {
        'enter': {'enter', 'return', 'line break'},
        'backspace': {'backspace', 'delete', 'erase'},
        'escape': {'escape'},
        # Add more English actions/keywords
    },
    'fr': {
        'enter': {'entrer', 'entrée', 'retour à la ligne'},
        'backspace': {'effacer', 'efface', 'retour arrière'},
        'escape': {'échappe', 'échap'},
        # Add more French actions/keywords
    },
    # Add other languages
}

# --- Replacement Definitions --- 
# Structure: {language_code: {word_to_replace: replacement, ...}, ...}
REPLACEMENTS: Dict[str, Dict[str, str]] = {
    'en': {
        'period': '.', 'full stop': '.',
        'comma': ',',
        'question mark': '?',
        'exclamation mark': '!', 'exclamation point': '!',
        'new line': '\\n', 'newline': '\\n'
    },
    'fr': {
        'point': '.',
        'virgule': ',',
        'point d\'interrogation': '?',
        'point d\'exclamation': '!',
        'nouvelle ligne': '\\n',
        'à la ligne': '\\n'
    }
    # Add other languages as needed, following the same pattern
}

def set_language(language_code: str):
    """Sets the current language by loading translations from the corresponding JSON file."""
    global CURRENT_LANGUAGE, current_translations
    
    if language_code not in SUPPORTED_LANGUAGES:
        logger.warning(f"Language '{language_code}' not supported. Locales dir: {LOCALE_DIR}, Supported: {SUPPORTED_LANGUAGES}. Falling back to English ('en').")
        language_code = 'en'

    CURRENT_LANGUAGE = language_code
    json_path = os.path.join(LOCALE_DIR, f"{language_code}.json")

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            current_translations = json.load(f)
        logger.info(f"Successfully loaded translations for language: {language_code} from {json_path}")
    except FileNotFoundError:
        logger.warning(f"Locale JSON file not found: {json_path}. Using empty translations for '{language_code}'.")
        current_translations = {} # Fallback to empty dict
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {json_path} for language '{language_code}': {e}", exc_info=True)
        current_translations = {} # Fallback to empty dict
    except Exception as e:
        logger.error(f"Unexpected error loading translations for language '{language_code}' from {json_path}: {e}", exc_info=True)
        current_translations = {} # Fallback to empty dict

def _(text: str, default_text: str = None) -> str:
    """Translates the given text using the current language settings.
    If default_text is provided and the key 'text' is not found, default_text will be returned.
    Otherwise, the original 'text' is returned if not found.
    """
    # Ensure translations are loaded if set_language hasn't been called explicitly yet
    # (e.g. if a module calls _() before GVM sets the initial language)
    if not current_translations and CURRENT_LANGUAGE:
        # This might happen if CURRENT_LANGUAGE is default 'en' but set_language('en') was never called
        # or if a previous call to set_language failed to load anything.
        logger.debug(f"'_' called with no translations loaded for CURRENT_LANGUAGE='{CURRENT_LANGUAGE}'. Attempting to load.")
        set_language(CURRENT_LANGUAGE)

    translated = current_translations.get(text)
    if translated is not None:
        return translated
    
    # Key not found, return default_text if provided, else original text
    return default_text if default_text is not None else text

# get_translator is no longer needed as _() directly accesses current_translations
# def get_translator(): ...

def get_action_keywords(language_code: str = None) -> Dict[str, Set[str]]:
    """Gets the action keywords for the specified or current language."""
    lang = language_code or CURRENT_LANGUAGE
    # Fallback to English if the language or its keywords aren't defined
    return ACTION_KEYWORDS.get(lang, ACTION_KEYWORDS.get('en', {}))

def get_replacements(language_code: str = None) -> Dict[str, str]:
    """Gets the text replacements for the specified or current language."""
    lang = language_code or CURRENT_LANGUAGE
    # Fallback to English if the language or its replacements aren't defined
    return REPLACEMENTS.get(lang, REPLACEMENTS.get('en', {}))

# --- Initialization ---
# Initial language will be set by GVM after config load.
# We can call set_language here with the default CURRENT_LANGUAGE to ensure
# 'current_translations' is populated at module import time if needed by any
# top-level calls to _(), though ideally GVM handles initial call.
# For robustness, _() will attempt to load if current_translations is empty.
logger.debug(f"i18n module loaded. Supported languages from JSON files: {SUPPORTED_LANGUAGES}")
set_language(CURRENT_LANGUAGE) # Load default 'en' translations at import time.

# Note: The main application logic (likely in GVM or main.py) should call 
# set_language(configured_language) early in the startup process based on 
# the user's configuration to override this default 'en' loading if needed. 