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

## Key Features

- **Dual Voice Modes (Multiple Activation Methods):** Implement two distinct voice input modes, configurable to be activated by:
    - **Hold-to-Talk:** Holding a specific mouse button or key.
    - **Wake Word:** Detecting a specific phrase (e.g., "hey" for Dictation, "command" for Command).
- **User Configuration:** Highly configurable system via UI (e.g., tray icon menu): Trigger methods (buttons, keys, wake words), specific wake words, SR provider, optional Dictation AI correction, Command list, cancellation methods, confirmation methods, etc.

- **Mode 1: Dictation**
    - **Activation:** Hold Dictation trigger OR Say Dictation wake word.
    - **Recording & Streaming:** Audio streamed to Deepgram while trigger is active (hold) or until confirmation/timeout (wake word).
    - **Real-time Typing Simulation:** Deepgram's interim/final results typed at cursor using `pynput`. Handles Deepgram's real-time corrections via backspace simulation.
    - **Completion:** Release Dictation trigger OR Say confirmation phrase (e.g., "confirmed") OR pause detection.
    - ***Optional* AI Correction:** If enabled, final segment sent to AI for review. Corrections highlighted; user can reject; final text updated at cursor.
    - **Filtering:** Minimum duration check.

- **Mode 2: Command**
    - **Activation:** Hold Command trigger OR Say Command wake word.
    - **Recording & Streaming:** Audio streamed to Deepgram while trigger is active or until confirmation/timeout.
    - **Command Feedback:** Recognized command text displayed in a temporary UI element.
    - **Completion & Confirmation:** Release Command trigger OR Say confirmation phrase (e.g., "confirmed").
    - **Cancellation (Before Confirmation):** Allow cancellation via configurable methods: Esc key, mouse gesture (e.g., specific movement), specific voice command (e.g., "cancel").
    - **AI Interpretation:** On confirmation, recognized text sent to AI model to interpret intent.
    - **Action Execution:** Interpreted intent mapped to predefined actions:
        - Simulate keyboard presses/shortcuts (`pynput`).
        - Execute specific, pre-approved scripts/commands (Requires careful security design).
    - **Undo / Go Back (Post-Execution):** Implement a mechanism (triggered by key/mouse/voice, e.g., "go back") to undo the last executed command (requires state tracking and revert logic).
    - **Filtering:** Minimum duration check.

- **System Interaction:** Primarily interacts via simulating keyboard input (`pynput`) or executing defined system actions in command mode.
