"""
Cloudinary-backed media uploads with local fallback.
"""
from __future__ import annotations

import os

try:
    import cloudinary
    import cloudinary.uploader
except Exception:  # pragma: no cover - optional during local dev
    cloudinary = None


class CloudinaryService:
    """Uploads media to Cloudinary when configured."""

    @staticmethod
    def init_app(app):
        cloud_name = app.config.get("CLOUDINARY_CLOUD_NAME")
        api_key = app.config.get("CLOUDINARY_API_KEY")
        api_secret = app.config.get("CLOUDINARY_API_SECRET")
        enabled = bool(cloudinary and cloud_name and api_key and api_secret)
        app.extensions["cloudinary_enabled"] = enabled
        if not enabled:
            return

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )

    @staticmethod
    def enabled() -> bool:
        from flask import current_app

        return bool(current_app.extensions.get("cloudinary_enabled"))

    @staticmethod
    def upload(file_storage, folder: str, resource_type: str = "image") -> str | None:
        if not file_storage or not getattr(file_storage, "filename", ""):
            return None
        if not CloudinaryService.enabled():
            return None

        original_name = os.path.splitext(file_storage.filename or "")[0] or "upload"
        response = cloudinary.uploader.upload(
            file_storage,
            folder=folder,
            resource_type=resource_type,
            use_filename=True,
            unique_filename=True,
            overwrite=False,
            public_id=original_name,
        )
        return response.get("secure_url")
