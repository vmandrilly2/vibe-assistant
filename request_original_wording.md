# Original User Requests for Module Functionality (More Comprehensive)

This file contains excerpts from the user's messages defining the core functionality of various modules and the overall architecture during the system design discussion.

## Overall Architecture Philosophy

> You think too much, without focusing on the most important: defining separate functionalities and subfunctionalities, and make sure they have their own independant code, make sure they could turned on/off. The orchestrator should have the minimum code. You should analyse the whole app, not just a specific functionality.

> The propose current road seems overly complex.
> main code should be:
> For each module in availlable module:
> If module was switch on since last time
>    run module initialization code
>    set module ressources as availlable
> else if module was switched off
>   run module cleanup code
>   set module ressources as unavaillable
> else if module is on,
>    Run module code.
> We don't need anything else for main file.
> Then in each module we have a similar structure: [...]

> Remove orchestrator wording from document. and forget about vibe_app. The GVM is going to be what gets everything done

## Global Variables Manager (GVM) Concept

> The global variables manager is the shared memory database. Each module can access it to get the info it needs, and each module can update it, which is basically the essence of their job.

> The global Variable Manager will have a submodule: globalVariableManager.UI which will display the real time (update every 0.5s) values of all global variables it manages. 
> This UI is a window in top left corner that I can close.
> And this time don't forget optimization: only labels with values that have changed needs to be updated.

## Global Variables Manager UI

> The global Variable Manager will have a submodule: globalVariableManager.UI which will display the real time (update every 0.5s) values of all global variables it manages.
> This UI is a window in top left corner that I can close.
> And this time don't forget optimization: only labels with values that have changed needs to be updated.

## Interaction Mechanism (Await State Change / Event Driven)

> Can we await instead for a GlobalVariable in the global variable manager to equal some value? Like the event of pressing a key would trigger the Input manager to check key pressed and update the global variable, which change to the required value would trigger the stt to do something accordingly?

## Input Manager

> The input manager job should be to contact the config manager to get the key or key combinaison to watch for and check if it is being pressed or unpressed, and updates the global dictation key pressed variable in the system global variables manager [...]

*(Initial request mentioned ConfigManager, later shifted to GVM holding config)*

## Audio Input (BackgroundAudioRecorder)

> Ok. Audio Input: Records audio, stores the audio chunks in the GVM. Is triggered by the GVM to start upon button key pressed variable = switch to true and stops upon button release (switch to false). Audio chunks in the GVM are cleaned up upon this button release

## STT (STTManager)

> STT:
> 1- tries to connect to deepgram stream.
> optional submodule 1: deepgram connection retries upon failure manager.
> optional submodule 2: send audio chunks collected so far by the GVM (="audio buffer")
> 2- upon connection, steams all new incoming chunks
> 3- receives the interim data, updates the interim data in GVM
> 4- receives confirmed data, updates the completions data in GVM
> 5- inform the end of data to deepgram,
> optional submodule: retrieves with a timeout the final dictation result, updates the completions data in GVM with the final confirmed data

> [Regarding stopping] "awaits input.dictation_key_pressed == False to know when to stop session." when did I say that?
> [...] It's clear on point 5: it informs the end of data, that is when the button is released. But then there are more tasks! The STT stops once its final task is completed (retrieval of the final confirmed data.

## Dictation Text Manager & Action Detector Submodule

> remove Text Normalization. We have a dictation text manager module: this will use the received confirmed text (it doesn't matter if it's the confirmed text during TTS or the final confirmed result) to perform the following:
> optional Dictation submodule 1: takes the STT confirmed text and look for commands from the current language dictionay. When there is a match, removes the command text from the dictation text, add the recognized command in the GVM, When there are commands in the GVM, the GVM will show the action UI manager for 3 seconds, with the list of the last 5 recognized actions (5 slots availlable instead of just one), hovering an action will add the action in the GVM confirmed actions requested list. The GVM will then trigger the related actions to be performed by the corresponding tool (eg: typing a key)

## Action Confirmation UI (Implicit from Dictation Text Manager)

> [...] When there are commands in the GVM, the GVM will show the action UI manager for 3 seconds, with the list of the last 5 recognized actions (5 slots availlable instead of just one), hovering an action will add the action in the GVM confirmed actions requested list. [...]

## Action Executor (Types)

> Action Execution. There are several different Actions types that can be taken, depending on the command.
> The Action Execution will take the list of actions to implement, first determine the action type, and according to the type, perform one of the following actions:
> - type a key or series of keys with the keyboad simulator.
> - type a series of backspace keys, deleting words ("delete" command) or the whole sentence ("cancel" command), using the current typed word history disctionary/diff mechanism.
> - send to AI model using the OpenAI module to perfom translation in the types language or the default selected target language if not availlable ("Translate to ..." command) or to get an answer ("ask ai" command) simply displaying the answer.

> [Regarding "ask ai"] No, for now the query IS the text that we are processing (same thing that for the translation):

## Translation (Relationship to Action Execution)

> Translation (TranslationManager) [...] This is not a module. It's now a submodule of action execution

## Interim Text UI (Tooltip Manager)

> interim text manager module (tooltip manager, optional): This directly updates the tooltip with the new interim text when it changes. 