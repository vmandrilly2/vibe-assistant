# Target System Design (Refactored for Async Declarative Modularity)

## Introduction

This document outlines a refactored system design based on declarative module management using Python's `asyncio` framework. The goal is an extremely minimal orchestrator responsible only for managing module lifecycles based on configuration, while all functional logic resides within independent, concurrently running, toggleable modules.

## Core Principles

1.  **Declarative Module Definition:** The application is composed of a defined list of core modules.
2.  **State-Driven Async Lifecycle Management:** The orchestrator tracks the desired state (on/off from config) vs. the current state for each module. Based on state changes, it calls standard **async** lifecycle methods (`init`, `cleanup`) and manages the primary execution logic of active modules via `asyncio.Task`.
3.  **Minimal Async Orchestrator:** `vibe_app.py`'s main `async` function initializes components, manages module state transitions (including starting/cancelling tasks), and facilitates message passing via central `asyncio.Queue`s. It avoids implementing module-specific domain logic.
4.  **Independent Async Modules:** Modules encapsulate specific functionalities. They implement standard `async` lifecycle methods (`async def init`, `async def cleanup`) and a core concurrent execution method (e.g., `async def run_loop`). They interact via orchestrator-managed `asyncio.Queue`s using `await queue.put()` and `await queue.get()`.
5.  **Configuration-Driven:** `config.json` dictates the desired state (enabled/disabled) of each module.
6.  **Async Queue-Based Communication:** Modules communicate asynchronously by putting message objects onto central input/output `asyncio.Queue`s managed by the orchestrator. The orchestrator routes messages efficiently without blocking.

## Proposed Components & Responsibilities

*(Modules listed below implement standard async methods like `async def init(config, queues)`, `async def run_loop()`, `async def cleanup()`, `async def handle_message(message)`)*

### 1. Orchestrator (`vibe_app.py`)

*   **Data:** `modules` (list of module instances), `module_states` (dict mapping module_name -> {'current_state': 'active'/'inactive', 'desired_state': 'active'/'inactive', 'task': asyncio.Task | None}), `input_queue`, `output_queue`.
*   **Responsibilities (within `async def main()`):**
    *   Load initial config (`ConfigManager`).
    *   Instantiate all potential modules, passing config and queues.
    *   Determine initial `desired_state` for all modules.
    *   **Main Async Logic:**
        *   Start a primary task to monitor and handle config reloads/shutdown events.
        *   Start a primary task to process messages from the central `output_queue` and route them async to module `handle_message` methods.
        *   Start a primary task to handle basic input listener events (from `InputManager`), translating them into messages (e.g., `start_stt`, `stop_stt`) put onto the `output_queue` for routing.
        *   Run an initial state synchronization loop: For each module, if `desired_state` is 'active', call `await module.init()` and start its main task: `task = asyncio.create_task(module.run_loop()); module_states[name]['task'] = task`. Update `current_state`.
        *   The main orchestrator task then primarily waits for shutdown or handles config reloads.
        *   **Config Reload Handling:**
            *   Update `desired_state` for all modules.
            *   Iterate through modules:
                *   If state change inactive -> active: `await module.init()`, create and store `asyncio.Task` for `module.run_loop()`, update `current_state`.
                *   If state change active -> inactive: Retrieve task, signal it to stop (e.g., `task.cancel()` or `module.stop_event.set()`), `await asyncio.wait_for(task, timeout)`, call `await module.cleanup()`, update `current_state`, clear task reference.
    *   **Shutdown:** Gracefully cancel/stop all active module tasks and call `cleanup`.
*   **Key Change:** Event-driven (`asyncio`). Manages module tasks rather than calling updates in a loop. Focuses on state transitions and message routing.

### 2. Configuration (`config_manager.py`)

*   **Status:** Unchanged.

### 3. Input (`InputManager` - Separate Module)

*   **Responsibilities:** Encapsulate `pynput` listeners (run in separate thread or carefully integrated with `asyncio`). Put structured input event messages onto the central `output_queue`.
*   **Status:** Logic moved out of `vibe_app.py`. Needs careful implementation to bridge threads and `asyncio` if necessary (e.g., using `loop.call_soon_threadsafe`).

### 4. Audio Input (`BackgroundAudioRecorder`)

*   **Responsibilities:** Capture/buffer audio. Lifecycle managed by orchestrator. Methods likely called via async messages.
*   **Status:** Unchanged functionality, lifecycle managed via `init`/`cleanup`. Toggleable.

### 5. STT (`STTManager`)

*   **Responsibilities:** Manage `STTConnectionHandler` instances (potentially as sub-tasks). Receive start/stop commands via `handle_message`. Put raw transcript messages onto the `output_queue`.
*   **Status:** Core logic likely runs in its `run_loop` task managed by orchestrator.

### 6. Processing Pipeline Modules (Independent Async Modules)

*   **A. Text Normalization (`TextNormalizer`)**
    *   **Responsibilities:** `run_loop` awaits `normalized_text_input_queue`. Processes text. `await output_queue.put(...)`.
    *   **Toggleable:** Yes. Orchestrator skips routing messages if inactive.
*   **B. Action Detection (`ActionDetector`)**
    *   **Responsibilities:** `run_loop` awaits `action_detect_input_queue`. If enabled internally (config check), detects keywords. `await output_queue.put(...)` with structured result (`text`, `action`).
    *   **Toggleable:** Yes.
*   **C. Action Dispatcher (Logic within Orchestrator's Output Queue Handler)**
    *   **Responsibilities:** Handles `processed_dictation` messages async. Checks config, sends messages to `KeyboardSimulator` or `ActionConfirmManager` async.

### 7. Action Confirmation UI (`ActionConfirmManager`)

*   **Responsibilities:** `run_loop` likely manages the Tkinter thread safely (using queues or `loop.call_soon_threadsafe`). Receives "show" via `handle_message`. Puts result messages on `output_queue`.
*   **Toggleable:** Yes.

### 8. Action Execution (`KeyboardSimulator`)

*   **Responsibilities:** Receives "type_text" / "execute_action" via `handle_message`. Performs sync keyboard actions (potentially needs `run_in_executor` if actions are slow and called from async orchestrator).
*   **Status:** Service module.

### 9. Translation (`TranslationManager`)

*   **Responsibilities:** Receives "translate_request" via `handle_message`. Calls `OpenAIManager` (async). Puts "type_text" message on `output_queue`.
*   **Toggleable:** Yes.

### 10. UI Feedback Modules (`TooltipManager`, `MicUIManager`, etc.)

*   **Responsibilities:** Receive state update messages via `handle_message`. Manage Tkinter threads safely. Put UI interaction messages on `output_queue`.
*   **Toggleable:** Yes.

## Key Benefits of Target Async Design

*   **Efficiency & Responsiveness:** Leverages `asyncio` for non-blocking I/O, keeping the application responsive. CPU is not wasted polling inactive modules.
*   **Improved Modularity & Concurrency:** Modules run as independent, concurrent tasks managed by the orchestrator.
*   **Clearer Lifecycles:** Explicit `init`, `run_loop` (managed via `Task`), and `cleanup` phases for modules.
*   **Predictable Toggling:** Enabling/disabling modules involves starting/stopping their associated `asyncio.Task` and running init/cleanup.
*   **Scalability:** Easier to manage potentially many concurrent operations (like multiple STT sessions, UI updates) without performance degradation typical of polling. 