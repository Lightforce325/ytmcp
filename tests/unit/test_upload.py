"""Tests for core/upload.py: the resumable (chunked) upload engine."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from ytmcp.config import Settings
from ytmcp.core import upload as upload_mod
from ytmcp.core.client import YouTubeClient
from ytmcp.core.exceptions import AuthError, UploadError
from ytmcp.core.models import Visibility
from ytmcp.core.upload import STUDIO_UPLOAD_URL, UploadController

UPLOAD_URL = "https://upload.example.com/resumable/session-42"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_file(tmp_path: Path, size: int, name: str = "video.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def _mock_init(payload: dict | None = None, **kwargs) -> respx.Route:
    return respx.post(STUDIO_UPLOAD_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"X-Upload-Content-Url": UPLOAD_URL},
            json=payload if payload is not None else {},
            **kwargs,
        )
    )


# --------------------------------------------------------------------------- #
# Chunk size sanity (Google resumable protocol)
# --------------------------------------------------------------------------- #
def test_chunk_size_is_multiple_of_256_kib() -> None:
    unit = 256 * 1024  # 256 KiB
    assert upload_mod.CHUNK_SIZE % unit == 0, (
        f"CHUNK_SIZE={upload_mod.CHUNK_SIZE} must be a multiple of {unit}"
    )
    assert upload_mod.CHUNK_SIZE > 0


# --------------------------------------------------------------------------- #
# Missing file
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_upload_missing_file_raises(settings: Settings, tmp_path: Path) -> None:
    async with YouTubeClient(settings) as client:
        controller = UploadController(client)
        with pytest.raises(UploadError, match="not found"):
            await controller.upload(tmp_path / "does-not-exist.mp4", title="Nope")


# --------------------------------------------------------------------------- #
# _send_chunks: multi-chunk Content-Range / Content-Length
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_send_chunks_multi_chunk_ranges_and_lengths(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_size = 256 * 1024
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", chunk_size)

    file_size = chunk_size * 2 + 10  # 3 chunks: 2 full + 1 tiny
    path = _make_file(tmp_path, file_size)

    put_route = respx.put(UPLOAD_URL).mock(
        side_effect=[
            httpx.Response(308),  # chunk 1 accepted, more expected
            httpx.Response(308),  # chunk 2 accepted, more expected
            httpx.Response(200, json={"videoId": "abc123"}),  # final chunk
        ]
    )

    async with YouTubeClient(settings) as client:
        controller = UploadController(client)
        video_id = await controller._send_chunks(UPLOAD_URL, path, file_size)

    assert video_id == "abc123"
    assert put_route.call_count == 3

    expected = [
        (f"bytes 0-{chunk_size - 1}/{file_size}", chunk_size),
        (f"bytes {chunk_size}-{chunk_size * 2 - 1}/{file_size}", chunk_size),
        (f"bytes {chunk_size * 2}-{file_size - 1}/{file_size}", 10),
    ]
    for call, (content_range, content_length) in zip(put_route.calls, expected, strict=True):
        headers = call.request.headers
        assert headers["Content-Range"] == content_range
        assert headers["Content-Length"] == str(content_length)
        assert headers["Content-Type"] == "application/octet-stream"
        assert len(call.request.content) == content_length


@pytest.mark.asyncio
@respx.mock
async def test_send_chunks_throws_video_id_from_headers_fallback(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 response without JSON falls back to the upload-id header."""
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 100)
    respx.put(UPLOAD_URL).mock(
        return_value=httpx.Response(200, headers={"X-GUploader-UploadID": "up-999"})
    )
    async with YouTubeClient(settings) as client:
        video_id = await UploadController(client)._send_chunks(UPLOAD_URL, path, 100)
    assert video_id == "up-999"


# --------------------------------------------------------------------------- #
# _send_chunks: 308 vs 200/201 and unexpected statuses
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201])
@respx.mock
async def test_send_chunks_completion_statuses(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 64)
    respx.put(UPLOAD_URL).mock(
        return_value=httpx.Response(status, json={"videoId": "vid-end"})
    )
    async with YouTubeClient(settings) as client:
        video_id = await UploadController(client)._send_chunks(UPLOAD_URL, path, 64)
    assert video_id == "vid-end"


