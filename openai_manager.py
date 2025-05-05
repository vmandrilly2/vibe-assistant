import asyncio
import logging
from typing import List, Dict, Optional

from openai import AsyncOpenAI, OpenAIError # Use Async client

# Assume constants like API key are handled securely, perhaps via GVM state or env vars
# from constants import OPENAI_API_KEY

logger = logging.getLogger(__name__)

class OpenAIManager:
    """Handles asynchronous interactions with the OpenAI API."""

    def __init__(self, gvm):
        self.gvm = gvm # GlobalVariablesManager instance
        self.client: Optional[AsyncOpenAI] = None
        # TODO: Consider initializing the client more dynamically based on GVM state/config
        # self.init_client()

    async def init_client(self):
        """Initializes the AsyncOpenAI client using API key from GVM/config."""
        api_key = await self.gvm.get("config.openai.api_key")
        if not api_key:
            logger.error("OpenAI API key not found in configuration.")
            self.client = None
            return

        try:
            # Consider adding proxy support if needed, reading proxy URL from GVM/config
            # http_client = httpx.AsyncClient(proxies=...) # if using httpx directly or configuring client
            self.client = AsyncOpenAI(api_key=api_key)
            # Test connection (optional, simple call)
            # await self.client.models.list() # Example call
            logger.info("AsyncOpenAI client initialized successfully.")
        except OpenAIError as e:
            logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
            self.client = None
        except Exception as e:
            logger.error(f"An unexpected error occurred during OpenAI client initialization: {e}", exc_info=True)
            self.client = None

    async def get_translation(self, text: str, source_lang: str, target_lang: str, model: str) -> Optional[str]:
        """Gets translation for the given text."""
        if not self.client:
            logger.warning("OpenAI client not initialized. Attempting to initialize...")
            await self.init_client()
            if not self.client:
                 logger.error("OpenAI client failed to initialize. Cannot get translation.")
                 return None

        logger.info(f"Requesting translation from '{source_lang}' to '{target_lang}' for: '{text[:50]}...' using model '{model}'")
        system_prompt = "You are an expert translation engine."
        user_prompt = f"Translate the following text accurately from {source_lang} to {target_lang}. Output only the translated text:\n\n{text}"

        try:
            # Use keyword arguments as per openai v1.0+ guidelines
            completion = await self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2, # Keep temperature low for accuracy
                max_tokens=int(len(text) * 1.5) + 50 # Estimate based on input length
            )

            translated_text = completion.choices[0].message.content.strip()
            logger.info(f"Translation received: '{translated_text[:50]}...'")
            return translated_text

        except OpenAIError as e:
            logger.error(f"OpenAI API error during translation: {e}", exc_info=True)
            await self.gvm.set(STATE_ERROR_MESSAGE, f"OpenAI Error: {e}") # Update GVM state
            return None
        except Exception as e:
            logger.error(f"Unexpected error during translation request: {e}", exc_info=True)
            await self.gvm.set(STATE_ERROR_MESSAGE, f"Translation Error: {e}")
            return None

    async def get_ai_query_response(self, query: str, model: str) -> Optional[str]:
        """Gets a response to a general AI query."""
        if not self.client:
            logger.warning("OpenAI client not initialized. Attempting to initialize...")
            await self.init_client()
            if not self.client:
                 logger.error("OpenAI client failed to initialize. Cannot get AI query response.")
                 return None

        logger.info(f"Requesting AI query response for: '{query[:50]}...' using model '{model}'")
        # Define appropriate prompts for general queries
        system_prompt = "You are a helpful AI assistant."
        user_prompt = query

        try:
            completion = await self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7, # Allow more creativity for general queries
                max_tokens=200 # Adjust as needed
            )

            response_text = completion.choices[0].message.content.strip()
            logger.info(f"AI Query response received: '{response_text[:50]}...'")
            return response_text

        except OpenAIError as e:
            logger.error(f"OpenAI API error during AI query: {e}", exc_info=True)
            await self.gvm.set(STATE_ERROR_MESSAGE, f"OpenAI Error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during AI query request: {e}", exc_info=True)
            await self.gvm.set(STATE_ERROR_MESSAGE, f"AI Query Error: {e}")
            return None

    async def cleanup(self):
        """Cleans up resources, e.g., close HTTP client if used explicitly."""
        # If using a custom httpx client, close it here:
        # if self.client and hasattr(self.client, '_client') and self.client._client:
        #    await self.client._client.aclose()
        logger.info("OpenAIManager cleaned up.")

# Note: The instantiation and calling of this manager will be handled
# by other modules (like ActionExecutor) or potentially managed by the GVM itself,
# likely triggered by state changes. 