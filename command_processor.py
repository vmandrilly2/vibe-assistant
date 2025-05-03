import logging
import json
import asyncio

# Assuming these managers/simulators are passed during initialization
# from openai_manager import OpenAIManager
# from keyboard_simulator import KeyboardSimulator
from pynput.keyboard import Key, KeyCode
# --- MODIFICATION START ---
# Assuming PYNPUT_KEY_MAP is accessible, maybe move it to a shared constants file later?
# Or pass it in during init. For now, we'll assume it's defined elsewhere or copied.
# from vibe_app import PYNPUT_KEY_MAP # Example if it remains in vibe_app
from constants import PYNPUT_KEY_MAP # Import from constants
# --- MODIFICATION END ---

# --- REMOVAL START ---
# Placeholder for PYNPUT_KEY_MAP if not imported
# PYNPUT_KEY_MAP = {
#     "enter": Key.enter, "esc": Key.esc, "tab": Key.tab, "space": Key.space,
#     "backspace": Key.backspace, "delete": Key.delete, "insert": Key.insert,
#     "home": Key.home, "end": Key.end, "pageup": Key.page_up, "pagedown": Key.page_down,
#     "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
#     "shift": Key.shift, "ctrl": Key.ctrl, "alt": Key.alt, "cmd": Key.cmd,
#     # Add other necessary keys from vibe_app.py's map here...
#     "f1": Key.f1, # etc.
#     "dot": ".", "comma": ",", # etc.
# }
# --- REMOVAL END ---

