# Blueprint & Task List

This file acts as both the project blueprint and a task manager. It outlines the core requirements and tracks implementation progress.

*   This file SHALL be updated by me or the AI assistant/coworker.
*   It does NOT contain code, but shall include EVERYTHING functional that was included in the code.
*   If the code disappears or needs rebuilding, this file alone shall be enough indication to recreate it completely.

## Purpose

[x] Create a tool to facilitate writing and editing text/code using voice commands and mouse triggers, minimizing keyboard use.
[x] Prioritize mouse actions (especially side buttons, though defaults have changed).

## Core Technical Approach

*   [x] **Target OS:** Windows 10.
*   [x] **Input Listener:** `pynput` for global mouse/keyboard events.
*   [x] **Keyboard Output:** `pynput.keyboard.Controller` for simulating typing.
*   [x] **Real-time Speech Recognition (SR):** Deepgram Python SDK (Streaming API).
*   [x] **Microphone Handling:** `PyAudio` for capturing input.
*   [ ] **AI Model (Commands):** Use an AI model for interpreting user intent in Command Mode.
*   [ ] **AI Model (Dictation Correction - Optional):** Use a cost-effective AI model for post-transcription correction (configurable).
*   [ ] **Future SR Options:** Design to allow adding other SR providers (e.g., AssemblyAI, Google Cloud Streaming, offline Whisper/Vosk).
*   [x] **Input Confirmation:** Verified `pynput` captures all 5 mouse buttons and scroll events on Windows 10.

## Configuration (`config.json`)

*   [x] Use `config.json` for settings.
*   [x] Create default `config.json` if missing.
*   [x] **`general`**:
    *   [x] `min_duration_sec`: (Float) Minimum recording duration (Default: 0.5).
    *   [x] `selected_language`: (String) SR language code (Default: "en-US").
*   [x] **`triggers`**:
    *   [x] `dictation_button`: (String) Mouse button for dictation ("left", "right", "middle", "x1", "x2") (Default: "middle").
    *   [x] `command_button`: (String) Mouse button for command (same options or `null`) (Default: `null`).
    *   [x] `command_modifier`: (String) Keyboard modifier key for command ("shift", "ctrl", "alt", or `null`) (Default: `null`).
*   [x] **`tooltip`**: Settings for interim dictation feedback.
    *   [x] `alpha`: (Float) Transparency (Default: 0.85).
    *   [x] `bg_color`: (String) Background color (Default: "lightyellow").
    *   [x] `fg_color`: (String) Text color (Default: "black").
    *   [x] `font_family`: (String) Font name (Default: "Arial").
    *   [x] `font_size`: (Integer) Font size (Default: 10).
*   [x] **API Key:** Deepgram key handled via `.env` or environment variable.

## Known Issues / Enhancements

*   [x] **Initial Transcription Delay:** Mitigated with a rolling audio buffer (`audio_buffer.py`).
*   [x] **Trigger Button Conflicts:** Default changed from side buttons to Middle Mouse Button due to conflicts. Further refinement may be needed.

## Key Features & Task Breakdown

### Core Functionality

*   [x] **Dual Voice Modes:** Support distinct Dictation and Command modes.
*   [x] **Systray Icon & Menu:**
    *   [x] Create a systray icon (`pystray`).
    *   [x] Build a dynamic menu for configuration.
    *   [x] **General Settings:**
        *   [x] Language selection submenu (dynamic options, checked state).
        *   [ ] Min Duration setting (requires input mechanism).
    *   [x] **Trigger Settings:**
        *   [x] Dictation Button submenu (dynamic options, checked state).
        *   [x] Command Button submenu (dynamic options, checked state).
        *   [x] Command Modifier submenu (dynamic options, checked state).
    *   [ ] **Tooltip Settings:** (Requires input mechanism for changes)
        *   [x] Display current values (Transparency, Background, Text Color, Font, Size).
    *   [x] **Control:**
        *   [x] Reload Config option (signals main app).
        *   [x] Exit option (signals main app).
    *   [x] Save config changes to `config.json`.
    *   [x] Signal main application (`vibe_app.py`) to reload config when changed via systray.
