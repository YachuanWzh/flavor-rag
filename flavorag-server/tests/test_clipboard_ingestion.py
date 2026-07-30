from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from starlette.datastructures import Headers, UploadFile

from app.api import knowledge
from app.ingestion.dedup import DuplicateCheckResult


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_clipboard_text_and_image_reuse_ingestion_job(tmp_path, monkeypatch):
    image_buffer = io.BytesIO()
    Image.new("RGB", (3, 2), color="blue").save(image_buffer, format="PNG")
    image = UploadFile(
        io.BytesIO(image_buffer.getvalue()),
        filename="paste-test.png",
        headers=Headers({"content-type": "image/png"}),
    )
    db = FakeSession()
    kb = SimpleNamespace(
        id="kb-1",
        tenant_id="tenant-1",
        department_id="dept-1",
    )
    user = SimpleNamespace(
        id="user-1",
        tenant_id="tenant-1",
        department_id="dept-1",
        role="admin",
    )
    captured = {}

    async def fake_require_kb(*args, **kwargs):
        return kb

    class FakeDetector:
        async def check_file(self, *args, **kwargs):
            return DuplicateCheckResult(is_duplicate=False)

    async def fake_enqueue(session, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(knowledge, "_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(knowledge, "require_kb", fake_require_kb)
    monkeypatch.setattr(knowledge, "DuplicateDetector", FakeDetector)
    monkeypatch.setattr(knowledge, "enqueue_ingestion_job", fake_enqueue)
    monkeypatch.setattr(knowledge.settings, "ingestion_async_enabled", True)

    result = await knowledge.paste_clipboard_document(
        kb_id="kb-1",
        content="# 标题\n\n正文\n\n![截图](clipboard-image://paste-test)",
        doc_name="产品说明",
        chunk_strategy="SEMANTIC",
        chunk_size=600,
        overlap=100,
        images=[image],
        image_references="[]",
        db=db,
        user=user,
    )

    document = db.added[0]
    assert result["data"]["status"] == "queued"
    assert document.doc_name == "产品说明.clipdoc"
    assert document.file_type == "clipdoc"
    assert document.chunk_config["clipboardImageCount"] == 1
    assert captured["doc"] is document
    assert captured["source_type"] == "file"
    assert captured["chunk_config"].strategy == "SEMANTIC"

    bundle = json.loads((tmp_path / f"{document.id}.clipdoc").read_text("utf-8"))
    assert bundle["version"] == 1
    assert len(bundle["images"]) == 1
    assert "clipboard-image://paste-test" not in bundle["content"]
    assert "clipboard-image://" in bundle["content"]


@pytest.mark.asyncio
async def test_rich_text_image_url_is_fetched_and_bundled(tmp_path, monkeypatch):
    image_buffer = io.BytesIO()
    Image.new("RGB", (4, 3), color="green").save(image_buffer, format="PNG")
    db = FakeSession()
    kb = SimpleNamespace(
        id="kb-1",
        tenant_id="tenant-1",
        department_id="dept-1",
    )
    user = SimpleNamespace(
        id="user-1",
        tenant_id="tenant-1",
        department_id="dept-1",
        role="admin",
    )
    fetched_urls = []

    async def fake_require_kb(*args, **kwargs):
        return kb

    class FakeDetector:
        async def check_file(self, *args, **kwargs):
            return DuplicateCheckResult(is_duplicate=False)

    class FakeFetcher:
        async def fetch(self, url):
            fetched_urls.append(url)
            if "expired" in url:
                raise knowledge.URLSecurityError("expired clipboard URL")
            return SimpleNamespace(
                content=image_buffer.getvalue(),
                content_type="image/png",
                filename="diagram.png",
            )

    async def fake_enqueue(*args, **kwargs):
        return None

    monkeypatch.setattr(knowledge, "_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(knowledge, "require_kb", fake_require_kb)
    monkeypatch.setattr(knowledge, "DuplicateDetector", FakeDetector)
    monkeypatch.setattr(knowledge, "SafeURLFetcher", lambda **kwargs: FakeFetcher())
    monkeypatch.setattr(knowledge, "enqueue_ingestion_job", fake_enqueue)
    monkeypatch.setattr(knowledge.settings, "ingestion_async_enabled", True)

    result = await knowledge.paste_clipboard_document(
        kb_id="kb-1",
        content="正文\n\n![流程图](clipboard-image://rich-image-1)",
        doc_name="富文本文档",
        chunk_strategy="FIXED_WINDOW",
        chunk_size=512,
        overlap=128,
        images=[],
        image_references=json.dumps([{
            "id": "rich-image-1",
            "url": "https://cdn.example.com/expired.png",
            "urls": [
                "https://cdn.example.com/expired.png",
                "https://cdn.example.com/diagram.png",
            ],
            "alt": "流程图",
        }]),
        db=db,
        user=user,
    )

    document = db.added[0]
    bundle = json.loads((tmp_path / f"{document.id}.clipdoc").read_text("utf-8"))
    assert result["data"]["status"] == "queued"
    assert fetched_urls == [
        "https://cdn.example.com/expired.png",
        "https://cdn.example.com/diagram.png",
    ]
    assert bundle["images"][0]["alt"] == "流程图"
    assert "clipboard-image://rich-image-1" not in bundle["content"]