class CommandProcessor:
    """Handles interpretation and execution of spoken commands."""

    def __init__(self, openai_manager, keyboard_sim, config):
        """
        Initializes the CommandProcessor.

        Args:
            openai_manager: Instance of OpenAIManager.
            keyboard_sim: Instance of KeyboardSimulator.
            config: The application's configuration dictionary.
        """
        self.openai_manager = openai_manager
        self.keyboard_sim = keyboard_sim
        self.config = config
        # Extract relevant config settings during init or access directly via self.config
        self.openai_model = self.config.get("general", {}).get("openai_model", "gpt-4.1-nano")
        self.modules_config = self.config.get("modules", {})
        self.command_interpretation_enabled = self.modules_config.get("command_interpretation_enabled", False)

        # Use the imported map
        self.key_map = PYNPUT_KEY_MAP

        logging.info("CommandProcessor initialized.")

    async def process_command(self, final_transcript: str):
        """
        Processes the final command transcript. Sends to OpenAI if enabled,
        parses the response, and simulates keyboard actions.

        Args:
            final_transcript: The final text transcript of the command.
        """
        # Logic from vibe_app.handle_command_final will be moved here.
        logging.debug(f"CommandProcessor received: '{final_transcript}'")

        if not final_transcript:
            logging.warning("CommandProcessor: Empty transcript received.")
            return

        # --- Check if command interpretation module is enabled --- >
        if not self.command_interpretation_enabled:
            logging.info("Command Interpretation module disabled in config. Skipping OpenAI call.")
            return
        # --- End Check --- >

        # --- Check if openai_manager is available --- >
        if not self.openai_manager:
            logging.error("OpenAI Manager not available in CommandProcessor. Cannot process command.")
            return
        # --- End Check --- >

        # --- Prepare OpenAI Request --- >
        # Create a simplified list of key names for the prompt
        # Using self.key_map which should be populated
        valid_key_names_str = ", ".join(list(self.key_map.keys())[:40]) + ", etc." # Limit length for prompt

        system_prompt = f"""
You are an AI assistant that translates natural language commands into keyboard actions.
The user will provide text representing desired keyboard input.
Analyze the input and determine the sequence of keys to press.
Output *only* a JSON object containing a single key "keys" with a value being a list of strings/lists, where each item is either:
1. A special key name exactly as found in this list (lowercase): {valid_key_names_str}
2. A single character to be typed (e.g., "a", "b", "1", "$", "?").
3. A combination represented as a list within the list, e.g., ["ctrl", "c"] or ["shift", "a"].
Modifiers should always come first in combinations. Do NOT output phrases like "press", "type", "key".
Focus on interpreting common keyboard commands like "enter", "delete", "control c", "page down", "type hello", "shift 1".
If the user says "type" followed by text, represent each character as a string in the list. Example: "type abc" -> {{"keys": ["a", "b", "c"]}}.
If the user says "press" followed by a key name, output that key name. Example: "press delete" -> {{"keys": ["delete"]}}.
If the user asks for a combination, output the list. Example: "press control alt delete" -> {{"keys": [["ctrl", "alt", "delete"]]}}.
Be precise. If unsure, return an empty list: {{"keys": []}}.
"""
        user_prompt = f"User input: \"{final_transcript}\""

        logging.info(f"Requesting AI key interpretation for command: '{final_transcript}'")

        try:
            # Call the generic method in OpenAIManager
            response_content = await self.openai_manager.get_openai_completion(
                model=self.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=150,
                response_format={"type": "json_object"} # Request JSON object
            )

            if response_content is None:
                logging.error("Failed to get command interpretation from OpenAI.")
                return

            logging.debug(f"OpenAI Keyboard Response Raw: {response_content}")

            # Parse the JSON response
            parsed_keys = []
            try:
                response_data = json.loads(response_content)
                if isinstance(response_data, dict) and "keys" in response_data and isinstance(response_data["keys"], list):
                    parsed_keys = response_data["keys"]
                    logging.info(f"Parsed keys from OpenAI: {parsed_keys}")
                else:
                    logging.error(f"OpenAI response JSON structure incorrect: Expected dict with 'keys' list, got: {response_content}")

            except json.JSONDecodeError:
                logging.error(f"Failed to decode OpenAI JSON response: {response_content}")
            except Exception as e:
                logging.error(f"Error parsing OpenAI response keys: {e}")

            # Simulate Key Presses
            if not self.keyboard_sim:
                 logging.error("KeyboardSimulator not available in CommandProcessor.")
                 return

            if not parsed_keys:
                logging.warning("No valid keys parsed from OpenAI response.")
                return

            for item in parsed_keys:
                if isinstance(item, str): # Single key or character
                    key_name_lower = item.lower()
                    key_obj = self.key_map.get(key_name_lower)
                    if key_obj:
                        if isinstance(key_obj, str):
                            self.keyboard_sim.simulate_typing(key_obj)
                        else:
                            self.keyboard_sim.simulate_key_press_release(key_obj)
                    elif len(item) == 1:
                        self.keyboard_sim.simulate_typing(item)
                    else:
                        logging.warning(f"Unknown single key name: '{item}'")

                elif isinstance(item, list): # Key combination
                    combo_keys = []
                    valid_combo = True
                    for key_name in item:
                        if isinstance(key_name, str):
                            key_name_lower = key_name.lower()
                            key_obj = self.key_map.get(key_name_lower)
                            if key_obj:
                                if isinstance(key_obj, str) and len(key_obj) == 1:
                                    combo_keys.append(KeyCode.from_char(key_obj))
                                elif not isinstance(key_obj, str):
                                    combo_keys.append(key_obj)
                                else:
                                    logging.warning(f"Mapped key '{key_name}' -> '{key_obj}' invalid for combination.")
                                    valid_combo = False; break
                            elif len(key_name) == 1:
                                try: combo_keys.append(KeyCode.from_char(key_name))
                                except Exception: logging.warning(f"Could not get KeyCode for char '{key_name}'"); valid_combo = False; break
                            else:
                                logging.warning(f"Unknown key name in combination: '{key_name}'")
                                valid_combo = False; break
                        else:
                            logging.warning(f"Invalid item type in combination list: {key_name}")
                            valid_combo = False; break

                    if valid_combo and combo_keys:
                        self.keyboard_sim.simulate_key_combination(combo_keys)
                    elif not combo_keys:
                        logging.warning(f"Empty key list derived from combination: {item}")

                else:
                    logging.warning(f"Unexpected item type in parsed keys list: {item}")

        except Exception as e:
            logging.error(f"Error during command processing: {e}", exc_info=True) 