# i18n.py
import gettext
import os
import logging
from typing import Dict, Set, Tuple, Any

logger = logging.getLogger(__name__)

LOCALE_DIR = os.path.join(os.path.dirname(__file__), 'locales')
SUPPORTED_LANGUAGES = [d for d in os.listdir(LOCALE_DIR) if os.path.isdir(os.path.join(LOCALE_DIR, d))] if os.path.exists(LOCALE_DIR) else ['en']
CURRENT_LANGUAGE = 'en' # Default language
translations = None

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
        'new line': '\n', 'newline': '\n',
        # Add more English replacements
    },
    'fr': {
        'point': '.',
        'virgule': ',',
        'point d\'interrogation': '?',
        'point d\'exclamation': '!',
        'nouvelle ligne': '\n',
        'à la ligne': '\n',
        # Add more French replacements
    },
    # Add other languages
}

def set_language(language_code: str):
    """Sets the current language for translations."""
    global CURRENT_LANGUAGE, translations
    if language_code not in SUPPORTED_LANGUAGES:
        logger.warning(f"Language '{language_code}' not supported or locale files missing. Falling back to English.")
        language_code = 'en'

    CURRENT_LANGUAGE = language_code
    try:
        translations = gettext.translation('messages', localedir=LOCALE_DIR, languages=[language_code])
        translations.install() # Make _ available globally (use with care or pass translator)
        logger.info(f"Set application language to: {language_code}")
    except FileNotFoundError:
        logger.warning(f"Locale file not found for language '{language_code}'. Using fallback (English/defaults).")
        translations = gettext.NullTranslations()
        translations.install()
    except Exception as e:
        logger.error(f"Error setting language to {language_code}: {e}", exc_info=True)
        translations = gettext.NullTranslations()
        translations.install()

def get_translator():
    """Gets the current translator object."""
    global translations
    if translations is None:
        set_language(CURRENT_LANGUAGE) # Ensure initialized
    return translations

def _(text: str) -> str:
    """Translates the given text using the current language settings."""
    translator = get_translator()
    return translator.gettext(text)

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
# Set initial language (e.g., from config or default)
# In the new design, the GVM might load the language from config
# and call set_language early on.
# set_language(CURRENT_LANGUAGE) # Initial setup

# Note: For this refactoring, the responsibility of calling `set_language`
# based on the configuration stored in the GVM will likely fall to a component
# that observes language changes in the GVM state, or perhaps the GVM itself
# during initialization or config reloads. 