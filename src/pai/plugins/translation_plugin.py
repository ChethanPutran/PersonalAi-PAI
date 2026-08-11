from typing import Dict, Any, List
try:
    from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False
from pai.plugins.base_plugin import BasePlugin

class TranslationPlugin(BasePlugin):
    """Real‑time translation using M2M100 or SeamlessM4T."""
    name = "translation"
    
    async def initialize(self) -> None:
        self.model = None
        self.tokenizer = None
        if TRANSFORMERS_AVAILABLE:
            self.model = M2M100ForConditionalGeneration.from_pretrained("facebook/m2m100_418M")
            self.tokenizer = M2M100Tokenizer.from_pretrained("facebook/m2m100_418M")
    
    def get_capabilities(self) -> List[str]:
        return ["translation.translate", "translation.detect_language"]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        if action == "translation.translate":
            return await self._translate(params.get('text'), params.get('target_lang', 'en'))
        elif action == "translation.detect_language":
            return await self._detect_language(params.get('text'))
        raise ValueError(f"Unknown action: {action}")
    
    async def _translate(self, text: str, target_lang: str) -> Dict:
        if self.model and self.tokenizer:
            self.tokenizer.src_lang = "en"  # detect from text in production
            encoded = self.tokenizer(text, return_tensors="pt")
            generated = self.model.generate(**encoded, forced_bos_token_id=self.tokenizer.get_lang_id(target_lang))
            translated = self.tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
            return {"original": text, "translated": translated, "target_lang": target_lang}
        # Fallback mock
        return {"original": text, "translated": f"[Translated to {target_lang}] {text}", "mock": True}
    
    async def _detect_language(self, text: str) -> str:
        # Simple detection (can use langdetect library)
        return "en"