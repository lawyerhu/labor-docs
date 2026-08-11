import hashlib
import io
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import boto3
from fastapi import HTTPException, UploadFile, status

from app.config import get_settings


ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".xls", ".xlsx"}
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def inspect_upload_bytes(data: bytes, suffix: str) -> str:
    """Validate the actual container before it is persisted or parsed."""
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if suffix in {".doc", ".xls"} and data.startswith(OLE_SIGNATURE):
        return "application/msword" if suffix == ".doc" else "application/vnd.ms-excel"
    if suffix in {".docx", ".xlsx"} and data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 10_000:
                    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Office文件包含异常数量的内部文件")
                expanded = sum(info.file_size for info in infos)
                compressed = max(sum(info.compress_size for info in infos), 1)
                if expanded > 300 * 1024 * 1024 or expanded / compressed > 1000:
                    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Office文件解压规模异常")
                names = {info.filename for info in infos}
        except zipfile.BadZipFile:
            names = set()
        expected = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
        if expected in names and "[Content_Types].xml" in names:
            return (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if suffix == ".docx"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "文件实际内容与扩展名不一致或文件已损坏")


def _antivirus_scan(path: Path) -> None:
    settings = get_settings()
    if not settings.antivirus_required:
        return
    scanner = shutil.which("clamscan")
    if not scanner:
        path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "病毒扫描服务不可用")
    result = subprocess.run([scanner, "--no-summary", str(path)], capture_output=True, timeout=90)
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "文件未通过安全扫描")


def safe_filename(name: str) -> str:
    clean = re.sub(r"[^\w\-.（）()\u4e00-\u9fff]", "_", Path(name).name)
    return clean[:180] or "材料"


def _s3_client():
    settings = get_settings()
    if not settings.s3_bucket:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "对象存储桶尚未配置")
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def _s3_uri(key: str) -> str:
    settings = get_settings()
    return f"s3://{settings.s3_bucket}/{key}"


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    without_scheme = uri.removeprefix("s3://")
    bucket, key = without_scheme.split("/", 1)
    return bucket, key


async def save_upload(case_id: str, upload: UploadFile) -> dict:
    settings = get_settings()
    original_name = safe_filename(upload.filename or "材料")
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "仅支持PDF、图片和Office文件")
    data = await upload.read(settings.max_file_bytes + 1)
    if len(data) > settings.max_file_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "单个文件不能超过50MB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件为空")
    detected_mime = inspect_upload_bytes(data, suffix)
    digest = hashlib.sha256(data).hexdigest()
    directory = settings.storage_path / "originals" / case_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest[:16]}-{original_name}"
    target.write_bytes(data)
    _antivirus_scan(target)
    stored_path = str(target)
    if settings.storage_backend == "s3":
        key = f"originals/{case_id}/{target.name}"
        _s3_client().upload_file(
            str(target),
            settings.s3_bucket,
            key,
            ExtraArgs={"ContentType": detected_mime, "ServerSideEncryption": "AES256"},
        )
        stored_path = _s3_uri(key)
        target.unlink(missing_ok=True)
    return {
        "original_name": original_name,
        "stored_path": stored_path,
        "mime_type": detected_mime,
        "size_bytes": len(data),
        "sha256": digest,
    }


def delete_if_managed(path_value: str) -> None:
    settings = get_settings()
    if path_value.startswith("s3://"):
        bucket, key = _parse_s3_uri(path_value)
        if settings.storage_backend == "s3" and bucket == settings.s3_bucket:
            _s3_client().delete_object(Bucket=bucket, Key=key)
        return
    path = Path(path_value).resolve()
    try:
        path.relative_to(settings.storage_path)
    except ValueError:
        return
    if path.is_file():
        path.unlink()


def materialize_for_processing(path_value: str, case_id: str, item_id: str) -> Path:
    if not path_value.startswith("s3://"):
        return Path(path_value)
    bucket, key = _parse_s3_uri(path_value)
    suffix = Path(key).suffix
    target = get_settings().storage_path / "processing" / case_id / f"{item_id}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    _s3_client().download_file(bucket, key, str(target))
    return target


def persist_artifact(case_id: str, path: Path) -> str:
    settings = get_settings()
    if settings.storage_backend != "s3":
        return str(path)
    key = f"generated/{case_id}/{path.name}"
    _s3_client().upload_file(
        str(path),
        settings.s3_bucket,
        key,
        ExtraArgs={"ContentType": "application/octet-stream", "ServerSideEncryption": "AES256"},
    )
    path.unlink(missing_ok=True)
    return _s3_uri(key)


def presigned_download(path_value: str, filename: str) -> str:
    bucket, key = _parse_s3_uri(path_value)
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key, "ResponseContentDisposition": f'attachment; filename="{filename}"'},
        ExpiresIn=300,
    )
