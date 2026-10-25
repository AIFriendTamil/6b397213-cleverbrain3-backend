"""Video provider adapter converting media identifiers into sanitized streaming player embeds."""

import html
import logging
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

from src.lib.config import settings

logger = logging.getLogger(__name__)

# Regex patterns for video URL extraction and validation
DANGEROUS_PROTOCOLS_REGEX = re.compile(
    r"^(javascript|data|file|vbscript|about):", re.IGNORECASE
)
YOUTUBE_URL_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)
VIMEO_URL_REGEX = re.compile(
    r"(?:https?://)?(?:www\.|player\.)?vimeo\.com/(?:video/)?([0-9]+)",
    re.IGNORECASE,
)


class VideoProviderError(Exception):
    """Base exception class for video provider connector operations."""

    pass


class VideoValidationError(VideoProviderError):
    """Raised when video identifier or URL fails security sanitization or validation."""

    pass


class VideoProviderUnavailableError(VideoProviderError):
    """Raised when external video provider HTTP endpoint is unreachable or times out."""

    pass


class VideoProviderAdapter:
    """Adapter for resolving and sanitizing media identifiers into streaming embeds."""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        embed_base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """Initialize video provider adapter with configuration parameters."""
        cfg = settings.video_provider_config
        self.provider = (provider or cfg.provider).lower().strip()
        self.api_url = (api_url or cfg.api_url).rstrip("/")
        self.api_key = api_key or cfg.api_key
        self.embed_base_url = (embed_base_url or cfg.embed_base_url).rstrip("/")
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(cfg.timeout_seconds)
        )
        self._custom_client = http_client

    def sanitize_media_input(self, raw_input: str) -> str:
        """Sanitize raw media identifier or URL input.

        Args:
            raw_input: Raw video identifier, URL, or provider specifier.

        Returns:
            Sanitized input string.

        Raises:
            VideoValidationError: If input is empty or contains forbidden protocols/scripts.
        """
        if not raw_input or not isinstance(raw_input, str) or not raw_input.strip():
            raise VideoValidationError("Media identifier or URL cannot be empty.")

        cleaned = raw_input.strip()

        # Check for dangerous protocols or inline scripts
        if DANGEROUS_PROTOCOLS_REGEX.search(cleaned):
            raise VideoValidationError(
                f"Forbidden protocol detected in media input: '{cleaned}'"
            )

        if "<script" in cleaned.lower() or "javascript:" in cleaned.lower():
            raise VideoValidationError("Script injection detected in media input.")

        return cleaned

    def parse_media_identifier(self, media_input: str) -> Tuple[str, str]:
        """Parse raw media input into provider type and video ID.

        Args:
            media_input: Raw string media identifier or URL.

        Returns:
            Tuple of (provider_type, video_id).
        """
        cleaned = self.sanitize_media_input(media_input)

        # Provider prefix format, e.g. "youtube:dQw4w9WgXcQ" or "vimeo:12345678"
        if ":" in cleaned and not cleaned.startswith(("http://", "https://", "/")):
            parts = cleaned.split(":", 1)
            provider_prefix = parts[0].lower().strip()
            video_id = parts[1].strip()
            if provider_prefix in ("youtube", "vimeo", "direct"):
                return provider_prefix, video_id

        # YouTube URL matching
        yt_match = YOUTUBE_URL_REGEX.search(cleaned)
        if yt_match:
            return "youtube", yt_match.group(1)

        # Vimeo URL matching
        vimeo_match = VIMEO_URL_REGEX.search(cleaned)
        if vimeo_match:
            return "vimeo", vimeo_match.group(1)

        # Direct video files (.mp4, .m3u8, .webm, etc.) or absolute HTTP URLs
        if cleaned.startswith(("http://", "https://", "/")):
            parsed = urlparse(cleaned)
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in (".mp4", ".m3u8", ".webm", ".ogg")):
                return "direct", cleaned
            # Fallback for arbitrary HTTP URLs
            return "direct", cleaned

        # Simple ID string fallback (e.g. numeric Vimeo ID)
        if cleaned.isdigit():
            return "vimeo", cleaned

        return "default", cleaned

    def build_embed_url(self, provider_type: str, video_id: str) -> str:
        """Construct sanitized player embed URL with streaming controls enabled.

        Args:
            provider_type: Type of provider ('youtube', 'vimeo', 'direct', 'default').
            video_id: Extracted video ID or URL.

        Returns:
            Sanitized embed player URL string.
        """
        if provider_type == "youtube":
            return (
                f"https://www.youtube-nocookie.com/embed/{quote(video_id)}"
                f"?rel=0&controls=1&enablejsapi=1&autoplay=0"
            )
        elif provider_type == "vimeo":
            return (
                f"https://player.vimeo.com/video/{quote(video_id)}"
                f"?controls=1&title=0&byline=0&autoplay=0"
            )
        elif provider_type == "direct":
            return self.sanitize_media_input(video_id)
        else:
            base = self.embed_base_url or "https://player.vimeo.com/video"
            if video_id.startswith("http://") or video_id.startswith("https://"):
                return video_id
            return f"{base}/{quote(video_id)}?controls=1"

    def generate_iframe_markup(
        self,
        embed_url: str,
        title: Optional[str] = None,
        allow_fullscreen: bool = True,
    ) -> str:
        """Generate sanitized HTML iframe markup for video player.

        Args:
            embed_url: Sanitized embed URL string.
            title: Optional accessibility title for iframe element.
            allow_fullscreen: Whether to enable allowfullscreen attribute.

        Returns:
            HTML iframe string.
        """
        safe_url = html.escape(embed_url)
        safe_title = html.escape(title or "Knowledge Base Video Player")
        fullscreen_attr = ' allowfullscreen="true"' if allow_fullscreen else ""

        return (
            f'<iframe src="{safe_url}" title="{safe_title}" '
            f'width="100%" height="100%" frameborder="0" '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"{fullscreen_attr}>'
            f"</iframe>"
        )

    def get_embed_config(
        self,
        media_identifier: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convert media identifier into sanitized player embed configuration.

        Args:
            media_identifier: Raw media URL, ID, or provider specification.
            title: Optional title description for accessibility and metadata.

        Returns:
            Dictionary payload containing player embed settings and sanitized URLs.
        """
        sanitized_input = self.sanitize_media_input(media_identifier)
        provider_type, video_id = self.parse_media_identifier(sanitized_input)
        embed_url = self.build_embed_url(provider_type, video_id)
        iframe_markup = self.generate_iframe_markup(embed_url, title=title)

        return {
            "original_input": sanitized_input,
            "provider": provider_type,
            "video_id": video_id,
            "embed_url": embed_url,
            "has_controls": True,
            "allow_fullscreen": True,
            "aspect_ratio": "16:9",
            "sanitized_iframe": iframe_markup,
            "title": title or "Embedded Video Player",
        }

    def resolve_media_stream(
        self,
        media_identifier: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve streaming player embed specification with optional remote provider lookup.

        Args:
            media_identifier: Raw media URL, ID, or provider specification.
            title: Optional title description.

        Returns:
            Dictionary payload containing video player embed details.

        Raises:
            VideoProviderUnavailableError: If external HTTP provider call fails.
        """
        config = self.get_embed_config(media_identifier, title=title)

        if self.provider in ("http", "external"):
            try:
                client = self._custom_client or httpx.Client(
                    timeout=self.timeout_seconds
                )
                headers = {"Accept": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                response = client.get(
                    f"{self.api_url}/resolve",
                    params={"media_id": config["video_id"], "provider": config["provider"]},
                    headers=headers,
                )
                if response.status_code >= 400:
                    logger.warning(
                        "External video provider returned HTTP status %d for media '%s'",
                        response.status_code,
                        media_identifier,
                    )
                    raise VideoProviderUnavailableError(
                        f"External video provider request failed with status code {response.status_code}"
                    )

                remote_data = response.json()
                if isinstance(remote_data, dict) and "embed_url" in remote_data:
                    config["embed_url"] = self.sanitize_media_input(remote_data["embed_url"])
                    config["sanitized_iframe"] = self.generate_iframe_markup(
                        config["embed_url"], title=title
                    )

            except httpx.RequestError as exc:
                logger.error("External video provider network error: %s", str(exc))
                raise VideoProviderUnavailableError(
                    f"Video provider service unavailable: {str(exc)}"
                ) from exc

        return config
