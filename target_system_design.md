# Target System Design (GVM-Driven Async Modularity)

## Introduction

This document outlines a refactored system design where a central `GlobalVariablesManager` (GVM) acts as both the shared state repository and the primary engine driving the application's logic and module lifecycles. The goal is maximum decentralization of specific logic into modules, with the GVM coordinating their activity based on state changes.

## Core Principles

1.  **Central GVM Engine:** The `GlobalVariablesManager` is the core. It holds state, manages module lifecycles based on config state within itself, and **runs reactive logic** to trigger actions/UI based on specific state changes.
2.  **State-Driven Lifecycle:** Modules are activated/deactivated (`init`/`cleanup`/task start/stop) by the `GVM` when relevant configuration flags *within the GVM's state* change.
3.  **Minimal Bootstrap:** A minimal entry point (e.g., `main.py`) initializes the GVM, tells it to load the config, and starts the GVM's primary execution/monitoring loop.
4.  **Independent Async Modules:** Modules encapsulate specific functionalities. They implement standard `async` lifecycle methods (`async def init`, `async def cleanup`) and a core concurrent execution method (`async def run_loop`). They interact *exclusively* by reading from, writing to, and `await`ing state changes within the `GlobalVariablesManager`.
5.  **Configuration-Driven:** `config.json` dictates the desired state, loaded into and managed by the GVM.
6.  **Synchronization & Reactivity:** The `GlobalVariablesManager` implements locking for safe state access and event/condition mechanisms for efficient asynchronous waiting by modules. It also contains internal logic to react to specific state changes.

## Proposed Components & Responsibilities

*(Modules listed below interact primarily via `await global_variables_manager.get(key)`, `await global_variables_manager.set(key, value)`, and `await global_variables_manager.wait_for_change(key)`)*

### 1. GlobalVariablesManager (Central Engine & State)

*   **Responsibilities:**
    *   Store the application's shared state (config, input status, session data, module statuses, etc.).
    *   Provide thread-safe/task-safe async methods (`get`, `set`, `wait_for_change`/`wait_for_value`).
    *   Implement internal locking (`asyncio.Lock`) and reactivity mechanisms (`asyncio.Event`/`Condition`).
    *   Load initial configuration into its state.
    *   Monitor its own state for configuration changes affecting module activation.
    *   Manage Module Lifecycles based on config flags in its state.
    *   **Run Reactive Logic:** Monitor specific state keys and trigger actions:
        *   Monitor `sessions.{id}.recognized_actions`: If list is not empty, trigger `ActionUIManager` to show (via setting `ui.confirmation.request` state).
        *   Monitor `ui.confirmation.confirmed_actions`: When actions appear, trigger `KeyboardSimulator` (via setting `output.action_queue` state).
    *   Manage the lifecycle of its UI sub-module.
*   **Sub-Modules:**
    *   `GlobalVariablesManager.UI`

#### 1.a. GlobalVariablesManager.UI (Sub-Module)

*   **Purpose:** Display real-time GVM state.
*   **Responsibilities:** (Lifecycle managed by GVM)
    *   Display window top-left, allow closing.
    *   Periodically read state from parent GVM.
    *   Perform optimized rendering.
*   **Toggleable:** Yes (`modules.global_variables_ui_enabled`).

### 2. Configuration (`config_manager.py`)

*   **Responsibilities:** Read/write config file. Provides method for GVM to load/reload config into its state.

### 3. Input (`InputManager`)

*   **Responsibilities:** (Lifecycle managed by GVM)
    *   Read target trigger keys from GVM state.
    *   Listen for physical key events.
    *   Update `input.dictation_key_pressed` state in GVM.
*   **Toggleable:** Yes.

### 4. Audio Input (`BackgroundAudioRecorder`)

*   **Responsibilities:** (Lifecycle managed by GVM based on `config.modules.audio_buffer_enabled`)
    *   `await`s `input.dictation_key_pressed == True` via GVM.
    *   Starts capture, writes chunks to GVM state (`audio.current_chunks`).
    *   `await`s `input.dictation_key_pressed == False` via GVM.
    *   Stops capture, cleans up chunks in GVM state.
*   **Toggleable:** Yes.

### 5. STT (`STTManager`)

*   **Responsibilities:** (Lifecycle managed by GVM)
    *   `await`s `input.dictation_key_pressed == True`.
    *   Initiates connection attempt, handles retries (optional), updates status state (`stt.session.{id}.status`).
    *   Upon connection: Sends buffer (optional), streams new audio (reading from GVM).
    *   Handles incoming messages:
        *   **Interim Results:** Updates `sessions.{id}.interim_transcript` state.
        *   **Final (Speech Final) Results:** Updates `sessions.{id}.final_transcript_segment` state.
    *   `await`s `input.dictation_key_pressed == False`.
    *   When stop trigger detected:
        *   Send end-of-stream signal.
        *   **(Optional) Final Result Retrieval:** Wait for final result, update `sessions.{session_id}.final_transcript_full` state.
        *   Close connection, update status state.
*   **Toggleable:** Yes.

### 6. Dictation Text Manager (`DictationTextManager`)

*   **Responsibilities:** (Lifecycle managed by GVM)
    *   `await`s changes to confirmed text states (`sessions.{id}.final_transcript_segment`, `sessions.{id}.final_transcript_full`).
    *   Takes the latest confirmed text.
    *   If `ActionDetector` sub-module is enabled, passes text to it; otherwise processes text directly.
    *   Writes the final text-to-be-typed to the GVM (e.g., `output.typing_queue`).
*   **Sub-Modules:**
    *   `ActionDetector` (Optional)
*   **Toggleable:** Likely core functionality, but contains toggleable sub-module.

#### 6.a Action Detector (Sub-Module of `DictationTextManager`)

