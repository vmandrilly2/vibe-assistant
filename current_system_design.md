# Current System Design (As of Discussion)

## Introduction

The application aims to provide voice dictation, command execution via keywords, and optional translation capabilities. It was intended to have a modular design where features could be toggled on/off independently via `config.json`. However, the current implementation deviates from this goal, leading to tangled responsibilities and unexpected behavior when modules are toggled.

## Core Components & Issues

### 1. Orchestrator (`vibe_app.py`)

*   **Responsibilities:**
    *   Initializes most components (UI managers, listeners, core processors).
    *   Runs the main `asyncio` event loop.
    *   Manages STT connection lifecycle (starting/stopping `STTConnectionHandler` instances).
    *   Handles basic UI events (language/mode selection from `ui_action_queue`).
    *   Routes raw transcripts from `transcript_queue` to `DictationProcessor`.
    *   Receives processed results (text + potential action) from `DictationProcessor`.
    *   Queues final text for typing via `typing_queue` / `KeyboardSimulator`.
    *   Handles confirmed actions from `ActionConfirmManager`.
    *   Handles config reload requests (`systray_ui.config_reload_event`).
    *   Manages application shutdown.
*   **Issues:**
    *   The main loop directly interprets the combined output of `DictationProcessor`, blurring the lines between processing and dispatching.
    *   Error handling for config reloads was initially missing, causing crashes.
    *   Logic for deciding *when* to trigger translation is embedded here.
    *   Handles the `g_pending_action` state based on results from `DictationProcessor`, creating tight coupling.

### 2. Configuration (`config_manager.py`)

*   **Responsibilities:** Reads, writes, and provides access to `config.json`. Handles reloading.
*   **Status:** Generally OK. Provides a central point for configuration.

### 3. Input (`pynput` Listeners in `vibe_app.py`)

*   **Responsibilities:** Captures low-level mouse clicks and key presses/releases.
*   **Status:** OK. Sends basic event information (button, state, key) to the orchestrator or updates modifier state.

### 4. Audio Input (`background_audio_recorder.py`)

*   **Responsibilities:** Captures microphone audio into a buffer, allows retrieval of recent audio.
*   **Status:** OK. Appears modular and toggleable (`modules.audio_buffer_enabled`).

### 5. STT (`stt_manager.py`)

*   **Responsibilities:** Manages WebSocket connection to Deepgram, sends audio data (from buffer or live mic), receives transcript events (interim, final, metadata). Puts transcript data onto the central `transcript_queue`.
*   **Status:** OK. Handles STT interaction separately.

### 6. Transcript Processing (`dictation_processor.py`)

*   **Responsibilities (Current):**
    *   Receives final transcript string.
    *   Applies basic text replacements (using `i18n`).
    *   **Detects action keywords** (e.g., "enter", "escape") using `i18n`.
    *   **Modifies the text segment** by removing the detected keyword.
    *   Checks `config_manager.get("modules.action_confirm_enabled")`.
    *   If enabled, sends a "show" command to `action_confirm_queue`.
    *   Returns the *modified* text segment and an `action_to_confirm` value (None if confirmation disabled).
*   **Issues:**
    *   **Overloaded Responsibility:** Combines text normalization, action keyword detection, *and* interaction with the action confirmation flow.
    *   **Violates Modularity:** Directly depends on `config_manager` to check the state of *another* module (`ActionConfirmManager`). Directly interacts with `action_confirm_queue`.
    *   **Unintended Side Effects:** Removes the action keyword from the text *even if* action confirmation is disabled, preventing the keyword from being typed as text.
    *   **Tight Coupling:** Its output format is dictated by the needs of the confirmation flow in `vibe_app.py`.

### 7. Action Confirmation (`action_confirm_ui.py`)

*   **Responsibilities:** Displays a UI popup asking the user to confirm/cancel a detected action. Sends the result (`action_confirmed` command) back to `vibe_app.py` via `ui_action_queue`.
*   **Issues:** While the UI module itself might be correctly toggleable, it's triggered inappropriately by `DictationProcessor` based on logic that shouldn't reside there.

### 8. Action Execution (`keyboard_simulator.py`)

*   **Responsibilities:** Simulates keyboard typing and key presses (like Enter, Esc).
*   **Status:** OK. Acts as a service invoked by the orchestrator.

### 9. Translation (`openai_manager.py`)

*   **Responsibilities:** Handles API calls to OpenAI for translation.
*   **Status:** OK. Acts as a service invoked by the orchestrator. Toggleability depends on the orchestrator checking config before calling it.

### 10. UI Feedback Modules (`tooltip_manager.py`, `mic_ui_manager.py`, `session_monitor_ui.py`, `systray_ui.py`)

*   **Responsibilities:** Provide visual feedback (live transcript, status, session info, menus). Receive state updates/commands from `vibe_app.py` via dedicated queues. Send user interactions back to `vibe_app.py`.
*   **Status:** Generally OK. Appear modular and toggleable, communicating via queues.

## Overall Flow Issues

*   The processing of a final transcript is not a clean pipeline. Logic is split messily between `DictationProcessor` (detection, partial handling) and `vibe_app.py` (interpreting results, triggering typing/confirmation).
*   Toggling `modules.action_confirm_enabled` has the side effect of changing how `DictationProcessor` modifies text, which is non-intuitive and incorrect. The text processing should be independent of whether the confirmation UI is shown.
*   `DictationProcessor` has become coupled to configuration settings and queues outside its core responsibility. 