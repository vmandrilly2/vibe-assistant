import logging
from openai import AsyncOpenAI
import json

class OpenAIManager:
    """Manages interactions with the OpenAI API."""
    def __init__(self, client: AsyncOpenAI):
        """Initializes the manager with an existing AsyncOpenAI client."""
        if not client:
            raise ValueError("AsyncOpenAI client is required for OpenAIManager.")
        self.client = client
        logging.info("OpenAIManager initialized.")

    async def get_openai_completion(
        self,
        model: str,
        messages: list,
        temperature: float,
        max_tokens: int,
        response_format: dict | None = None,
    ) -> str | None:
        """Calls the OpenAI Chat Completions API with the provided parameters.

        Args:
            model: The model to use (e.g., 'gpt-4.1-nano').
            messages: A list of message objects (e.g., [{'role': 'system', 'content': ...}]).
            temperature: Sampling temperature.
            max_tokens: Maximum number of tokens to generate.
            response_format: Optional dictionary for response format (e.g., {'type': 'json_object'}).

        Returns:
            The content of the response message as a string, or None if an error occurs.
        """
        if not self.client:
            logging.error("OpenAI client not available in OpenAIManager.")
            return None

        logging.debug(f"Calling OpenAI API. Model: {model}, Temp: {temperature}, MaxTokens: {max_tokens}, ResponseFormat: {response_format}, Messages: {messages}")

        try:
            # Prepare arguments, excluding response_format if it's None
            api_args = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format is not None:
                api_args["response_format"] = response_format

            response = await self.client.chat.completions.create(**api_args)

            response_content = response.choices[0].message.content
            logging.debug(f"OpenAI Response Raw: {response_content!r}") # Use !r for clarity
            return response_content.strip() if response_content else None

        except Exception as e:
            # Log the error with more details if possible
            error_details = str(e)
            if hasattr(e, 'response') and e.response:
                 try:
                     error_body = await e.response.json()
                     error_details = json.dumps(error_body, indent=2)
                 except: # Fallback if response is not JSON
                    try: error_details = await e.response.text()
                    except: pass

            logging.error(f"Error during OpenAI API call: {type(e).__name__}\nDetails: {error_details}", exc_info=False) # exc_info=False to avoid duplicate trace
            return None

# Example usage (for testing the module directly)
if __name__ == '__main__':
    import asyncio
    import os
    from dotenv import load_dotenv

    async def run_test():
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("Error: OPENAI_API_KEY not found in environment variables or .env file.")
            return

        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
        print("Testing OpenAIManager...")

        try:
            client = AsyncOpenAI(api_key=api_key)
            manager = OpenAIManager(client)

            # --- Test 1: Simple Translation --- >
            print("\n--- Testing Translation ---")
            translate_messages = [
                {"role": "system", "content": "You are an expert translation engine."},{"role": "user", "content": "Translate the following text accurately from English to French. Output only the translated text:\n\nHello world"}
            ]
            translation = await manager.get_openai_completion(
                model="gpt-4o-mini", # Use a known fast/cheap model for testing
                messages=translate_messages,
                temperature=0.2,
                max_tokens=50
            )
            if translation:
                print(f"Translation Result: '{translation}'")
            else:
                print("Translation failed.")

            await asyncio.sleep(1) # Small delay

            # --- Test 2: Command Interpretation (JSON) --- >
            print("\n--- Testing Command Interpretation (JSON) ---")
            command_messages = [
                {"role": "system", "content": "You translate commands to JSON key lists. Example: 'press enter' -> {\"keys\": [\"enter\"]}. Output ONLY JSON."},{"role": "user", "content": "User input: \"press control alt delete\""}
            ]
            interpretation = await manager.get_openai_completion(
                model="gpt-4o-mini", # Use a known fast/cheap model for testing
                messages=command_messages,
                temperature=0.1,
                max_tokens=100,
                response_format={"type": "json_object"}
            )
            if interpretation:
                print(f"Command Interpretation Result: '{interpretation}'")
                try:
                    parsed = json.loads(interpretation)
                    print(f"Parsed JSON: {parsed}")
                except json.JSONDecodeError:
                    print("Failed to parse result as JSON.")
            else:
                print("Command interpretation failed.")

        except Exception as e:
            print(f"An error occurred during testing: {e}")
        finally:
            if 'client' in locals() and client:
                await client.close()
                print("\nOpenAI client closed.")

    # Run the async test function
    asyncio.run(run_test()) 