"""Unit tests for video provider adapter in backend/src/connectors/video_provider.py."""

import pytest
from unittest.mock import MagicMock, patch
import httpx

from src.connectors.video_provider import (
    VideoProviderAdapter,
    VideoProviderError,
    VideoValidationError,
    VideoProviderUnavailableError,
)


def test_sanitize_media_input_valid():
    adapter = VideoProviderAdapter()
    assert adapter.sanitize_media_input(" https://youtube.com/watch?v=dQw4w9WgXcQ ") == "https://youtube.com/watch?v=dQw4w9WgXcQ"


def test_sanitize_media_input_empty():
    adapter = VideoProviderAdapter()
    with pytest.raises(VideoValidationError, match="cannot be empty"):
        adapter.sanitize_media_input("")
    with pytest.raises(VideoValidationError, match="cannot be empty"):
        adapter.sanitize_media_input("   ")


def test_sanitize_media_input_dangerous_protocols():
    adapter = VideoProviderAdapter()
    with pytest.raises(VideoValidationError, match="Forbidden protocol"):
        adapter.sanitize_media_input("javascript:alert(1)")
    with pytest.raises(VideoValidationError, match="Forbidden protocol"):
        adapter.sanitize_media_input("data:text/html,<script>alert(1)</script>")


def test_sanitize_media_input_script_injection():
    adapter = VideoProviderAdapter()
    with pytest.raises(VideoValidationError, match="Script injection"):
        adapter.sanitize_media_input("http://example.com/video.mp4<script>alert(1)</script>")


def test_parse_media_identifier_prefix():
    adapter = VideoProviderAdapter()
    assert adapter.parse_media_identifier("youtube:dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")
    assert adapter.parse_media_identifier("vimeo:12345678") == ("vimeo", "12345678")
    assert adapter.parse_media_identifier("direct:https://example.com/stream.mp4") == ("direct", "https://example.com/stream.mp4")


def test_parse_media_identifier_youtube_urls():
    adapter = VideoProviderAdapter()
    assert adapter.parse_media_identifier("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")
    assert adapter.parse_media_identifier("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")
    assert adapter.parse_media_identifier("https://youtube.com/embed/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")


def test_parse_media_identifier_vimeo_urls():
    adapter = VideoProviderAdapter()
    assert adapter.parse_media_identifier("https://vimeo.com/12345678") == ("vimeo", "12345678")
    assert adapter.parse_media_identifier("https://player.vimeo.com/video/12345678") == ("vimeo", "12345678")
    assert adapter.parse_media_identifier("12345678") == ("vimeo", "12345678")


def test_parse_media_identifier_direct_urls():
    adapter = VideoProviderAdapter()
    assert adapter.parse_media_identifier("https://example.com/video.mp4") == ("direct", "https://example.com/video.mp4")
    assert adapter.parse_media_identifier("https://example.com/video.m3u8") == ("direct", "https://example.com/video.m3u8")
    assert adapter.parse_media_identifier("https://example.com/video.webm") == ("direct", "https://example.com/video.webm")


def test_build_embed_url():
    adapter = VideoProviderAdapter()
    yt_url = adapter.build_embed_url("youtube", "dQw4w9WgXcQ")
    assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in yt_url
    assert "controls=1" in yt_url

    vimeo_url = adapter.build_embed_url("vimeo", "12345678")
    assert "player.vimeo.com/video/12345678" in vimeo_url
    assert "controls=1" in vimeo_url

    direct_url = adapter.build_embed_url("direct", "https://example.com/stream.mp4")
    assert direct_url == "https://example.com/stream.mp4"

    default_url = adapter.build_embed_url("default", "custom_id")
    assert "custom_id" in default_url


def test_generate_iframe_markup():
    adapter = VideoProviderAdapter()
    markup = adapter.generate_iframe_markup("https://player.vimeo.com/video/12345", title="Test Title")
    assert '<iframe src="https://player.vimeo.com/video/12345"' in markup
    assert 'title="Test Title"' in markup
    assert 'allowfullscreen="true"' in markup


def test_get_embed_config():
    adapter = VideoProviderAdapter()
    config = adapter.get_embed_config("youtube:dQw4w9WgXcQ", title="Tutorial Video")
    assert config["provider"] == "youtube"
    assert config["video_id"] == "dQw4w9WgXcQ"
    assert config["has_controls"] is True
    assert config["allow_fullscreen"] is True
    assert "youtube-nocookie.com" in config["embed_url"]
    assert config["title"] == "Tutorial Video"
    assert "<iframe" in config["sanitized_iframe"]


def test_resolve_media_stream_mock():
    adapter = VideoProviderAdapter(provider="mock")
    config = adapter.resolve_media_stream("vimeo:12345678")
    assert config["provider"] == "vimeo"
    assert config["video_id"] == "12345678"


def test_resolve_media_stream_external_success():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"embed_url": "https://custom-embed.com/play/12345"}
    mock_client.get.return_value = mock_response

    adapter = VideoProviderAdapter(provider="external", http_client=mock_client)
    config = adapter.resolve_media_stream("vimeo:12345")
    assert config["embed_url"] == "https://custom-embed.com/play/12345"
    assert '<iframe src="https://custom-embed.com/play/12345"' in config["sanitized_iframe"]


def test_resolve_media_stream_external_http_error():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_client.get.return_value = mock_response

    adapter = VideoProviderAdapter(provider="external", http_client=mock_client)
    with pytest.raises(VideoProviderUnavailableError, match="status code 500"):
        adapter.resolve_media_stream("vimeo:12345")


def test_resolve_media_stream_external_network_error():
    mock_client = MagicMock()
    mock_client.get.side_effect = httpx.RequestError("Connection timeout")

    adapter = VideoProviderAdapter(provider="external", http_client=mock_client)
    with pytest.raises(VideoProviderUnavailableError, match="unavailable"):
        adapter.resolve_media_stream("vimeo:12345")
