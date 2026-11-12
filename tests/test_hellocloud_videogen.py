import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import __init__ as plugin


class HelloCloudVideoGenProviderTest(unittest.TestCase):
    @patch.object(plugin, "save_bytes_video", return_value=Path("/tmp/hellocloud-test.mp4"))
    @patch("openai.OpenAI")
    @patch.object(plugin.HelloCloudVideoGenProvider, "_api_key", return_value="hello_test")
    def test_text_to_video_uses_hellocloud_payload_and_downloads_result(
        self, _api_key, openai_class, _save_bytes_video
    ):
        client = openai_class.return_value
        client.videos.create.return_value = SimpleNamespace(
            id="vid_123", status="completed", error=None, expires_at=1234567890
        )
        client.videos.download_content.return_value.read.return_value = b"mp4-bytes"

        result = plugin.HelloCloudVideoGenProvider().generate(
            "A dolphin swimming in Mutsu Bay", duration=3, audio=False
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["video"], "/tmp/hellocloud-test.mp4")
        self.assertEqual(result["duration"], 5)
        request = client.videos.create.call_args.kwargs
        self.assertEqual(request["model"], "minimax-h3")
        self.assertEqual(request["seconds"], "5")
        self.assertEqual(
            request["extra_body"],
            {"aspect_ratio": "16:9", "generate_audio": False},
        )
        self.assertNotIn("size", request)
        client.videos.download_content.assert_called_once_with("vid_123")

    @patch.object(plugin, "save_bytes_video", return_value=Path("/tmp/hellocloud-test.mp4"))
    @patch("openai.OpenAI")
    @patch.object(plugin.HelloCloudVideoGenProvider, "_api_key", return_value="hello_test")
    def test_image_inputs_use_hellocloud_reference_fields(
        self, _api_key, openai_class, _save_bytes_video
    ):
        client = openai_class.return_value
        client.videos.create.return_value = SimpleNamespace(
            id="vid_123", status="completed", error=None, expires_at=None
        )
        client.videos.download_content.return_value.read.return_value = b"mp4-bytes"

        result = plugin.HelloCloudVideoGenProvider().generate(
            "Animate these images",
            image_url="https://example.com/first.png",
            reference_image_urls=["https://example.com/second.png"],
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["modality"], "image")
        extra_body = client.videos.create.call_args.kwargs["extra_body"]
        self.assertEqual(extra_body["input_reference"], "https://example.com/first.png")
        self.assertEqual(
            extra_body["input_references"],
            [{"type": "image", "url": "https://example.com/second.png"}],
        )

    @patch.object(plugin, "save_bytes_video", return_value=Path("/tmp/hellocloud-test.mp4"))
    @patch("openai.OpenAI")
    @patch.object(plugin.HelloCloudVideoGenProvider, "_api_key", return_value="hello_test")
    def test_local_and_data_image_inputs_are_uploaded_as_files(
        self, _api_key, openai_class, _save_bytes_video
    ):
        client = openai_class.return_value
        client.videos.create.return_value = SimpleNamespace(
            id="vid_123", status="completed", error=None, expires_at=None
        )
        client.videos.download_content.return_value.read.return_value = b"mp4-bytes"
        encoded = "data:image/png;base64,iVBORw=="

        with self.subTest(source="data URL"):
            result = plugin.HelloCloudVideoGenProvider().generate(
                "Animate this", image_url=encoded
            )
            self.assertTrue(result["success"])
            request = client.videos.create.call_args.kwargs
            self.assertEqual(request["input_reference"][1], b"\x89PNG")
            self.assertEqual(request["input_reference"][2], "image/png")
            self.assertNotIn("input_reference", request["extra_body"])

        with self.subTest(source="local path"):
            with self.temporary_image() as path:
                result = plugin.HelloCloudVideoGenProvider().generate(
                    "Animate this", image_url=path
                )
            self.assertTrue(result["success"])
            request = client.videos.create.call_args.kwargs
            self.assertEqual(request["input_reference"][1], b"\x89PNG")
            self.assertEqual(request["input_reference"][2], "image/png")

    @patch.object(plugin, "save_bytes_video", return_value=Path("/tmp/hellocloud-test.mp4"))
    @patch("openai.OpenAI")
    @patch.object(plugin.HelloCloudVideoGenProvider, "_api_key", return_value="hello_test")
    def test_rejects_excess_or_non_https_reference_images(
        self, _api_key, openai_class, _save_bytes_video
    ):
        provider = plugin.HelloCloudVideoGenProvider()
        client = openai_class.return_value
        client.videos.create.return_value = SimpleNamespace(
            id="vid_123", status="completed", error=None, expires_at=None
        )
        client.videos.download_content.return_value.read.return_value = b"mp4-bytes"

        too_many = provider.generate(
            "Animate these",
            image_url="https://example.com/first.png",
            reference_image_urls=[
                "https://example.com/second.png",
                "https://example.com/third.png",
            ],
        )
        self.assertFalse(too_many["success"])
        self.assertEqual(too_many["error_type"], "invalid_request")

        non_https = provider.generate(
            "Animate this", reference_image_urls=["data:image/png;base64,iVBORw=="]
        )
        self.assertFalse(non_https["success"])
        self.assertEqual(non_https["error_type"], "invalid_request")
        openai_class.return_value.videos.create.assert_not_called()

    @patch("openai.OpenAI")
    @patch.object(plugin.HelloCloudVideoGenProvider, "_api_key", return_value="hello_test")
    def test_expired_job_finishes_without_download(self, _api_key, openai_class):
        provider = plugin.HelloCloudVideoGenProvider()
        provider._poll_deadline_s = 0
        client = openai_class.return_value
        client.videos.create.return_value = SimpleNamespace(
            id="vid_expired", status="expired", error="Job expired", expires_at=0
        )

        result = provider.generate("A dolphin")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "job_failed")
        self.assertIn("Job expired", result["error"])
        client.videos.download_content.assert_not_called()

    def temporary_image(self):
        import tempfile
        from contextlib import contextmanager

        @contextmanager
        def _make_image():
            with tempfile.NamedTemporaryFile(suffix=".png") as image:
                image.write(b"\x89PNG")
                image.flush()
                yield image.name

        return _make_image()


if __name__ == "__main__":
    unittest.main()
