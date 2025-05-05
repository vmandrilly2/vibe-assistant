import asyncio
import logging
from typing import List, Dict, Optional

from openai import AsyncOpenAI, OpenAIError # Use Async client

# Assume constants like API key are handled securely, perhaps via GVM state or env vars
# from constants import OPENAI_API_KEY

logger = logging.getLogger(__name__)

class OpenAIManager:
    """Manages interactions with the OpenAI API."""
    def __init__(self, openai_client: AsyncOpenAI):
        """Initializes the manager with an existing AsyncOpenAI client."""
        # self.gvm = gvm # No longer needs GVM directly if client is passed
        # self.api_key = None # Key is managed by the client
        if not openai_client:
            raise ValueError("AsyncOpenAI client is required for OpenAIManager.")
        self.client = openai_client
        logger.info("OpenAIManager initialized.")

    async def init(self):
        """(Re)Load config or keys if needed (currently not required)."""
        # Removed key loading logic as client is pre-configured
        # self.api_key = await self.gvm.get("config.openai.api_key")
        # if not self.api_key or self.api_key == "YOUR_OPENAI_API_KEY":
        #     logger.error("OpenAI API key not found in GVM configuration (config.openai.api_key).")
        #     self.api_key = None
        #     # Fail init or allow operation without key?
        #     return False
        # self.client = AsyncOpenAI(api_key=self.api_key)
        logger.info("OpenAIManager initialized (no specific init actions needed).")
        return True

    async def get_translation(self, text: str, target_language: str, source_language: str = "English") -> Optional[str]:
        """Gets a translation from OpenAI."""
        if not self.client:
            logger.error("OpenAI client not available.")
            return None
        # model = await self.gvm.get("config.translation.model", "gpt-4o-mini") # Get model from GVM
        model = "gpt-4o-mini" # Hardcode for now, or make configurable without GVM

        messages = [
            {"role": "system", "content": f"You are a translation engine. Translate the following text accurately from {source_language} to {target_language}. Output only the translated text, without any introductory phrases, explanations, or quotation marks."},
            {"role": "user", "content": text}
        ]

        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=int(len(text) * 2.5) + 50 # Estimate max tokens needed
            )
            translated_text = response.choices[0].message.content
            logger.debug(f"OpenAI Translation raw response: {translated_text!r}")
            return translated_text.strip() if translated_text else None
        except Exception as e:
            logging.error(f"Error during OpenAI translation: {e}", exc_info=True)
            return None

    async def get_ai_query_response(self, prompt: str) -> Optional[str]:
        """Gets a general query response from OpenAI."""
        if not self.client:
            logger.error("OpenAI client not available.")
            return None
        # model = await self.gvm.get("config.general.openai_model", "gpt-4o-mini") # Get model from GVM
        model = "gpt-4o-mini" # Hardcode for now

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ]

        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=150 # Adjust as needed
            )
            ai_response = response.choices[0].message.content
            logger.debug(f"OpenAI Query raw response: {ai_response!r}")
            return ai_response.strip() if ai_response else None
        except Exception as e:
            logging.error(f"Error during OpenAI query: {e}", exc_info=True)
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