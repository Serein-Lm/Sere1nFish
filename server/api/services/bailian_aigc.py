"""Aliyun Bailian AIGC runtime client.

The HTTP APIs are intentionally wrapped here instead of spreading endpoint
details across routers. Qwen image edit is synchronous; Wanx image edit and
video generation are asynchronous task APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from api.services.runtime_config import get_runtime_app_config, get_runtime_config_section


TaskProtocol = Literal["workspace", "legacy", "auto"]


class BailianAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class BailianRuntimeConfig:
    api_key: str
    workspace_id: str | None
    region: str
    base_url: str | None
    legacy_base_url: str | None
    timeout_seconds: float
    qwen_image_edit_model: str
    wanx_image_edit_model: str
    text_to_video_model: str
    image_to_video_model: str


_REGION_DOMAINS = {
    "beijing": "cn-beijing.maas.aliyuncs.com",
    "singapore": "ap-southeast-1.maas.aliyuncs.com",
    "frankfurt": "eu-central-1.maas.aliyuncs.com",
}

_LEGACY_BASE_URLS = {
    "beijing": "https://dashscope.aliyuncs.com/api/v1",
    "virginia": "https://dashscope-us.aliyuncs.com/api/v1",
}

_REGION_ALIASES = {
    "cn-beijing": "beijing",
    "china": "beijing",
    "zh": "beijing",
    "bj": "beijing",
    "ap-southeast-1": "singapore",
    "sg": "singapore",
    "intl": "singapore",
    "us": "virginia",
    "us-east": "virginia",
    "us-east-1": "virginia",
    "eu": "frankfurt",
    "eu-central-1": "frankfurt",
}


def _normalize_region(region: str | None) -> str:
    value = (region or "beijing").strip().lower().replace("_", "-")
    return _REGION_ALIASES.get(value, value or "beijing")


def _api_v1_base(url: str) -> str:
    base = str(url).strip().rstrip("/")
    if base.endswith("/api/v1"):
        return base
    return f"{base}/api/v1"


def _extract_task(output: dict[str, Any]) -> dict[str, Any]:
    data = output.get("output") if isinstance(output.get("output"), dict) else {}
    return {
        "task_id": data.get("task_id"),
        "task_status": data.get("task_status"),
    }


def _extract_qwen_images(output: dict[str, Any]) -> list[str]:
    images: list[str] = []
    data = output.get("output") if isinstance(output.get("output"), dict) else {}
    for choice in data.get("choices") or []:
        message = choice.get("message") if isinstance(choice, dict) else {}
        for item in message.get("content") or []:
            if isinstance(item, dict) and item.get("image"):
                images.append(str(item["image"]))
    return images


def _extract_result_urls(output: dict[str, Any]) -> dict[str, Any]:
    data = output.get("output") if isinstance(output.get("output"), dict) else {}
    result_urls: list[str] = []
    for item in data.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            result_urls.append(str(item["url"]))
    return {
        "result_urls": result_urls,
        "video_url": data.get("video_url"),
        "task_status": data.get("task_status"),
        "task_id": data.get("task_id"),
    }


async def load_bailian_config() -> BailianRuntimeConfig:
    app_config = await get_runtime_app_config()
    bailian = await get_runtime_config_section("bailian")
    rt = app_config.runtime

    api_key = bailian.get("api_key") or rt.api_key
    if not api_key:
        raise BailianAPIError("Bailian api_key is not configured", status_code=500)

    region = _normalize_region(bailian.get("region") or "beijing")
    return BailianRuntimeConfig(
        api_key=api_key,
        workspace_id=bailian.get("workspace_id"),
        region=region,
        base_url=bailian.get("base_url"),
        legacy_base_url=bailian.get("legacy_base_url"),
        timeout_seconds=float(bailian.get("timeout_seconds") or 300),
        qwen_image_edit_model=bailian.get("qwen_image_edit_model") or "qwen-image-2.0-pro",
        wanx_image_edit_model=bailian.get("wanx_image_edit_model") or "wanx2.1-imageedit",
        text_to_video_model=bailian.get("text_to_video_model") or "wan2.7-t2v-2026-06-12",
        image_to_video_model=bailian.get("image_to_video_model") or "wan2.7-i2v-2026-04-25",
    )


class BailianAIGCClient:
    def __init__(self, config: BailianRuntimeConfig):
        self.config = config

    def workspace_base_url(self) -> str:
        if self.config.base_url:
            return _api_v1_base(self.config.base_url)
        domain = _REGION_DOMAINS.get(self.config.region)
        if not domain:
            raise BailianAPIError(f"Unsupported Bailian workspace region: {self.config.region}", status_code=400)
        if not self.config.workspace_id:
            raise BailianAPIError("bailian.workspace_id is required for workspace endpoint", status_code=500)
        return f"https://{self.config.workspace_id}.{domain}/api/v1"

    def legacy_base_url(self) -> str:
        if self.config.legacy_base_url:
            return _api_v1_base(self.config.legacy_base_url)
        if self.config.region in _LEGACY_BASE_URLS:
            return _LEGACY_BASE_URLS[self.config.region]
        return self.workspace_base_url()

    def task_base_url(self, protocol: TaskProtocol) -> tuple[str, str]:
        if protocol == "legacy":
            return self.legacy_base_url(), "legacy"
        if protocol == "workspace":
            return self.workspace_base_url(), "workspace"
        if self.config.workspace_id or self.config.base_url:
            return self.workspace_base_url(), "workspace"
        return self.legacy_base_url(), "legacy"

    def image_to_video_base_url(self, protocol: TaskProtocol) -> tuple[str, str]:
        if protocol == "legacy":
            return self.legacy_base_url(), "legacy"
        if protocol == "workspace":
            return self.workspace_base_url(), "workspace"
        # Wan 2.7 I2V official HTTP examples still use dashscope.aliyuncs.com
        # for Beijing, while Singapore uses workspace-specific domains.
        if self.config.region == "beijing":
            return self.legacy_base_url(), "legacy"
        return self.workspace_base_url(), "workspace"

    def _headers(self, *, async_task: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if async_task:
            headers["X-DashScope-Async"] = "enable"
        return headers

    async def _post_json(self, url: str, payload: dict[str, Any], *, async_task: bool = False) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(url, headers=self._headers(async_task=async_task), json=payload)
        except httpx.HTTPError as exc:
            raise BailianAPIError(f"Bailian request failed: {exc}") from exc
        return self._decode_response(response)

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise BailianAPIError(f"Bailian task query failed: {exc}") from exc
        return self._decode_response(response)

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BailianAPIError(
                f"Bailian returned non-JSON response: HTTP {response.status_code}",
                status_code=502,
                payload=response.text[:500],
            ) from exc

        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise BailianAPIError(
                message or f"Bailian HTTP error {response.status_code}",
                status_code=502,
                payload=payload,
            )

        if isinstance(payload, dict) and payload.get("code"):
            raise BailianAPIError(
                payload.get("message") or str(payload.get("code")),
                status_code=502,
                payload=payload,
            )
        return payload

    async def qwen_image_edit(
        self,
        *,
        images: list[str],
        prompt: str,
        model: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.workspace_base_url()}/services/aigc/multimodal-generation/generation"
        content = [{"image": image} for image in images]
        content.append({"text": prompt})
        payload = {
            "model": model or self.config.qwen_image_edit_model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ]
            },
            "parameters": parameters or {
                "n": 1,
                "negative_prompt": " ",
                "prompt_extend": True,
                "watermark": False,
            },
        }
        response = await self._post_json(url, payload)
        return {
            "ok": True,
            "provider": "aliyun_bailian",
            "mode": "qwen_image_edit",
            "model": payload["model"],
            "images": _extract_qwen_images(response),
            "response": response,
        }

    async def wanx_image_edit(
        self,
        *,
        base_image_url: str,
        prompt: str,
        function: str = "description_edit",
        mask_image_url: str | None = None,
        model: str | None = None,
        parameters: dict[str, Any] | None = None,
        extra_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.workspace_base_url()}/services/aigc/image2image/image-synthesis"
        input_data = {
            "function": function,
            "prompt": prompt,
            "base_image_url": base_image_url,
            **(extra_input or {}),
        }
        if mask_image_url:
            input_data["mask_image_url"] = mask_image_url
        payload = {
            "model": model or self.config.wanx_image_edit_model,
            "input": input_data,
            "parameters": parameters or {"n": 1},
        }
        response = await self._post_json(url, payload, async_task=True)
        task = _extract_task(response)
        return {
            "ok": True,
            "provider": "aliyun_bailian",
            "mode": "wanx_image_edit",
            "model": payload["model"],
            "task_protocol": "workspace",
            **task,
            "response": response,
        }

    async def text_to_video(
        self,
        *,
        prompt: str,
        negative_prompt: str | None = None,
        audio_url: str | None = None,
        model: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.workspace_base_url()}/services/aigc/video-generation/video-synthesis"
        input_data: dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            input_data["negative_prompt"] = negative_prompt
        if audio_url:
            input_data["audio_url"] = audio_url
        payload = {
            "model": model or self.config.text_to_video_model,
            "input": input_data,
            "parameters": parameters or {
                "resolution": "720P",
                "ratio": "16:9",
                "duration": 5,
                "prompt_extend": True,
                "watermark": False,
            },
        }
        response = await self._post_json(url, payload, async_task=True)
        task = _extract_task(response)
        return {
            "ok": True,
            "provider": "aliyun_bailian",
            "mode": "text_to_video",
            "model": payload["model"],
            "task_protocol": "workspace",
            **task,
            "response": response,
        }

    async def image_to_video(
        self,
        *,
        img_url: str | None = None,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        audio_url: str | None = None,
        last_frame_url: str | None = None,
        first_clip_url: str | None = None,
        media: list[dict[str, str]] | None = None,
        template: str | None = None,
        model: str | None = None,
        parameters: dict[str, Any] | None = None,
        protocol: TaskProtocol = "auto",
    ) -> dict[str, Any]:
        resolved_model = model or self.config.image_to_video_model
        use_legacy_payload = protocol == "legacy" or resolved_model.startswith(("wan2.6", "wan2.5", "wanx2.1"))
        base_url, resolved_protocol = self.image_to_video_base_url("legacy" if use_legacy_payload else protocol)
        url = f"{base_url}/services/aigc/video-generation/video-synthesis"

        input_data: dict[str, Any] = {}
        if use_legacy_payload:
            if not img_url:
                raise BailianAPIError("legacy image_to_video requires img_url", status_code=422)
            input_data["img_url"] = img_url
        else:
            media_items = list(media or [])
            if first_clip_url:
                media_items.append({"type": "first_clip", "url": first_clip_url})
            elif img_url:
                media_items.append({"type": "first_frame", "url": img_url})
            if last_frame_url:
                media_items.append({"type": "last_frame", "url": last_frame_url})
            if audio_url:
                media_items.append({"type": "driving_audio", "url": audio_url})
            if not media_items:
                raise BailianAPIError("image_to_video requires img_url, first_clip_url, or media", status_code=422)
            input_data["media"] = media_items
        if prompt:
            input_data["prompt"] = prompt
        if negative_prompt:
            input_data["negative_prompt"] = negative_prompt
        if audio_url and use_legacy_payload:
            input_data["audio_url"] = audio_url
        if template and use_legacy_payload:
            input_data["template"] = template
        payload = {
            "model": resolved_model,
            "input": input_data,
            "parameters": parameters or {
                "resolution": "720P",
                "duration": 5,
                "prompt_extend": True,
                "watermark": False,
            },
        }
        response = await self._post_json(url, payload, async_task=True)
        task = _extract_task(response)
        return {
            "ok": True,
            "provider": "aliyun_bailian",
            "mode": "image_to_video",
            "model": payload["model"],
            "task_protocol": resolved_protocol,
            "payload_protocol": "legacy" if use_legacy_payload else "wan2.7",
            **task,
            "response": response,
        }

    async def query_task(self, task_id: str, *, protocol: TaskProtocol = "workspace") -> dict[str, Any]:
        base_url, resolved_protocol = self.task_base_url(protocol)
        response = await self._get_json(f"{base_url}/tasks/{task_id}")
        return {
            "ok": True,
            "provider": "aliyun_bailian",
            "task_protocol": resolved_protocol,
            **_extract_result_urls(response),
            "response": response,
        }


async def get_bailian_client() -> BailianAIGCClient:
    return BailianAIGCClient(await load_bailian_config())
