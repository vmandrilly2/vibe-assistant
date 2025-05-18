import logging
import queue
import pyautogui
from pynput.keyboard import Key # For action execution check later

# Assuming KeyboardSimulator is imported where needed or passed in
# from keyboard_simulator import KeyboardSimulator
from i18n import _, get_current_language, ALL_DICTATION_REPLACEMENTS

class DictationProcessor:
    """Handles the processing of final dictation results, including corrections and keyword actions."""

    def __init__(self, keyboard_sim, action_confirm_q: queue.Queue, transcription_active_event):
        """
        Args:
            keyboard_sim: Instance of KeyboardSimulator for typing actions.
            action_confirm_q: Queue for triggering the ActionConfirmManager UI.
            transcription_active_event: Event signalling if transcription is active (for tooltip checks - REMOVE THIS? Not used anymore here).
        """
        self.keyboard_sim = keyboard_sim
        self.action_confirm_queue = action_confirm_q
        self.transcription_active_event = transcription_active_event
        logging.info("DictationProcessor initialized.")

    def handle_final(self, final_transcript: str, history: list, activation_id):
        """Handles the final dictation transcript segment based on history.
        Calculates target state, determines diff, executes typing, and updates history.
        Detects potential action keywords and returns them for confirmation handling.

        Args:
            final_transcript: The final transcript segment from Deepgram.
            history: The current list of typed word history entries.
            activation_id: The unique ID for this activation sequence.

        Returns:
            tuple: (new_history_list, final_text_string_typed, action_to_confirm)
                   action_to_confirm will be None if no action keyword was detected.
        """
        logging.debug(f"DictationProcessor handling final segment '{final_transcript}' for ID {activation_id}")

        action_to_confirm = None # Initialize

        # --- Step A: Calculate Target Word List & Detect Actions --- >

        target_words = [entry['text'] for entry in history] # Start with existing words
        logging.debug(f"Initial target_words from history: {target_words}")

        original_words = final_transcript.split()
        punctuation_to_strip = '.,!?;:'

        # --- Get translated keywords & replacements --- >
        back_keywords_str = _("dictation.backspace_keywords", default="back")
        back_keywords = set(kw.strip().lower() for kw in back_keywords_str.split(',') if kw.strip())
        enter_keywords_str = _("dictation.enter_keywords", default="enter")
        enter_keywords = set(kw.strip().lower() for kw in enter_keywords_str.split(',') if kw.strip())
        escape_keywords_str = _("dictation.escape_keywords", default="escape")
        escape_keywords = set(kw.strip().lower() for kw in escape_keywords_str.split(',') if kw.strip())
        current_lang_base = get_current_language()
        replacements = ALL_DICTATION_REPLACEMENTS.get(current_lang_base, {})
        logging.debug(f"Using keywords for '{current_lang_base}': back={back_keywords}, enter={enter_keywords}, escape={escape_keywords}, replacements={len(replacements)}")
        # --- End Get i18n data --- >

        # --- Combine all potential triggers (spoken phrases) --- >
        all_triggers = {}
        for phrase in enter_keywords: all_triggers[phrase] = "Enter"
        for phrase in escape_keywords: all_triggers[phrase] = "Escape"
        for phrase, action_char in replacements.items():
            if phrase not in all_triggers: all_triggers[phrase] = action_char
        sorted_trigger_phrases = sorted(all_triggers.keys(), key=len, reverse=True)
        # --- End Combine and Sort --- >

        # --- Check for triggers at the end of the transcript --- >
        trigger_found = False
        trigger_phrase_length = 0
        text_segment_to_process = final_transcript # Default to full transcript
        processed_transcript_for_match = final_transcript.lower()
        if processed_transcript_for_match.endswith('.'): # Strip ONLY trailing period for matching
            processed_transcript_for_match = processed_transcript_for_match[:-1]

        for phrase in sorted_trigger_phrases:
            if phrase and (processed_transcript_for_match == phrase or processed_transcript_for_match.endswith(f" {phrase}")):
                trigger_found = True
                action_to_confirm = all_triggers[phrase] # STORE the detected action
                # --- Use simple approximation for trigger length --- >
                if processed_transcript_for_match == phrase:
                     trigger_phrase_length = len(final_transcript)
                else:
                     trigger_phrase_length = len(phrase) + 1
                # --- End simple approximation --- >
                text_segment_to_process = final_transcript[:-trigger_phrase_length].rstrip()
                logging.info(f"Detected trigger phrase: '{phrase}' -> Action: '{action_to_confirm}'. Text to process: '{text_segment_to_process}'")

                # --- Show confirmation UI --- >
                try:
                    pos = pyautogui.position()
                    if self.action_confirm_queue:
                        self.action_confirm_queue.put_nowait(("show", {"action": action_to_confirm, "pos": pos}))
                        logging.debug(f"Sent '{action_to_confirm}' action to confirmation queue.")
                        # g_pending_action = action_to_confirm # Managed by caller (vibe_app)
                        # g_action_confirmed = False # Managed by caller (vibe_app)
                    else:
                        logging.warning("Action Confirm queue not available, cannot show confirmation.")
                        # Don't reset action_to_confirm here, let vibe_app decide based on config
                        # action_to_confirm = None
                        # trigger_found = False # Keep trigger found, let vibe_app handle execution if needed
                except queue.Full:
                    logging.warning(f"Action confirmation queue full. Cannot show confirmation UI for '{action_to_confirm}'.")
                    # Keep action, let vibe_app handle execution if needed and confirmation disabled
                except Exception as e:
                    logging.error(f"Error sending 'show' for '{action_to_confirm}' to ActionConfirmManager: {e}")
                    # Keep action, maybe vibe_app can still execute if confirmation disabled
                    # action_to_confirm = None
                    # trigger_found = False

                break # Stop after finding the longest match
        # --- End trigger checking logic --- >

        # --- Process the determined text segment --- >
        target_words = [entry['text'] for entry in history] # Start with existing words again for processing
        original_words_segment = text_segment_to_process.split()

        # --- Simplified: Append all words from the segment (no backspace handling) --- >
        for word in original_words_segment:
            if word: # Append original word with punctuation
                target_words.append(word)
        # --- End Simplified ---

        logging.debug(f"Final target_words after segment processing: {target_words}")

        # --- Step B: Calculate Target Text --- >
        target_text = " ".join(target_words) + (' ' if target_words else '')
        # logging.debug(f"Processor calculated target_text: '{target_text}'")

        # --- Calculate text based on OLD history --- >
        old_text = " ".join([entry['text'] for entry in history]) + (' ' if history else '')

        # --- Determine the NEW text to be typed (diff) --- >
        if target_text.startswith(old_text):
            text_to_queue_for_typing = target_text[len(old_text):]
        else:
            # Fallback if something unexpected happened (e.g., history divergence?)
            # In this simplified model, just queue the whole new target text
            logging.warning(f"Target text '{target_text}' did not start with old text '{old_text}'. Queuing full target.")
            text_to_queue_for_typing = target_text

        # --- Step F: Update History to Match Target State --- >
        new_history = []
        if target_words:
            # logging.debug(f"Processor rebuilding history with: {target_words}")
            for word in target_words:
                if word:
                    # Calculate length including the expected space after the word
                    length_with_space = len(word) + 1
                    entry = {"text": word, "length_with_space": length_with_space}
                    new_history.append(entry)
        # else: logging.debug("Processor history cleared.")

        # Return updated history, the full text for this segment, and detected action
        return new_history, text_to_queue_for_typing, action_to_confirm

    # Methods handle_interim and handle_final will be added next. 