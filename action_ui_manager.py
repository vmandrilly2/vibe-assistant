# action_ui_manager.py
import asyncio
import logging
import tkinter as tk
from tkinter import ttk
import threading
from typing import List, Optional, Tuple

# Assuming GVM access and constants
# from global_variables_manager import GlobalVariablesManager
from constants import (
    STATE_UI_CONFIRMATION_REQUEST,
    STATE_SESSION_RECOGNIZED_ACTIONS_TEMPLATE,
    STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS
)

logger = logging.getLogger(__name__)

class ActionUIManager:
    """Manages the UI for confirming detected actions."""

    def __init__(self, gvm, main_loop: asyncio.AbstractEventLoop):
        logger.info("ActionUIManager initialized.")
        self.gvm = gvm
        self.main_loop = main_loop
        self.root: Optional[tk.Tk] = None
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event() # Use threading event for Tkinter thread
        self._ui_visible = False
        self._current_session_id: Optional[str] = None
        self._hide_timer: Optional[asyncio.TimerHandle] = None # Use asyncio timer

    def _show_ui(self, session_id: str, actions: List[str]):
        """Creates and shows the Tkinter window in its own thread."""
        if self.thread and self.thread.is_alive():
            logger.warning("Action UI thread already running. Attempting to stop first.")
            self._stop_ui_thread()

        self._current_session_id = session_id
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run_tk_app, args=(actions,), daemon=True, name="ActionUIThread")
        self.thread.start()

    def _run_tk_app(self, actions: List[str]):
        """The main function for the Tkinter thread."""
        logger.debug("Action UI Tkinter thread started.")
        try:
            self.root = tk.Tk()
            self.root.title("Confirm Action")
            self.root.attributes("-topmost", True)
            self.root.geometry("+100+100") # Position placeholder
            self.root.overrideredirect(True) # Frameless

            style = ttk.Style(self.root)
            style.theme_use('clam') # Use a theme that supports rounded buttons if possible
            style.configure('Action.TButton', padding=6, relief="flat", font=('Segoe UI', 10), borderwidth=0)
            style.map('Action.TButton',
                      foreground=[('active', '#0078D7'), ('disabled', '#B0B0B0')],
                      background=[('active', '#CCE4F7'), ('pressed', '#92C1ED')],
                      focuscolor=('focus', '#E5F1FB'),
                      highlightcolor=('focus', '#0078D7'))
            
            main_frame = ttk.Frame(self.root, padding="10 5 10 5", style='App.TFrame')
            main_frame.pack(expand=True, fill='both')
            style.configure('App.TFrame', background='#F0F0F0')

            ttk.Label(main_frame, text="Confirm Action:", font=('Segoe UI', 11, 'bold'), background='#F0F0F0').pack(pady=(0, 5))

            # Display up to 5 unique recent actions
            # TODO: Get history/uniqueness logic if needed
            limited_actions = actions[-5:] # Simple slice for now

            for action in limited_actions:
                btn = ttk.Button(main_frame, text=action, style='Action.TButton', command=lambda a=action: self._on_action_click(a))
                btn.pack(fill='x', pady=2)
                # Bind hover events to update GVM state
                btn.bind("<Enter>", lambda e, a=action: self._on_action_hover(a))
                btn.bind("<Leave>", lambda e: self._on_action_leave())

            self._ui_visible = True
            logger.debug("Action UI window displayed.")
            
            # Schedule hiding after timeout using asyncio loop from the main thread
            asyncio.run_coroutine_threadsafe(self._schedule_hide(3.0), self.main_loop) # Use stored loop

            self.root.mainloop()

        except Exception as e:
            logger.error(f"Error in Action UI Tkinter thread: {e}", exc_info=True)
        finally:
             logger.debug("Action UI Tkinter mainloop finished.")
             self._ui_visible = False
             self.root = None # Clear reference

    async def _schedule_hide(self, delay: float):
         """Schedules the UI to hide after a delay using asyncio."""
         if self._hide_timer:
              self._hide_timer.cancel()
         logger.debug(f"Scheduling Action UI hide in {delay}s")
         self._hide_timer = asyncio.get_running_loop().call_later(delay, self._request_hide_from_thread)

    def _request_hide_from_thread(self):
        """Requests UI hide, ensuring it runs in the Tkinter thread if needed."""
        # This callback runs in the main asyncio loop. We need to ensure
        # _hide_ui runs in the Tkinter thread.
        if self.root and self._ui_visible:
             logger.debug("Timer expired, requesting UI hide.")
             # Schedule _hide_ui to run in the Tkinter thread
             self.root.after(0, self._hide_ui)
        self._hide_timer = None # Clear timer handle
        
    def _hide_ui(self):
        """Hides the Tkinter window. Must be called from Tkinter thread."""
        if self.root and self._ui_visible:
            logger.debug("Hiding Action UI window.")
            self.root.quit() # Stop mainloop
            self.root.destroy()
            self._ui_visible = False
            # Clear GVM state related to confirmed actions on hide? Debatable.
            # asyncio.run_coroutine_threadsafe(self.gvm.set(STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS, []), self.gvm.get_main_loop())
            
    def _stop_ui_thread(self):
         """Signals the Tkinter thread to stop and waits for it."""
         if self.thread and self.thread.is_alive():
              logger.debug("Stopping existing Action UI thread...")
              self._stop_event.set() # Signal thread (though mainloop exit is primary)
              if self.root: # If window exists, schedule quit
                   try: self.root.after(0, self._hide_ui) # Ensure called from Tkinter thread
                   except tk.TclError: pass # Ignore if root is already destroyed
              self.thread.join(timeout=1.0)
              if self.thread.is_alive():
                   logger.warning("Action UI thread did not stop gracefully.")
              self.thread = None
              self._ui_visible = False
              self.root = None

    def _on_action_hover(self, action: str):
        """Called when mouse enters an action button. Updates GVM state."""
        if not self._current_session_id:
             return
        logger.debug(f"Hover detected on action: {action}")
        # Schedule GVM update in the main asyncio loop
        asyncio.run_coroutine_threadsafe(
            self.gvm.set(STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS, [(self._current_session_id, action)]),
            self.main_loop
        )
        # Optionally hide immediately on hover? Or wait for click/timeout?
        # self._request_hide_from_thread() 

    def _on_action_leave(self):
        """Called when mouse leaves an action button. Clears GVM state?"""
        # Decide if leaving should clear the confirmed action
        # logger.debug("Hover left action button.")
        # asyncio.run_coroutine_threadsafe(
        #     self.gvm.set(STATE_UI_CONFIRMATION_CONFIRMED_ACTIONS, []),
        #     self.gvm.get_main_loop()
        # )
        pass
        
    def _on_action_click(self, action: str):
        """Called when an action button is clicked."""
        # Currently, hover triggers the GVM update. Click might just hide UI faster.
        logger.debug(f"Action button '{action}' clicked.")
        self._request_hide_from_thread() # Hide UI immediately on click

    async def run_loop(self):
        """Monitors GVM state for confirmation requests."""
        logger.info("ActionUIManager run_loop starting.")
        while True:
            try:
                # Wait for a confirmation request
                await self.gvm.wait_for_change(STATE_UI_CONFIRMATION_REQUEST)
                request_data = await self.gvm.get(STATE_UI_CONFIRMATION_REQUEST)
                
                if request_data: # Check if data exists (could be set to None/empty to clear)
                    # Assume request_data is { "session_id": str, "actions": List[str] }
                    session_id = request_data.get("session_id")
                    actions = request_data.get("actions")
                    
                    if session_id and actions:
                        logger.info(f"Received action confirmation request for session {session_id} with actions: {actions}")
                        # Clear the request state immediately to prevent re-triggering
                        await self.gvm.set(STATE_UI_CONFIRMATION_REQUEST, None) 
                        # Show the UI in its thread
                        self._show_ui(session_id, actions)
                    else:
                         logger.warning(f"Invalid confirmation request data: {request_data}")
                         # Clear invalid request
                         await self.gvm.set(STATE_UI_CONFIRMATION_REQUEST, None) 
                
            except asyncio.CancelledError:
                logger.info("ActionUIManager run_loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in ActionUIManager run_loop: {e}", exc_info=True)
                await asyncio.sleep(1) # Avoid tight loop on error
                
        await self.cleanup()
        logger.info("ActionUIManager run_loop finished.")

    async def cleanup(self):
        """Stops the UI thread and cleans up."""
        logger.info("ActionUIManager cleaning up...")
        if self._hide_timer:
             self._hide_timer.cancel()
             self._hide_timer = None
        self._stop_ui_thread()
        logger.info("ActionUIManager cleanup finished.")

# Note: Requires GVM to have get_main_loop() method.
# Assumes the UI confirmation request state contains session_id and actions list.
# Interaction logic (hover vs click confirmation) might need refinement based on desired UX. 