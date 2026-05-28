"""
Object Storage Service
Direct-to-storage uploads for merch digital products.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

try:  # pragma: no cover - optional dependency in local dev
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # pragma: no cover
    boto3 = None
    BotoConfig = None


@dataclass(frozen=True)
class MultipartConfig:
    part_size_bytes: int
    small_file_threshold_bytes: int
    signed_url_expires_seconds: int


class ObjectStorageService:
    """S3-compatible storage helper used for merch digital product uploads."""

    @staticmethod
    def enabled() -> bool:
        from flask import current_app

        config = current_app.config
        return bool(
            boto3
            and config.get('OBJECT_STORAGE_BUCKET')
            and config.get('OBJECT_STORAGE_ACCESS_KEY_ID')
            and config.get('OBJECT_STORAGE_SECRET_ACCESS_KEY')
        )

    @staticmethod
    def _client():
        from flask import current_app

        if not ObjectStorageService.enabled():
            raise RuntimeError('Object storage is not configured.')

        config = current_app.config
        client_options = {
            'region_name': config.get('OBJECT_STORAGE_REGION') or 'us-east-1',
            'aws_access_key_id': config.get('OBJECT_STORAGE_ACCESS_KEY_ID'),
            'aws_secret_access_key': config.get('OBJECT_STORAGE_SECRET_ACCESS_KEY'),
        }
        if config.get('OBJECT_STORAGE_ENDPOINT_URL'):
            client_options['endpoint_url'] = config.get('OBJECT_STORAGE_ENDPOINT_URL')
        if BotoConfig is not None:
            client_options['config'] = BotoConfig(
                s3={
                    'addressing_style': 'path' if config.get('OBJECT_STORAGE_FORCE_PATH_STYLE') else 'virtual'
                }
            )
        return boto3.client('s3', **client_options)

    @staticmethod
    def get_settings() -> MultipartConfig:
        from flask import current_app

        return MultipartConfig(
            part_size_bytes=int(current_app.config.get('MERCH_UPLOAD_PART_SIZE_BYTES') or (16 * 1024 * 1024)),
            small_file_threshold_bytes=int(current_app.config.get('MERCH_UPLOAD_SMALL_FILE_THRESHOLD_BYTES') or (64 * 1024 * 1024)),
            signed_url_expires_seconds=int(current_app.config.get('OBJECT_STORAGE_SIGNED_URL_EXPIRES_SECONDS') or 600),
        )

    @staticmethod
    def bucket_name() -> str:
        from flask import current_app

        bucket = current_app.config.get('OBJECT_STORAGE_BUCKET')
        if not bucket:
            raise RuntimeError('OBJECT_STORAGE_BUCKET is not configured.')
        return bucket

    @staticmethod
    def public_url(key: str) -> str | None:
        from flask import current_app

        base_url = (current_app.config.get('OBJECT_STORAGE_PUBLIC_BASE_URL') or '').strip()
        if not base_url:
            return None
        return urljoin(base_url.rstrip('/') + '/', key.lstrip('/'))

    @staticmethod
    def generate_put_url(*, key: str, content_type: str, expires_in: int | None = None) -> str:
        client = ObjectStorageService._client()
        settings = ObjectStorageService.get_settings()
        return client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': ObjectStorageService.bucket_name(),
                'Key': key,
                'ContentType': content_type or 'application/octet-stream',
            },
            ExpiresIn=int(expires_in or settings.signed_url_expires_seconds),
        )

    @staticmethod
    def generate_get_url(*, key: str, expires_in: int | None = None) -> str:
        client = ObjectStorageService._client()
        settings = ObjectStorageService.get_settings()
        return client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': ObjectStorageService.bucket_name(),
                'Key': key,
            },
            ExpiresIn=int(expires_in or settings.signed_url_expires_seconds),
        )

    @staticmethod
    def create_multipart_upload(*, key: str, content_type: str) -> str:
        client = ObjectStorageService._client()
        response = client.create_multipart_upload(
            Bucket=ObjectStorageService.bucket_name(),
            Key=key,
            ContentType=content_type or 'application/octet-stream',
        )
        upload_id = response.get('UploadId')
        if not upload_id:
            raise RuntimeError('Unable to initialize multipart upload.')
        return upload_id

    @staticmethod
    def generate_part_url(*, key: str, upload_id: str, part_number: int, expires_in: int | None = None) -> str:
        client = ObjectStorageService._client()
        settings = ObjectStorageService.get_settings()
        return client.generate_presigned_url(
            'upload_part',
            Params={
                'Bucket': ObjectStorageService.bucket_name(),
                'Key': key,
                'UploadId': upload_id,
                'PartNumber': int(part_number),
            },
            ExpiresIn=int(expires_in or settings.signed_url_expires_seconds),
        )

    @staticmethod
    def complete_multipart_upload(*, key: str, upload_id: str, parts: list[dict]) -> dict:
        client = ObjectStorageService._client()
        return client.complete_multipart_upload(
            Bucket=ObjectStorageService.bucket_name(),
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts},
        )

    @staticmethod
    def head_object(*, key: str) -> dict:
        client = ObjectStorageService._client()
        return client.head_object(
            Bucket=ObjectStorageService.bucket_name(),
            Key=key,
        )

    @staticmethod
    def abort_multipart_upload(*, key: str, upload_id: str) -> None:
        client = ObjectStorageService._client()
        client.abort_multipart_upload(
            Bucket=ObjectStorageService.bucket_name(),
            Key=key,
            UploadId=upload_id,
        )
