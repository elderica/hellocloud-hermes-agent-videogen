"""HelloCloud (https://router.hello.ai) 動画生成バックエンド。

OpenAI 互換の ``/v1/videos`` で MiniMax H3 を利用し、非同期ジョブの完了後に
MP4 を ``$HERMES_HOME/cache/generated/videos/`` へ保存します。
API キーは環境変数 ``HELLOCLOUD_API_KEY`` から読み込みます。

ドキュメント: https://router.hello.ai/docs/videos/
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.secret_scope import get_secret_str
from agent.video_gen_provider import (
    OpenAICompatibleVideoGenProvider,
    error_response,
    save_bytes_video,
    success_response,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.router.hello.ai/v1"
DEFAULT_MODEL = "minimax-h3"
BASE_URL_ENV = "HELLOCLOUD_VIDEO_BASE_URL"
REQUEST_TIMEOUT_SECONDS = 120.0

_ASPECT_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
_MIN_DURATION = 5
_MAX_DURATION = 15
_MAX_REFERENCE_IMAGES = 2


def _image_file_upload(ref: str) -> Tuple[str, bytes, str]:
    """data URL / ローカル画像を OpenAI SDK のファイルアップロード形式にする。"""
    if ref.startswith("data:"):
        header, separator, encoded = ref.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ValueError("画像の data URL は base64 形式である必要があります")
        mime = header[5:].split(";", 1)[0] or "image/png"
        if not mime.startswith("image/"):
            raise ValueError("画像以外の data URL は指定できません")
        raw = base64.b64decode(encoded, validate=True)
        extension = mimetypes.guess_extension(mime) or ".png"
        return f"input{extension}", raw, mime

    path = Path(ref)
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime.startswith("image/"):
        raise ValueError("画像ファイルを指定してください")
    return path.name, raw, mime


class HelloCloudVideoGenProvider(OpenAICompatibleVideoGenProvider):
    """HelloCloud MiniMax H3 text-to-video / image-to-video プロバイダー。"""

    name = "hellocloud"
    display_name = "HelloCloud"
    _env_key = "HELLOCLOUD_API_KEY"
    _default_base_url = DEFAULT_BASE_URL
    _poll_interval_s = 10.0
    _poll_deadline_s = 1800.0

    def _base_url(self) -> str:
        """設定 ``video_gen.hellocloud.base_url`` → 環境変数 → 既定 URL。"""
        try:
            from hermes_cli.config import load_config

            cfg = load_config()
            section = cfg.get("video_gen") if isinstance(cfg, dict) else {}
            hellocloud = section.get("hellocloud") if isinstance(section, dict) else {}
            if isinstance(hellocloud, dict):
                value = hellocloud.get("base_url")
                if isinstance(value, str) and value.strip():
                    return value.strip().rstrip("/")
        except Exception:  # noqa: BLE001
            pass
        return get_secret_str(BASE_URL_ENV).strip().rstrip("/") or DEFAULT_BASE_URL

    def _create_and_poll(self, client: Any, call_kwargs: Dict[str, Any]) -> Any:
        """期限切れも終端状態として扱い、30 分の期限付きでポーリングする。"""
        video = client.videos.create(**call_kwargs)
        terminal = {"completed", "succeeded", "failed", "error", "cancelled", "canceled", "expired"}
        deadline = time.monotonic() + self._poll_deadline_s
        while getattr(video, "status", None) not in terminal:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"動画ジョブ {getattr(video, 'id', '?')} が {int(self._poll_deadline_s)} 秒以内に完了しませんでした "
                    f"(status={getattr(video, 'status', None)!r})"
                )
            time.sleep(self._poll_interval_s)
            video = client.videos.retrieve(video.id)
        return video

    def list_models(self) -> List[Dict[str, Any]]:
        return [{
            "id": DEFAULT_MODEL,
            "display": "MiniMax H3 (HelloCloud)",
            "speed": "2K / 5-15s",
            "strengths": "音声生成、参照画像最大 2 枚、text-to-video / image-to-video",
            "price": "¥30/秒 + 参照画像 1 枚ごとに ¥10",
            "modalities": ["text", "image"],
            "min_duration": _MIN_DURATION,
            "max_duration": _MAX_DURATION,
        }]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": list(_ASPECT_RATIOS),
            "resolutions": ["2K"],
            "max_duration": _MAX_DURATION,
            "min_duration": _MIN_DURATION,
            "supports_audio": True,
            "supports_negative_prompt": False,
            "supports_seed": False,
            "supports_upscale": False,
            "max_reference_images": _MAX_REFERENCE_IMAGES,
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "HelloCloud",
            "badge": "paid",
            "tag": "MiniMax H3 via HelloCloud (api.router.hello.ai) — HELLOCLOUD_API_KEY (環境変数)",
            "env_vars": [],
        }

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # 2K 固定のため size は送らない。negative_prompt / seed / upscale は非対応。
        del resolution, negative_prompt, seed, kwargs
        prompt = (prompt or "").strip()
        model_id = (model or "").strip() or DEFAULT_MODEL
        image_url = (image_url or "").strip() or None
        references = [
            ref.strip() for ref in (reference_image_urls or [])
            if isinstance(ref, str) and ref.strip()
        ]

        def fail(error: str, error_type: str) -> Dict[str, Any]:
            return error_response(
                error=error, error_type=error_type, provider=self.name,
                model=model_id, prompt=prompt, aspect_ratio=aspect_ratio,
            )

        if not prompt:
            return fail("プロンプトは必須で、空ではない文字列である必要があります。", "invalid_request")
        if not self._api_key():
            return fail(
                "HELLOCLOUD_API_KEY が設定されていません。有料キー (hello_...) を設定してください。",
                "missing_credentials",
            )
        if len(references) + (1 if image_url else 0) > _MAX_REFERENCE_IMAGES:
            return fail("参照画像は image_url / reference_image_urls の合計で最大 2 枚です。", "invalid_request")
        if any(not ref.startswith("https://") for ref in references):
            return fail(
                "reference_image_urls は公開 HTTPS URL のみ指定できます。ローカル画像 / data URL は image_url を使ってください。",
                "invalid_request",
            )
        try:
            import openai
        except ImportError:
            return fail("openai パッケージが必要です (pip install openai)。", "missing_dependency")

        seconds = max(_MIN_DURATION, min(_MAX_DURATION, duration)) if duration is not None else None
        extra_body: Dict[str, Any] = {"aspect_ratio": aspect_ratio}
        if audio is not None:
            extra_body["generate_audio"] = bool(audio)
        if image_url and image_url.startswith("https://"):
            extra_body["input_reference"] = image_url
        if references:
            extra_body["input_references"] = [
                {"type": "image", "url": ref} for ref in references
            ]
        request: Dict[str, Any] = {"model": model_id, "prompt": prompt, "extra_body": extra_body}
        if image_url and not image_url.startswith("https://"):
            try:
                request["input_reference"] = _image_file_upload(image_url)
            except Exception as exc:  # noqa: BLE001
                return fail(f"参照画像を読み込めませんでした: {exc}", "invalid_request")
        if seconds is not None:
            request["seconds"] = str(seconds)

        client = openai.OpenAI(
            api_key=self._api_key(), base_url=self._base_url(), timeout=REQUEST_TIMEOUT_SECONDS,
        )
        try:
            try:
                video = self._create_and_poll(client, request)
            except Exception as exc:  # noqa: BLE001
                logger.debug("HelloCloud video generation failed", exc_info=True)
                return fail(f"HelloCloud 動画生成に失敗しました: {exc}", "api_error")

            status = getattr(video, "status", None)
            if status not in ("completed", "succeeded"):
                job_error = getattr(video, "error", None)
                return fail(
                    str(job_error) if job_error else f"動画ジョブが status={status!r} で終了しました。",
                    "job_failed",
                )
            try:
                raw = client.videos.download_content(video.id).read()
                video_ref = str(save_bytes_video(raw, prefix=self.name, extension="mp4"))
            except Exception as exc:  # noqa: BLE001
                logger.debug("HelloCloud video download failed", exc_info=True)
                return fail(f"動画の取得・保存に失敗しました: {exc}", "empty_response")

            extra: Dict[str, Any] = {"job_id": str(video.id), "resolution": "2K"}
            if getattr(video, "expires_at", None) is not None:
                extra["expires_at"] = int(video.expires_at)
            return success_response(
                video=video_ref, model=model_id, prompt=prompt,
                modality="image" if image_url or references else "text",
                aspect_ratio=aspect_ratio, duration=seconds or 0, provider=self.name, extra=extra,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


def register(ctx) -> None:
    """プラグインエントリポイント。"""
    ctx.register_video_gen_provider(HelloCloudVideoGenProvider())
