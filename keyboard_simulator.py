import asyncio
import logging
import platform
import time
from pynput import keyboard

logger = logging.getLogger(__name__)

class KeyboardSimulator:
    """Provides methods to simulate keyboard input using pynput."""

    def __init__(self, default_interval=0.01):
        self.controller = keyboard.Controller()
        self.default_interval = default_interval # Time between key presses/releases
        # Platform specific adjustments if needed
        if platform.system() == "Darwin":
            logger.warning("MacOS may require special permissions for pynput to control keyboard.")
        elif platform.system() == "Linux":
            # Linux might require specific display server context
            pass

    async def type_text(self, text: str, interval: float = None):
        """Simulates typing the given text character by character."""
        if interval is None:
            interval = self.default_interval
        logger.debug(f"Simulating typing: '{text}' with interval {interval}s")
        try:
            for char in text:
                # logger.debug(f"KeyboardSimulator: Typing character '{char}'") # Maybe too verbose?
                self.controller.press(char)
                await asyncio.sleep(interval / 2) # Short pause after press
                self.controller.release(char)
                await asyncio.sleep(interval / 2) # Short pause after release
            logger.debug(f"Finished typing: '{text}'")
        except Exception as e:
            logger.error(f"Error during type_text('{text}'): {e}", exc_info=True)

    async def press_key(self, key, interval: float = None):
        """Simulates pressing a single key (can be Key or KeyCode)."""
        if interval is None:
            interval = self.default_interval
        logger.debug(f"Simulating key press: {key}")
        try:
            self.controller.press(key)
            await asyncio.sleep(interval) # Hold duration or just pause
        except Exception as e:
            logger.error(f"Error during press_key({key}): {e}", exc_info=True)

    async def release_key(self, key):
        """Simulates releasing a single key."""
        logger.debug(f"Simulating key release: {key}")
        try:
            self.controller.release(key)
        except Exception as e:
            logger.error(f"Error during release_key({key}): {e}", exc_info=True)

    async def press_release_key(self, key, interval: float = None):
        """Simulates pressing and immediately releasing a single key."""
        if interval is None:
            interval = self.default_interval
        logger.debug(f"Simulating press & release: {key} with interval {interval}s")
        try:
            self.controller.press(key)
            await asyncio.sleep(interval / 2)
            self.controller.release(key)
            await asyncio.sleep(interval / 2)
        except Exception as e:
            logger.error(f"Error during press_release_key({key}): {e}", exc_info=True)

    async def press_modifiers(self, modifiers: list):
        """Presses a list of modifier keys."""
        logger.debug(f"Pressing modifiers: {modifiers}")
        try:
            for mod in modifiers:
                self.controller.press(mod)
                await asyncio.sleep(self.default_interval / 4) # Very short delay
        except Exception as e:
            logger.error(f"Error pressing modifiers {modifiers}: {e}", exc_info=True)

    async def release_modifiers(self, modifiers: list):
        """Releases a list of modifier keys (in reverse order)."""
        logger.debug(f"Releasing modifiers: {modifiers}")
        try:
            for mod in reversed(modifiers):
                self.controller.release(mod)
                await asyncio.sleep(self.default_interval / 4)
        except Exception as e:
            logger.error(f"Error releasing modifiers {modifiers}: {e}", exc_info=True)

    async def key_combination(self, modifiers: list, key, interval: float = None):
        """Simulates holding modifiers, pressing/releasing a key, then releasing modifiers."""
        if interval is None:
            interval = self.default_interval
        logger.debug(f"Simulating key combination: Modifiers={modifiers}, Key={key}")
        try:
            await self.press_modifiers(modifiers)
            await asyncio.sleep(interval / 2)
            await self.press_release_key(key, interval / 2)
            await asyncio.sleep(interval / 2)
            await self.release_modifiers(modifiers)
        except Exception as e:
            logger.error(f"Error during key_combination (Mods={modifiers}, Key={key}): {e}", exc_info=True)

# Example Usage (would be called by ActionExecutor)
# async def example():
#     simulator = KeyboardSimulator()
#     await simulator.type_text("Hello, World!")
#     await asyncio.sleep(0.5)
#     await simulator.press_release_key(keyboard.Key.enter)
#     await asyncio.sleep(0.5)
#     await simulator.key_combination([keyboard.Key.ctrl, keyboard.Key.shift], 's')
#
# if __name__ == "__main__":
#     logging.basicConfig(level=logging.DEBUG)
#     asyncio.run(example()) 