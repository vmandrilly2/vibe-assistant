import time
import logging
from pynput import keyboard

class KeyboardSimulator:
    """Handles keyboard simulation actions."""
    def __init__(self):
        try:
            self.kb_controller = keyboard.Controller()
            logging.info("KeyboardSimulator initialized.")
        except Exception as e:
            logging.error(f"Failed to initialize pynput keyboard controller: {e}", exc_info=True)
            # How to handle this? Raise the exception? Set a flag?
            # For now, log the error. Methods below should check self.kb_controller.
            self.kb_controller = None

    def simulate_typing(self, text):
        """Simulates typing the given text."""
        if not self.kb_controller:
            logging.error("Keyboard controller not available, cannot simulate typing.")
            return
        try:
            logging.info(f"Simulating type: '{text}'")
            self.kb_controller.type(text)
        except Exception as e:
            logging.error(f"Error during simulate_typing: {e}", exc_info=True)

    def simulate_backspace(self, count):
        """Simulates pressing backspace multiple times."""
        if not self.kb_controller:
            logging.error("Keyboard controller not available, cannot simulate backspace.")
            return
        if count <= 0:
            return
        try:
            logging.info(f"Simulating {count} backspaces")
            for _ in range(count):
                self.kb_controller.press(keyboard.Key.backspace)
                self.kb_controller.release(keyboard.Key.backspace)
                time.sleep(0.01) # Small delay between key presses
        except Exception as e:
            logging.error(f"Error during simulate_backspace: {e}", exc_info=True)


    def simulate_key_press_release(self, key_obj):
        """Simulates pressing and releasing a single key."""
        if not self.kb_controller:
            logging.error("Keyboard controller not available, cannot simulate key press/release.")
            return
        try:
            logging.debug(f"Simulating press/release: {key_obj}")
            self.kb_controller.press(key_obj)
            self.kb_controller.release(key_obj)
            time.sleep(0.02) # Small delay between keys
        except Exception as e:
            logging.error(f"Failed to simulate key {key_obj}: {e}")

    def simulate_key_combination(self, keys):
        """Simulates pressing modifier keys, then a main key, then releasing all."""
        if not self.kb_controller:
            logging.error("Keyboard controller not available, cannot simulate key combination.")
            return
        if not keys:
            return

        modifiers = []
        main_key = None
        try:
            # Separate modifiers from the main key
            for key_obj in keys:
                # Check if key is a modifier using pynput's attributes
                # Use isinstance to be safer with different Key types
                if isinstance(key_obj, keyboard.Key) and hasattr(key_obj, 'value') and key_obj.value.is_modifier:
                     modifiers.append(key_obj)
                # Check specific modifier keys if the above doesn't work reliably
                elif key_obj in [keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
                                 keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
                                 keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
                                 keyboard.Key.cmd]: # Add cmd for Mac if needed
                     modifiers.append(key_obj)
                elif main_key is None: # First non-modifier is the main key
                    main_key = key_obj
                else:
                     logging.warning(f"Multiple non-modifier keys found in combination: {keys}. Using first: {main_key}")

            if not main_key: # Maybe it was just modifiers? (e.g., "press control") - less common
                if modifiers:
                    logging.info(f"Simulating modifier press/release only: {modifiers}")
                    for mod in modifiers: self.kb_controller.press(mod)
                    time.sleep(0.05) # Hold briefly
                    for mod in reversed(modifiers): self.kb_controller.release(mod)
                else:
                    logging.warning("No main key or modifiers found in combination.")
                return

            # Press modifiers
            logging.info(f"Simulating combo: Modifiers={modifiers}, Key={main_key}")
            for mod in modifiers:
                self.kb_controller.press(mod)
            time.sleep(0.05) # Brief pause after modifiers

            # Press and release main key
            self.kb_controller.press(main_key)
            self.kb_controller.release(main_key)
            time.sleep(0.05) # Brief pause after main key

            # Release modifiers (in reverse order)
            for mod in reversed(modifiers):
                self.kb_controller.release(mod)

        except Exception as e:
            logging.error(f"Error simulating key combination {keys}: {e}", exc_info=True)
            # Attempt to release any potentially stuck keys
            if self.kb_controller: # Check again if controller exists
                if main_key:
                    try: self.kb_controller.release(main_key)
                    except: pass # Ignore errors on release attempt
                for mod in reversed(modifiers):
                    try: self.kb_controller.release(mod)
                    except: pass # Ignore errors on release attempt

# Example usage (for testing the module directly)
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
    print("Testing KeyboardSimulator...")
    simulator = KeyboardSimulator()

    if simulator.kb_controller:
        print("Waiting 3 seconds before typing...")
        time.sleep(3)

        print("Typing 'Hello World!'")
        simulator.simulate_typing('Hello World!')
        time.sleep(1)

        print("Pressing Enter")
        simulator.simulate_key_press_release(keyboard.Key.enter)
        time.sleep(1)

        print("Typing 'abc' then backspacing 3 times")
        simulator.simulate_typing('abc')
        time.sleep(0.5)
        simulator.simulate_backspace(3)
        time.sleep(1)

        print("Simulating Ctrl+C")
        # Note: Use Key.ctrl_l or Key.ctrl_r if specific side is needed
        simulator.simulate_key_combination([keyboard.Key.ctrl, 'c'])
        time.sleep(1)

        print("Simulating Shift+A (should type 'A')")
        simulator.simulate_key_combination([keyboard.Key.shift, 'a'])
        time.sleep(1)

        print("Test finished.")
    else:
        print("Keyboard controller failed to initialize. Test aborted.") 