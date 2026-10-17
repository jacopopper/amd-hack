import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from step_wise.config import Settings
from step_wise.models import AssessmentOutput
from step_wise.pipeline.gemma import complete


class GemmaEndpointTests(unittest.TestCase):
    def test_non_hf_endpoint_uses_its_key_exact_model_and_full_path(self):
        settings = Settings(gemma_base_url="https://amd.invalid/spaces/example/8000/v1",
                            gemma_model="Gemma-4-31B-IT", gemma_provider="amd",
                            gemma_api_key="synthetic-amd-secret", hf_token="synthetic-hf-secret")
        response = {"model": settings.gemma_model, "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"hazards": [], "coverage": [], "follow_up_requests": []})}}]}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("step_wise.pipeline.gemma.post_chat_streaming", return_value=response) as post:
                complete(settings, AssessmentOutput, "prompt", "content", audit_directory=Path(tmp))
            self.assertEqual(post.call_args.args[0], settings.gemma_base_url + "/chat/completions")
            self.assertEqual(post.call_args.args[1]["model"], "Gemma-4-31B-IT")
            self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer synthetic-amd-secret"})
            audit = "".join(path.read_text() for path in Path(tmp).glob("*.json"))
            self.assertNotIn(settings.gemma_api_key, audit)
            self.assertNotIn(settings.hf_token, audit)


if __name__ == "__main__":
    unittest.main()
