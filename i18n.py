import json
import os
import logging

"""Handles internationalization of the application.
Localization Languages list: see locales/ folder.
Below, dictation replacements are defined for French, and should be moved to french locale files. And equivalent replacements should be added for other languages.
"""

LOCALE_DIR = "locales"
DEFAULT_LOCALE = "en" # Default language if selected one is not found

_translations = {}
_current_lang = None

# --- Dictation Replacements (Moved from vibe_app.py) --- >
# Maps spoken words (lowercase) to characters for replacement in Dictation mode.
DICTATION_REPLACEMENTS_FR = {
    # Punctuation
    "point": ".",
    "virgule": ",",
    "point virgule": ";",
    "deux points": ":", # Adjusted from "2 points" for likely spoken form
    "2 points": ":",  # Added for Deepgram output with numerals=True
    "point d'interrogation": "?",
    "Point d'interrogation,": "?",
    "point d'exclamation": "!",
    # Symbols
    "arobase": "@",
    "dièse": "#", # Also known as croisillon
    "dollar": "$",
    "pourcent": "%",
    "et commercial": "&",
    "astérisque": "*",
    "plus": "+",
    "moins": "-",
    "égal": "=",
    "barre oblique": "/", # Slash
    "barre oblique inversée": "\\", # Backslash
    "barre verticale": "|", # Pipe
    "soulignement": "_", # Underscore
    "trait d'union": "-", # Hyphen
    "tiret": "-", # Added hyphen alternative
    "slash": "/", # Added Slash
    # Quotes
    "apostrophe": "'",
    "guillemet": '"',
    "guillemet simple": "'",
    "guillemet double": '"',
    # Parentheses/Brackets
    "parenthèse ouvrante": "(",
    "parenthèse fermante": ")",
    "crochet ouvrant": "[",
    "crochet fermant": "]",
    "accolade ouvrante": "{",
    "accolade fermante": "}",
    "chevron ouvrant": "<",
    "chevron fermant": ">",
}

# Add replacement dicts for other languages here
# DICTATION_REPLACEMENTS_EN = { ... }

ALL_DICTATION_REPLACEMENTS = {
    "fr": DICTATION_REPLACEMENTS_FR,
    # "en": DICTATION_REPLACEMENTS_EN,
}

# --- End Dictation Replacements --- >

def load_translations(lang_code):
    """Loads translation strings for the given language code."""
    global _translations, _current_lang
    
    if not lang_code:
        lang_code = DEFAULT_LOCALE
        logging.warning(f"No language code provided, falling back to default: {DEFAULT_LOCALE}")

    # Extract base language code (e.g., 'en' from 'en-US')
    base_lang_code = lang_code.split('-')[0].lower()

    file_path = os.path.join(LOCALE_DIR, f"{base_lang_code}.json")
    default_file_path = os.path.join(LOCALE_DIR, f"{DEFAULT_LOCALE}.json")

    loaded_data = {}
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
            _current_lang = base_lang_code
            logging.info(f"Loaded translations for: {base_lang_code}")
        elif os.path.exists(default_file_path):
            logging.warning(f"Translation file not found for '{base_lang_code}'. Loading default '{DEFAULT_LOCALE}'.")
            with open(default_file_path, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
            _current_lang = DEFAULT_LOCALE
        else:
            logging.error(f"Default translation file '{default_file_path}' not found. No translations loaded.")
            _current_lang = None
            loaded_data = {}

    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from translation file {file_path} or {default_file_path}: {e}")
        loaded_data = {}
        _current_lang = None
    except Exception as e:
        logging.error(f"Error loading translation file: {e}")
        loaded_data = {}
        _current_lang = None
        
    _translations = loaded_data
    return _translations # Return the loaded translations

def get_translation(key, default=None, **kwargs):
    """Retrieves a translation string for the given key.

    Args:
        key: The key for the translation string (e.g., 'menu.mode').
        default: The default value to return if the key is not found.
        **kwargs: Values for placeholder substitution in the translation string.

    Returns:
        The translated string, or the default value, or the key itself if not found.
    """
    if not _translations:
        # logging.warning("Translation system not initialized or failed to load.")
        return default if default is not None else key

    # Navigate nested keys
    keys = key.split('.')
    value = _translations
    try:
        for k in keys:
            value = value[k]
        
        if isinstance(value, str):
            # Perform substitution if kwargs are provided
            if kwargs:
                return value.format(**kwargs)
            return value
        else:
            # Handle cases where the key exists but value is not a string (e.g., nested dict)
            logging.warning(f"Translation value for key '{key}' is not a string: {value}")
            return default if default is not None else key

    except KeyError:
        # logging.warning(f"Translation key '{key}' not found for language '{_current_lang}'.")
        return default if default is not None else key
    except Exception as e:
        logging.error(f"Error retrieving translation for key '{key}': {e}")
        return default if default is not None else key

def get_current_language():
    """Returns the currently loaded language code."""
    return _current_lang

# Alias for convenience
_ = get_translation 