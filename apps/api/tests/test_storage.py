from pathlib import Path
from types import SimpleNamespace

from app.services.storage import persist_artifact


def test_persist_artifact_uses_r2_compatible_put_object_headers(monkeypatch, tmp_path: Path):
    artifact = tmp_path / "document.docx"
    artifact.write_bytes(b"generated document")
    uploads = []

    class FakeS3Client:
        def upload_file(self, *args, **kwargs):
            uploads.append((args, kwargs))

    monkeypatch.setattr(
        "app.services.storage.get_settings",
        lambda: SimpleNamespace(storage_backend="s3", s3_bucket="labor-docs-files"),
    )
    monkeypatch.setattr("app.services.storage._s3_client", lambda: FakeS3Client())

    stored_path = persist_artifact("case-1", artifact)

    assert stored_path == "s3://labor-docs-files/generated/case-1/document.docx"
    assert uploads[0][1]["ExtraArgs"] == {"ContentType": "application/octet-stream"}
