from __future__ import annotations

import logging
from typing import Any

try:
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
except ImportError:
    genai = None

logger = logging.getLogger(__name__)

class GeminiClient:
    def __init__(self, api_key: str | None, model_name: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.model = None
        
        if genai and api_key:
            try:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel(
                    model_name=model_name,
                    safety_settings={
                        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    }
                )
                logger.info(f"Gemini client initialized with model: {model_name}")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client: {e}")

    def is_available(self) -> bool:
        return self.model is not None

    def chat(self, system_instruction: str, user_prompt: str) -> str:
        if not self.is_available():
            return "Gemini API is not configured or available."

        try:
            # For simplicity, we use a single turn chat with system instruction prepended
            # In a more advanced version, we could use start_chat()
            full_prompt = f"SYSTEM: {system_instruction}\n\nUSER: {user_prompt}"
            response = self.model.generate_content(full_prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini chat error: {e}")
            return f"Error communicating with Gemini: {e}"
