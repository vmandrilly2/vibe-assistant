import asyncio
import time
import logging
import signal
import platform
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Logging Setup ---
log_file = "vibe_app.log"
log_format = '%(asctime)s %(levelname)s: [%(name)s] %(message)s'
logging.basicConfig(level=logging.DEBUG,
                    format=log_format,
                    handlers=[
                        logging.FileHandler(log_file, mode='w'),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger(__name__)
# ---------------------

# --- Restore Imports ---
from global_variables_manager import GlobalVariablesManager
from config_manager import ConfigManager
# ----------------------

print("--- Script Start ---")

# --- Restore Instantiation ---
config_manager = ConfigManager("config.json")
gvm = GlobalVariablesManager(config_manager) # Restore GVM instantiation
# --------------------------

# --- Restore main async function ---
global gvm_ref # Use a global ref for signal handler (or pass gvm differently)
gvm_ref = gvm

async def main():
    logger.info("Starting Vibe Assistant application (Restored main)...")
    # GVM already instantiated globally for this structure
    logger.info("Calling simplified gvm.run()...")
    await gvm_ref.run() # Call run on the global instance
    logger.info("Simplified gvm.run() completed.")
# --- End Restore main async function ---

# --- Remove minimal_main ---
# async def minimal_main():
#     logger.info("--- minimal_main started ---") # Use logger
#     await asyncio.sleep(1) # Simulate some async work
#     logger.info("--- minimal_main finished ---") # Use logger
# --------------------------

# --- Restore Signal Handling and Shutdown ---
# async def shutdown(sig, loop): # Commenting out unused shutdown function
#     logger.info(f"Received exit signal {sig.name}...")
#     if gvm_ref:
#         gvm_ref.request_shutdown()
#     # ... rest of shutdown logic from original ...
#     tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
#     if tasks:
#         logger.info(f"Cancelling {len(tasks)} outstanding tasks...")
#         [task.cancel() for task in tasks]
#         await asyncio.gather(*tasks, return_exceptions=True)
#         logger.info("Outstanding tasks cancelled.")
# -------------------------------------------

if __name__ == "__main__":
    logger.info(f"""=== Application Start ===""")
    # print("--- Running asyncio.run(minimal_main)... ---")
    # start_time = time.time()
    try:
        # Call the restored main function
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught in __main__, exiting process.")
    except Exception as e:
        logger.critical(f"Unhandled exception in __main__: {e}", exc_info=True)
    finally:
        logger.info(f"""=== Application End ===""")
    # end_time = time.time()
    # print(f"--- asyncio.run completed (duration: {end_time - start_time:.2f}s) ---")

# print("--- Script End ---")
