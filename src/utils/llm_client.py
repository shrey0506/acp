import json, os, re
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ACP")
load_dotenv()

class LiteLLMClient:
    def __init__(self):
        self.model = os.getenv("LITELLM_MODEL", "gemini/gemini-2.5-flash-lite")
        self.enabled = bool(os.getenv("GEMINI_API_KEY"))
        if self.enabled: import litellm; self.litellm = litellm

    def chat(self, messages, json_mode=False):
        if not self.enabled: return {"action": "CHAT", "msg_to_user": "Fallback enabled"}
        
        kwargs = {"model": self.model, "messages": messages, "temperature": 0.1}
        try:
            res = self.litellm.completion(**kwargs).choices[0].message.content
            if not json_mode: return res
            
            # Robust JSON extraction
            match = re.search(r'\{.*\}', res.replace('\n', ' '), re.DOTALL)
            if match: return json.loads(match.group(0))
            return json.loads(res)
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return {"action": "CHAT", "msg_to_user": "Processing error, please try again."}

llm_client = LiteLLMClient()