*   **Input:** Confirmed text segment/string.
*   **Responsibilities:**
    *   Check if enabled (`config.modules.action_detection_enabled`).
    *   If enabled:
        *   Look for commands (keywords) from the i18n dictionary.
        *   If command found: **Remove command from text**, update GVM state `sessions.{id}.recognized_actions` list with the detected action(s).
        *   Return the (potentially modified) text.
    *   If disabled: Return text unmodified.
*   **Output:** Text string (modified or original).
*   **Toggleable:** Yes (`config.modules.action_detection_enabled`).

### 7. Action Confirmation UI (`ActionUIManager` - Replaces `ActionConfirmManager`)

*   **Responsibilities:** (Lifecycle managed by GVM based on `config.modules.action_confirm_enabled`)
    *   `await`s changes to `ui.confirmation.request` state (set by GVM's reactive logic).
    *   Manages Tkinter thread/window.
    *   Displays UI window (for ~3 seconds).
    *   Shows list of recent actions from GVM state (`sessions.{id}.recognized_actions` - up to last 5 different actions).
    *   On hover over an action button: Update GVM state `ui.confirmation.confirmed_actions` list with the hovered action.
    *   Hides UI after timeout or interaction.
*   **Toggleable:** Yes.

### 8. Action Execution (`ActionExecutor` - Replaces KeyboardSimulator section)

*   **Responsibilities:** (Lifecycle managed by GVM)
    *   `await`s changes to the confirmed actions state in GVM (e.g., `ui.confirmation.confirmed_actions` or `output.action_queue`).
    *   Processes the list of confirmed actions sequentially.
    *   For each action:
        *   **Determine Action Type:** Analyze the action command (e.g., "Enter", "Escape", "delete", "cancel", "Translate to ...", "ask ai ...", custom single chars).
        *   **Dispatch Execution based on Type:**
            *   **Simple Key Press:** (e.g., "Enter", "Escape", single chars) - Use `KeyboardSimulator` to press/release the corresponding key.
            *   **Text Typing:** (If an action results in text) - Use `KeyboardSimulator` to type the text.
            *   **Deletion:** (e.g., "delete", "cancel")
                *   Requires access to the relevant dictation history state in GVM (e.g., `sessions.{id}.history`).
                *   Calculate the necessary number of backspaces (for last word or whole segment).
                *   Use `KeyboardSimulator` to send the backspace sequence.
                *   Update the history state in GVM accordingly.
            *   **AI Translation:** (e.g., "Translate to [language]")
                *   Extract target language.
                *   Read the relevant final text from GVM state (`sessions.{id}.final_transcript_segment` or similar).
                *   **Call `OpenAIManager` (or similar AI interface) via GVM state/direct call** to perform translation, passing the text and target language.
                *   **(Response Handling):** Place the translated text result into the GVM state (e.g., `output.typing_queue`) for the `KeyboardSimulator` part of `ActionExecutor` to type.
            *   **AI Query:** (e.g., command is detected as "ask ai")
                *   Read the *entire* confirmed text segment from GVM state that contained the "ask ai" command (e.g., `sessions.{id}.final_transcript_segment`).
                *   **Call `OpenAIManager` (or similar AI interface) via GVM state/direct call**, passing the full text segment as the query.
                *   Handle the response: Update a specific GVM state variable (e.g., `ui.ai_response_display`) intended for a dedicated UI element to show the answer (does not type the answer by default).
*   **Dependencies:**
    *   `KeyboardSimulator` (for executing key presses/typing).
    *   `GlobalVariablesManager` (for reading actions, history, config; writing results/state).
    *   **`OpenAIManager` (or similar AI interface)** (for translation and queries).
*   **Toggleable:** Likely core functionality, but specific action *types* could potentially be disabled via config if needed in the future.

### 9. KeyboardSimulator (Utility Component)

*   **Responsibilities:** Provides low-level methods to simulate key presses, releases, and typing sequences. Used by `ActionExecutor`.
*   **Status:** Simple utility, likely initialized by GVM and passed to `ActionExecutor` or accessed via GVM state.

### 10. Interim Text UI (`InterimTextUIManager` - Replaces `TooltipManager`)

*   **Responsibilities:** (Lifecycle managed by GVM based on `config.modules.interim_text_ui_enabled`)
    *   `await`s changes to `sessions.{id}.interim_transcript` state.
    *   Manages Tkinter tooltip window.
    *   Updates tooltip display content.
*   **Toggleable:** Yes (`config.modules.interim_text_ui_enabled`).

### 11. Other UI Feedback Modules (`MicUIManager`, `SessionMonitorUI`, `SystrayUI`)

*   **Responsibilities:** (Lifecycles managed by GVM based on config)
    *   `await` changes to relevant GVM state keys.
    *   Update Tkinter UI.
    *   Write UI interaction results (e.g., language change) back to GVM state.
*   **Toggleable:** Yes.

## Key Benefits of GVM-Driven Design

*   **Centralized Control & State:** Single source of truth and coordination.
*   **Efficiency & Responsiveness:** `asyncio` prevents blocking; reactivity avoids polling.
*   **Maximum Decoupling:** Modules interact only with GVM state.
*   **Simplified Bootstrap:** Entry point (`main.py`) becomes very simple.

## Challenges

*   **GVM Complexity:** The `GlobalVariablesManager` becomes highly complex, containing state storage, locking, reactivity, *and* module lifecycle logic. This concentration of responsibility needs careful implementation.
*   **Potential Bottlenecks:** Heavy reliance on GVM's internal lock for state updates could be a bottleneck.
*   **Debugging:** Tracing logic flow through state changes in the central GVM can be challenging.
*   **Testability:** Mocking the complex GVM for module testing might be difficult. 