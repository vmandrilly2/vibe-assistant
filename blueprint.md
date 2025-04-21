# This file contains the core request for this project

This file SHALL be updated by me or the AI assistant/coworker. It does NOT contain code, but shall include EVERYTHING functional that was included in the code, so that if the code disapear or needs to be rebuilt, this file alone shall be enough indication to recreate completely what was coded.

# Purpose

This project shall make it very easy for me to write text AND code, as well as EDIT text and code, with much less keybord presses needed if any at all. Mouse actions shall prevail (Mouse has 5 buttons: left, right, wheel press + 2 side buttons that are never used by other softares and therefore should be used in priority for this project).

# Task 1: let's make a plan. 

Shall we make a search to consider what already exists (especially on github) that could be tested and modified，or would it be better to start from scratch? Probably the best would be to have existing code as a basis to see what exists, get inspiration, but starting from scratch with exactly what I need could be easier for the AI model since the code it will be improving on would be his own thinking to start with.

## Technical Approach

- **Operating System:** The target OS is Windows 10. This is important for mouse/keyboard event handling.
- **Input Listener & Controller:** We will use the `pynput` Python library for capturing global mouse/keyboard events (`pynput.mouse.Listener`, `pynput.keyboard.Listener`) and for simulating keyboard output (`pynput.keyboard.Controller`).
- **Real-time Speech Recognition:** We will start with **Deepgram** using their Python SDK and streaming API for low-latency transcription in both Dictation and Command modes, once activated.
- **Microphone Handling:** Initial implementation will use `PyAudio` to capture microphone input and stream it to Deepgram.
- **Wake Word Detection:** (Future Enhancement) When implemented, the chosen library is **`openWakeWord`** ([GitHub](https://github.com/dscripka/openWakeWord)) due to its performance, customizability, and open-source nature.
- **AI Models:** 
    - An AI model will be used for interpreting user intent in **Command Mode**.
    - A separate, cost-effective AI model can *optionally* be used for post-transcription correction in **Dictation Mode** (configurable).
- **Future SR Options:** Design should allow adding other SR providers (e.g., AssemblyAI, Google Cloud Streaming, offline Whisper/Vosk) as selectable options later.
- **Input Confirmation:** Testing confirmed `pynput` successfully captures all 5 mouse buttons (Left, Right, Middle, X1, X2) and scroll events on the target Windows 10 system.

## Known Issues / Planned Enhancements

- **Initial Transcription Delay:** Audio spoken immediately upon trigger activation may be missed due to connection/processing latency. **Plan:** Implement a short (~2s) rolling audio buffer to capture pre-activation speech.
- **Trigger Button Conflicts:** Default side mouse buttons (X1/X2) conflict with standard application functions (e.g., Back/Forward navigation in Notepad++, Cursor). **Resolution:** Switched default trigger to Middle Mouse Button (Wheel Press). Further refinement (e.g., short vs. long press, modifiers) might be needed.

## Configuration (`config.json`)

The application uses a `config.json` file in the same directory to manage settings. If the file doesn't exist, it will be created with default values upon first run.

- **`general`**:
    - `min_duration_sec`: (Float) Minimum recording duration in seconds required to process the audio (default: 0.5).
    - `selected_language`: (String) The language code for speech recognition (e.g., "en-US", "fr-FR", "es-ES") (default: "en-US").
- **`triggers`**:
    - `dictation_button`: (String) Mouse button for dictation mode ("left", "right", "middle", "x1", "x2") (default: "middle").
    - `command_button`: (String) Mouse button for command mode (same options as dictation) or `null` to disable (default: `null`).
    - `command_modifier`: (String) Keyboard modifier key required with `command_button` ("shift", "ctrl", "alt") or `null` for no modifier (default: `null`).
- **`tooltip`**: Settings for the interim dictation feedback tooltip.
    - `alpha`: (Float) Window transparency (0.0 to 1.0) (default: 0.85).
    - `bg_color`: (String) Background color (Tkinter color name or hex) (default: "lightyellow").
    - `fg_color`: (String) Text color (Tkinter color name or hex) (default: "black").
    - `font_family`: (String) Font name (default: "Arial").
    - `font_size`: (Integer) Font size (default: 10).

*(Note: The Deepgram API key should still be set via a `.env` file or environment variable for security.)*

## Key Features

- **Dual Voice Modes (Multiple Activation Methods):** 
    - **Primary Trigger:** Middle Mouse Button (Wheel Press) - Hold-to-Talk. (Further differentiation TBD).
    - (Future) Wake Word activation.
- **User Configuration:** Highly configurable system via UI (e.g., tray icon menu): Trigger methods (buttons, keys, wake words), specific wake words, SR provider **and language**, optional Dictation AI correction, Command list, cancellation methods, confirmation methods, etc.
- **Real-time Visual Feedback:**
    - **Interim Text Tooltip:** While dictation is active, interim transcription results are displayed in a text tooltip near the cursor (Configurable via `tooltip` section in `config.json`).
    - **Microphone Status/Volume Indicator:** When dictation or command mode is active, a microphone icon appears below the cursor. The fill level of the icon dynamically represents the current input volume level detected by a parallel audio monitoring stream (using `PyAudio`, `numpy`). (Icon appearance currently hardcoded, could be added to config later).

- **Mode 1: Dictation**
    - **Activation:** Hold Dictation trigger (Configurable via `triggers.dictation_button`). Shows text tooltip and status icon.
    - **Recording & Streaming:** Audio streamed to Deepgram (using configured language) while trigger is active (hold) or until confirmation/timeout (wake word).
    - **Real-time Interim Feedback (Tooltip):** While dictation is active, interim transcription results from Deepgram are displayed in a small, temporary tooltip window near the mouse cursor. This provides immediate visual feedback without simulating keyboard input. (Uses `tkinter` for the window and `pyautogui` for cursor position).
    - **Real-time Typing Simulation (Final):** Deepgram's *final* results (after pauses or corrections) are typed at the cursor using `pynput`. Handles Deepgram's real-time corrections via backspace simulation based on word history comparison.
    - **Completion:** Release Dictation trigger OR Say confirmation phrase (e.g., "confirmed") OR pause detection. The interim tooltip is hidden upon completion or when a final result is processed. Hides text tooltip and status icon.
    - ***Optional* AI Correction:** If enabled, final segment sent to AI for review. Corrections highlighted; user can reject; final text updated at cursor.
    - **Filtering:** Minimum duration check.

- **Mode 2: Command**
    - **Activation:** Hold Command trigger (Configurable via `triggers.command_button` and `triggers.command_modifier`). Shows status icon (and potentially command feedback UI later).
    - **Recording & Streaming:** Audio streamed to Deepgram while trigger is active or until confirmation/timeout.
    - **Command Feedback:** Recognized command text displayed in a temporary UI element.
    - **Completion & Confirmation:** Release Command trigger OR Say confirmation phrase (e.g., "confirmed"). Hides status icon.
    - **Cancellation (Before Confirmation):** Allow cancellation via configurable methods: Esc key, mouse gesture (e.g., specific movement), specific voice command (e.g., "cancel").
    - **AI Interpretation:** On confirmation, recognized text sent to AI model to interpret intent.
    - **Action Execution:** Interpreted intent mapped to predefined actions:
        - Simulate keyboard presses/shortcuts (`pynput`).
        - Execute specific, pre-approved scripts/commands (Requires careful security design).
    - **Undo / Go Back (Post-Execution):** Implement a mechanism (triggered by key/mouse/voice, e.g., "go back") to undo the last executed command (requires state tracking and revert logic).
    - **Filtering:** Minimum duration check.

- **System Interaction:** Primarily interacts via simulating keyboard input (`pynput`) or executing defined system actions in command mode.