*   [x] **Real-time Visual Feedback:**
    *   [x] **Interim Text Tooltip (Dictation):**
        *   [x] Display interim results near cursor (`tkinter`, `pyautogui`).
        *   [x] Make appearance configurable via `config.json` (`tooltip` section).
        *   [x] Manage tooltip lifecycle (show/hide/update) via a queue (`TooltipManager`).
    *   [x] **Microphone Status/Volume Indicator:**
        *   [x] Display mic icon near cursor when active (`tkinter`).
        *   [x] Dynamically show input volume level via icon fill (`numpy`, `pyaudio`).
        *   [x] Manage indicator lifecycle/updates via a queue (`StatusIndicatorManager`).
        *   [x] Use background audio monitoring (`audio_buffer.py`) for continuous volume level.
        *   [ ] Make icon appearance configurable (currently hardcoded).
*   [x] **Audio Buffering:** Use `BufferedAudioInput` class to continuously capture audio in the background.
    *   [x] Maintain a rolling buffer of recent audio.
    *   [x] Send buffered audio to Deepgram upon activation.
    *   [x] Continuously calculate RMS volume and send to status indicator queue.

### Mode 1: Dictation

*   [x] **Activation:** Hold Dictation trigger (`triggers.dictation_button` from config).
*   [x] **Visual Feedback:** Show text tooltip and status icon on activation.
*   [x] **Recording & Streaming:** Stream audio to Deepgram using the selected language (`general.selected_language`).
*   [x] **Real-time Interim Feedback:** Display interim results in the tooltip.
*   [x] **Real-time Typing Simulation (Final):** Type final results at the cursor (`pynput`).
*   [x] **Correction Handling:** Handle Deepgram's real-time corrections via backspace simulation based on word history.
    *   [x] Implement `handle_dictation_final` logic comparing history and new transcript.
    *   [x] Implement special "back" word handling.
*   [x] **Completion:** Release Dictation trigger.
*   [x] **Cleanup:** Hide text tooltip and status icon on completion.
*   [ ] **Optional AI Correction:** Implement post-processing step with AI review.
    *   [ ] Add configuration flag to enable/disable.
    *   [ ] Send final text to AI.
    *   [ ] Implement UI/mechanism for user to accept/reject AI corrections.
    *   [ ] Update text at cursor based on user decision.
*   [x] **Filtering:** Discard transcription if duration < `general.min_duration_sec`.

### Mode 2: Command (Partially Implemented)

*   [x] **Activation:** Hold Command trigger (`triggers.command_button` + optional `triggers.command_modifier` from config).
*   [x] **Visual Feedback:** Show status icon on activation.
*   [x] **Recording & Streaming:** Stream audio to Deepgram.
*   [ ] **Command Feedback UI:** Display recognized command text in a temporary UI element. (Currently only logs).
*   [x] **Completion:** Release Command trigger.
*   [x] **Cleanup:** Hide status icon on completion.
*   [x] **Cancellation:** Allow cancellation via Esc key during command mode.
*   [ ] **AI Interpretation:** Send recognized text to AI model on completion to interpret intent.
*   [ ] **Action Execution:** Map interpreted intent to actions:
    *   [ ] Simulate keyboard presses/shortcuts (`pynput`). (Basic 'press enter' example exists).
    *   [ ] Execute specific, pre-approved scripts/commands (Requires security design).
*   [ ] **Undo / Go Back:** Implement mechanism to undo the last executed command.
    *   [ ] Track last executed command details.
    *   [ ] Implement revert logic for different action types.
    *   [ ] Define trigger (key/mouse/voice) for undo.
*   [x] **Filtering:** Discard command if duration < `general.min_duration_sec`.

### System Interaction

*   [x] Primarily interact via simulating keyboard input (`pynput`).
*   [ ] Allow executing defined system actions in command mode (requires implementation).
