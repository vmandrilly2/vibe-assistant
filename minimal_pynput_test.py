# minimal_pynput_test.py
import logging
from pynput import mouse

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: [%(name)s] %(message)s')
logger = logging.getLogger(__name__)

def on_click(x, y, button, pressed):
    logger.debug(f"Click detected: Button={button}, Pressed={pressed} at ({x}, {y})")
    if button == mouse.Button.middle:
        if pressed:
            print("--- MIDDLE BUTTON PRESSED ---")
        else:
            print("--- MIDDLE BUTTON RELEASED ---")

logger.info("Starting minimal pynput mouse listener...")
listener = mouse.Listener(on_click=on_click)
try:
    listener.start()
    logger.info("Listener started. Press the middle mouse button...")
    listener.join() # Keep the script alive
except Exception as e:
    logger.error(f"An error occurred: {e}", exc_info=True)
except KeyboardInterrupt:
    logger.info("KeyboardInterrupt received, stopping listener.")
finally:
    if listener.is_alive():
        listener.stop()
    logger.info("Listener stopped.")

print("Script finished.") 