@pytest.mark.asyncio
@respx.mock
async def test_send_chunks_accepts_308_without_video_id(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session that only ever returns 308 yields the 'unknown' fallback id."""
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 50)
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(308))
    async with YouTubeClient(settings) as client:
        video_id = await UploadController(client)._send_chunks(UPLOAD_URL, path, 50)
    assert video_id == "unknown"


@pytest.mark.asyncio
@respx.mock
async def test_send_chunks_raises_on_unexpected_status(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 100)
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(404, text="gone"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UploadError, match="Chunk 1/1 failed"):
            await UploadController(client)._send_chunks(UPLOAD_URL, path, 100)


@pytest.mark.asyncio
@respx.mock
async def test_send_chunks_raises_on_failure_after_partial_success(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_size = 256 * 1024
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", chunk_size)
    file_size = chunk_size * 2
    path = _make_file(tmp_path, file_size)
    respx.put(UPLOAD_URL).mock(
        side_effect=[httpx.Response(308), httpx.Response(400, text="bad range")]
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UploadError, match="Chunk 2/2 failed"):
            await UploadController(client)._send_chunks(UPLOAD_URL, path, file_size)


# --------------------------------------------------------------------------- #
# Full upload flow
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_upload_returns_result_with_video_id_and_url(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 1024)

    init_route = _mock_init()
    put_route = respx.put(UPLOAD_URL).mock(
        return_value=httpx.Response(200, json={"videoId": "VID001"})
    )

    async with YouTubeClient(settings) as client:
        result = await UploadController(client).upload(
            path,
            title="Judul Video",
            description="deskripsi",
            tags=["a", "b"],
            visibility=Visibility.UNLISTED,
        )

    assert result.video_id == "VID001"
    assert result.title == "Judul Video"
    assert result.url == "https://www.youtube.com/watch?v=VID001"
    assert result.status.upload_status == "uploaded"
    assert result.status.privacy_status == Visibility.UNLISTED

    assert init_route.called
    assert put_route.call_count == 1
    init_body = init_route.calls.last.request.content.decode()
    assert "Judul Video" in init_body
    assert '"fileSize": "1024"' in init_body or '"fileSize":"1024"' in init_body


@pytest.mark.asyncio
@respx.mock
async def test_upload_truncates_long_title(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 32)
    init_route = _mock_init()
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(200, json={"videoId": "V"}))

    long_title = "T" * 150
    async with YouTubeClient(settings) as client:
        await UploadController(client).upload(path, title=long_title)

    body = init_route.calls.last.request.content.decode()
    assert "T" * 150 not in body
    assert "T" * 100 in body


@pytest.mark.asyncio
@respx.mock
async def test_upload_init_auth_error(settings: Settings, tmp_path: Path) -> None:
    path = _make_file(tmp_path, 10)
    respx.post(STUDIO_UPLOAD_URL).mock(return_value=httpx.Response(403, text="no cookies"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(AuthError):
            await UploadController(client).upload(path, title="X")


@pytest.mark.asyncio
@respx.mock
async def test_upload_init_failure_raises_upload_error(
    settings: Settings, tmp_path: Path
) -> None:
    path = _make_file(tmp_path, 10)
    respx.post(STUDIO_UPLOAD_URL).mock(return_value=httpx.Response(400, text="bad request"))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UploadError, match="Upload init failed"):
            await UploadController(client).upload(path, title="X")


@pytest.mark.asyncio
@respx.mock
async def test_upload_init_without_upload_url_raises(
    settings: Settings, tmp_path: Path
) -> None:
    path = _make_file(tmp_path, 10)
    respx.post(STUDIO_UPLOAD_URL).mock(return_value=httpx.Response(200, json={}))
    async with YouTubeClient(settings) as client:
        with pytest.raises(UploadError, match="No upload URL"):
            await UploadController(client).upload(path, title="X")


@pytest.mark.asyncio
@respx.mock
async def test_upload_url_can_come_from_location_header(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 10)
    respx.post(STUDIO_UPLOAD_URL).mock(
        return_value=httpx.Response(200, headers={"Location": UPLOAD_URL}, json={})
    )
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(200, json={"id": "from-id"}))

    async with YouTubeClient(settings) as client:
        result = await UploadController(client).upload(path, title="X")
    assert result.video_id == "from-id"


# --------------------------------------------------------------------------- #
# Thumbnail (non-fatal)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_thumbnail_failure_does_not_fail_upload(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 16)
    thumb = _make_file(tmp_path, 8, name="thumb.png")

    _mock_init()
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(200, json={"videoId": "V1"}))
    # Thumbnail endpoint rejects the request -> must be swallowed.
    respx.post(url__regex=r".*video_manager/set_thumbnail.*").mock(
        return_value=httpx.Response(500, text="thumbnail boom")
    )

    async with YouTubeClient(settings) as client:
        result = await UploadController(client).upload(
            path, title="X", thumbnail_path=thumb
        )
    assert result.video_id == "V1"


@pytest.mark.asyncio
@respx.mock
async def test_upload_calls_set_thumbnail_on_success(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_mod, "CHUNK_SIZE", 256 * 1024)
    path = _make_file(tmp_path, 16)
    thumb = _make_file(tmp_path, 8, name="thumb.png")

    _mock_init()
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(200, json={"videoId": "V2"}))
    thumb_route = respx.post(url__regex=r".*video_manager/set_thumbnail.*").mock(
        return_value=httpx.Response(200, json={})
    )

    async with YouTubeClient(settings) as client:
        result = await UploadController(client).upload(
            path, title="X", thumbnail_path=thumb
        )
    assert result.video_id == "V2"
    assert thumb_route.called
    assert "V2" in thumb_route.calls.last.request.content.decode(errors="ignore")


@pytest.mark.asyncio
@respx.mock
async def test_set_thumbnail_raises_on_missing_file(
    settings: Settings, tmp_path: Path
) -> None:
    async with YouTubeClient(settings) as client:
        with pytest.raises(UploadError, match="Thumbnail not found"):
            await UploadController(client).set_thumbnail("vid", tmp_path / "nope.png")


@pytest.mark.asyncio
@respx.mock
async def test_set_thumbnail_raises_on_http_error(
    settings: Settings, tmp_path: Path
) -> None:
    thumb = _make_file(tmp_path, 8, name="thumb.png")
    respx.post(url__regex=r".*video_manager/set_thumbnail.*").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    async with YouTubeClient(settings) as client:
        with pytest.raises(UploadError, match="Thumbnail set failed"):
            await UploadController(client).set_thumbnail("vid", thumb)
