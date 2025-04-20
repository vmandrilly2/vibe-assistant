from pynput import mouse
import time

# Dictionary to keep track of button states
button_states = {}

def on_click(x, y, button, pressed):
    """Callback function for mouse click events."""
    button_name = str(button)
    if pressed:
        print(f'{button_name} Pressed at ({x}, {y})')
        button_states[button_name] = True
    else:
        print(f'{button_name} Released at ({x}, {y})')
        button_states[button_name] = False

def on_scroll(x, y, dx, dy):
    """Callback function for mouse scroll events."""
    print(f'Scrolled {"down" if dy < 0 else "up"} at ({x}, {y})')

# --- DPI Awareness (Important for accurate coordinates on Windows) ---
# Optional: If coordinates seem off, uncomment the following lines.
# Requires pywin32: pip install pywin32
# import ctypes
# try:
#     PROCESS_PER_MONITOR_DPI_AWARE = 2
#     ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
# except AttributeError: # Windows older than 8.1
#     try:
#         ctypes.windll.user32.SetProcessDPIAware()
#     except AttributeError: # Windows older than Vista
#         print("Warning: Cannot set DPI awareness.")
# except Exception as e:
#     print(f"Warning: Could not set DPI awareness: {e}")

# --- Listener Setup ---
print("Starting mouse listener... Press Ctrl+C in the terminal to stop.")
print("Try clicking all your mouse buttons: Left, Right, Middle (Wheel), Side Button 1, Side Button 2")

# Using a non-blocking listener and joining manually to allow Ctrl+C exit
listener = mouse.Listener(
    on_click=on_click,
    on_scroll=on_scroll)

listener.start()

try:
    # Keep the main thread alive while the listener runs
    while listener.is_alive():
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nStopping listener...")
    listener.stop()
    print("Listener stopped.")

print("\nFinal button states detected:")
for btn, state in button_states.items():
    print(f"- {btn}: {'Pressed' if state else 'Released'}")

print("Exiting.") 