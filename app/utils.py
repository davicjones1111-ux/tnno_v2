"""
Utility Functions
Helper functions for the RetroQuest Platform
"""
import os
import io
import mimetypes
import random
import string
import zipfile
from datetime import timedelta
from pathlib import Path
from secrets import token_hex
from werkzeug.utils import secure_filename
from flask import current_app, send_file
try:
    from PIL import Image, UnidentifiedImageError
except Exception:  # pragma: no cover - optional dependency during local dev
    Image = None

    class UnidentifiedImageError(Exception):
        pass
from app.models import User
from app.datetime_utils import utc_now
from app.security import log_security_event
from app.services.cloudinary_service import CloudinaryService


# Allowed file extensions for uploads
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'avif', 'jfif', 'tiff', 'tif'}
IMAGE_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'avif', 'jfif', 'tiff', 'tif'}
MAX_IMAGE_UPLOAD_BYTES = 2 * 1024 * 1024
DANGEROUS_UPLOAD_EXTENSIONS = {
    'app', 'bat', 'bin', 'cmd', 'com', 'cpl', 'dll', 'dmg', 'exe', 'hta', 'iso',
    'jar', 'js', 'jse', 'lib', 'lnk', 'msi', 'msp', 'php', 'pl', 'ps1', 'py', 'rb',
    'scr', 'sh', 'so', 'svg', 'vb', 'vbe', 'vbs', 'wsf'
}
GENERIC_MIME_TYPES = {'application/octet-stream', 'binary/octet-stream'}
MIME_ALLOWLIST = {
    'png': {'image/png'},
    'jpg': {'image/jpeg'},
    'jpeg': {'image/jpeg'},
    'gif': {'image/gif'},
    'bmp': {'image/bmp', 'image/x-ms-bmp'},
    'webp': {'image/webp'},
    'avif': {'image/avif'},
    'jfif': {'image/jpeg'},
    'tif': {'image/tiff'},
    'tiff': {'image/tiff'},
    'pdf': {'application/pdf'},
    'zip': {'application/zip', 'application/x-zip-compressed'},
    'rar': {'application/vnd.rar', 'application/x-rar-compressed'},
    '7z': {'application/x-7z-compressed'},
    'txt': {'text/plain'},
    'csv': {'text/csv', 'application/csv', 'text/plain'},
    'rtf': {'application/rtf', 'text/rtf'},
    'doc': {'application/msword', 'application/vnd.ms-office'},
    'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    'xls': {'application/vnd.ms-excel', 'application/vnd.ms-office'},
    'xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
    'ppt': {'application/vnd.ms-powerpoint', 'application/vnd.ms-office'},
    'pptx': {'application/vnd.openxmlformats-officedocument.presentationml.presentation'},
    'mp3': {'audio/mpeg'},
    'wav': {'audio/wav', 'audio/x-wav', 'audio/wave'},
    'm4a': {'audio/mp4', 'audio/x-m4a'},
    'mp4': {'video/mp4'},
    'mov': {'video/quicktime', 'video/mp4'},
    'webm': {'video/webm'},
    'avi': {'video/x-msvideo'},
}


def allowed_file(filename):
    """Check if file has allowed extension"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_random_filename(extension: str) -> str:
    """Generate a random server-side filename with the original extension."""
    safe_ext = (extension or '').lower().lstrip('.')
    if not safe_ext:
        raise ValueError('Missing file extension.')
    return f'{token_hex(16)}.{safe_ext}'


def _normalize_subfolder(subfolder: str) -> str:
    raw = str(subfolder or '').strip().replace('\\', '/').strip('/')
    if not raw:
        return ''
    parts = [part for part in raw.split('/') if part not in {'', '.'}]
    if any(part == '..' for part in parts):
        raise ValueError('Invalid upload path.')
    return '/'.join(parts)


def get_upload_root() -> Path:
    upload_folder = current_app.config.get('UPLOAD_FOLDER') or os.path.join(current_app.static_folder, 'uploads')
    if not os.path.isabs(upload_folder):
        upload_folder = os.path.join(current_app.root_path, upload_folder)
    return Path(upload_folder).resolve()


def get_upload_directory(subfolder: str = '') -> Path:
    root = get_upload_root()
    normalized = _normalize_subfolder(subfolder)
    directory = root / normalized if normalized else root
    directory = directory.resolve()
    if directory != root and root not in directory.parents:
        raise ValueError('Invalid upload path.')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_upload_path(filename: str, subfolder: str = '') -> Path:
    if not filename:
        raise ValueError('Missing filename.')
    if os.path.isabs(filename) or '/' in filename or '\\' in filename:
        raise ValueError('Unsafe filename.')
    safe_name = secure_filename(filename)
    if safe_name != filename:
        raise ValueError('Unsafe filename.')
    directory = get_upload_directory(subfolder)
    path = (directory / filename).resolve()
    if directory not in path.parents:
        raise ValueError('Invalid upload path.')
    return path


def send_uploaded_file(filename: str, subfolder: str = '', *, download_name: str | None = None):
    """Serve a stored upload from a validated local path."""
    path = resolve_upload_path(filename, subfolder=subfolder)
    return send_file(path, as_attachment=True, download_name=download_name or path.name)


def _read_upload_bytes(file, *, max_bytes: int | None = None) -> bytes:
    file.stream.seek(0)
    data = file.read()
    file.stream.seek(0)
    if not data:
        raise ValueError('Empty file upload.')
    if max_bytes and len(data) > max_bytes:
        raise ValueError(f'File must be {max_bytes // (1024 * 1024)}MB or smaller.')
    return data


def _is_probably_text_bytes(data: bytes) -> bool:
    sample = data[:4096]
    if b'\x00' in sample:
        return False
    try:
        sample.decode('utf-8')
        return True
    except UnicodeDecodeError:
        printable = sum(
            1 for byte in sample
            if byte in b'\t\n\r' or 32 <= byte <= 126
        )
        return printable / max(len(sample), 1) >= 0.85


def _zip_contains_prefix(data: bytes, prefix: str) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return any(name.startswith(prefix) for name in archive.namelist())
    except zipfile.BadZipFile:
        return False


def _is_ole_document(data: bytes) -> bool:
    return data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')


def _looks_like_mp4_family(data: bytes) -> bool:
    return len(data) > 12 and data[4:8] == b'ftyp'


def _has_executable_signature(data: bytes) -> bool:
    signatures = (
        b'MZ',            # Windows PE
        b'\x7fELF',       # Linux ELF
        b'\xcf\xfa\xed\xfe',  # Mach-O
        b'\xfe\xed\xfa\xcf',  # Mach-O
    )
    if data.startswith(signatures):
        return True
    if data.startswith(b'#!'):
        return True
    return False


def _matches_extension_signature(extension: str, data: bytes) -> bool:
    ext = (extension or '').lower()
    if ext in {'png'}:
        return data.startswith(b'\x89PNG\r\n\x1a\n')
    if ext in {'jpg', 'jpeg', 'jfif'}:
        return data.startswith(b'\xff\xd8\xff')
    if ext == 'gif':
        return data[:6] in {b'GIF87a', b'GIF89a'}
    if ext == 'bmp':
        return data.startswith(b'BM')
    if ext == 'webp':
        return data.startswith(b'RIFF') and data[8:12] == b'WEBP'
    if ext == 'avif':
        return _looks_like_mp4_family(data) and data[8:12] in {b'avif', b'avis'}
    if ext in {'tif', 'tiff'}:
        return data.startswith((b'II*\x00', b'MM\x00*'))
    if ext == 'pdf':
        return data.startswith(b'%PDF')
    if ext == 'zip':
        return zipfile.is_zipfile(io.BytesIO(data))
    if ext == 'docx':
        return zipfile.is_zipfile(io.BytesIO(data)) and _zip_contains_prefix(data, 'word/')
    if ext == 'xlsx':
        return zipfile.is_zipfile(io.BytesIO(data)) and _zip_contains_prefix(data, 'xl/')
    if ext == 'pptx':
        return zipfile.is_zipfile(io.BytesIO(data)) and _zip_contains_prefix(data, 'ppt/')
    if ext == 'rar':
        return data.startswith((b'Rar!\x1a\x07\x00', b'Rar!\x1a\x07\x01\x00'))
    if ext == '7z':
        return data.startswith(b"7z\xbc\xaf'\x1c")
    if ext in {'txt', 'csv'}:
        return _is_probably_text_bytes(data)
    if ext == 'rtf':
        return data.startswith(b'{\\rtf')
    if ext in {'doc', 'xls', 'ppt'}:
        return _is_ole_document(data)
    if ext == 'mp3':
        return data.startswith(b'ID3') or (len(data) > 2 and data[0] == 0xff and (data[1] & 0xe0) == 0xe0)
    if ext == 'wav':
        return data.startswith(b'RIFF') and data[8:12] == b'WAVE'
    if ext == 'm4a':
        return _looks_like_mp4_family(data) and data[8:12] in {b'M4A ', b'M4B ', b'isom', b'mp42'}
    if ext in {'mp4', 'mov'}:
        return _looks_like_mp4_family(data)
    if ext == 'webm':
        return data.startswith(b'\x1aE\xdf\xa3')
    if ext == 'avi':
        return data.startswith(b'RIFF') and data[8:12] == b'AVI '
    return False


def _client_mime_allowed(extension: str, file) -> bool:
    allowed_mimes = MIME_ALLOWLIST.get(extension, set())
    if not allowed_mimes:
        return True

    reported = (getattr(file, 'mimetype', '') or '').lower().strip()
    guessed = (mimetypes.guess_type(file.filename or '')[0] or '').lower().strip()

    candidates = {mime for mime in {reported, guessed} if mime}
    if not candidates:
        return True
    if candidates & allowed_mimes:
        return True
    if candidates <= GENERIC_MIME_TYPES:
        return True
    return False


def _validate_upload(file, *, allowed_exts=None, max_bytes: int | None = None):
    if not file or not file.filename:
        raise ValueError('Missing upload file.')

    filename = secure_filename(file.filename)
    if not filename or '.' not in filename:
        raise ValueError('Please choose a file with a valid extension.')

    extension = filename.rsplit('.', 1)[1].lower()
    if extension in DANGEROUS_UPLOAD_EXTENSIONS:
        log_security_event('dangerous_upload_extension', 'Blocked executable upload', filename=filename)
        raise ValueError('Executable or script uploads are not allowed.')

    normalized_allowed = {str(item).lower() for item in (allowed_exts or set())} if allowed_exts is not None else None
    if normalized_allowed is not None and extension not in normalized_allowed:
        return None, extension

    data = _read_upload_bytes(file, max_bytes=max_bytes)
    if _has_executable_signature(data):
        log_security_event('dangerous_upload_signature', 'Blocked executable upload signature', filename=filename)
        raise ValueError('Executable or script uploads are not allowed.')

    if not _client_mime_allowed(extension, file):
        log_security_event('upload_mime_mismatch', 'Blocked upload with invalid MIME type', filename=filename)
        raise ValueError('Uploaded file type does not match its extension.')

    if not _matches_extension_signature(extension, data):
        log_security_event('upload_signature_mismatch', 'Blocked upload with invalid signature', filename=filename)
        raise ValueError('Uploaded file failed file type validation.')

    return data, extension


def _upload_to_cloudinary(file, *, folder: str, resource_type: str):
    file.stream.seek(0)
    remote_url = CloudinaryService.upload(file, folder=folder, resource_type=resource_type)
    file.stream.seek(0)
    return remote_url


def generate_random_code(length=8):
    """Generate random alphanumeric code"""
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(length))


def generate_unique_6digit_id():
    """Generate a unique 6-digit user ID"""
    from app.extensions import db
    from app.models import User
    
    while True:
        # Generate random 6-digit number
        user_6digit = str(random.randint(100000, 999999))
        
        # Check if it already exists
        existing = User.query.filter_by(user_6digit=user_6digit).first()
        if not existing:
            return user_6digit


def save_uploaded_file(file, subfolder='', *, allow_remote=True):
    """
    Save uploaded file to static/uploads directory
    Returns the relative path from static folder for use in templates
    """
    if not file or not file.filename or not allowed_file(file.filename):
        return None

    validated = _validate_upload(
        file,
        allowed_exts=ALLOWED_EXTENSIONS,
        max_bytes=int(current_app.config.get('MAX_CONTENT_LENGTH') or (10 * 1024 * 1024)),
    )
    if validated[0] is None:
        return None
    data, ext = validated

    cloudinary_folder = current_app.config.get('CLOUDINARY_UPLOAD_FOLDER') or 'retroquest'
    if allow_remote:
        remote_url = _upload_to_cloudinary(
            file,
            folder=f'{cloudinary_folder}/{subfolder or "misc"}',
            resource_type='auto',
        )
        if remote_url:
            return remote_url

    filename = generate_random_filename(ext)
    filepath = resolve_upload_path(filename, subfolder=subfolder)
    with open(filepath, 'wb') as handle:
        handle.write(data)

    normalized_subfolder = _normalize_subfolder(subfolder)
    if normalized_subfolder:
        return f'uploads/{normalized_subfolder}/{filename}'
    return f'uploads/{filename}'


def save_uploaded_image_optimized(file, subfolder='posts', max_bytes=MAX_IMAGE_UPLOAD_BYTES):
    """
    Save a compressed image under static/uploads/<subfolder>.
    - validates common image uploads
    - enforces max upload size
    - deduplicates by content hash
    """
    if not file or not file.filename:
        return None

    validated = _validate_upload(file, allowed_exts=IMAGE_ALLOWED_EXTENSIONS, max_bytes=max_bytes)
    if validated[0] is None:
        raise ValueError('Please upload a valid image file.')
    raw, _ = validated

    cloudinary_folder = current_app.config.get('CLOUDINARY_UPLOAD_FOLDER') or 'retroquest'
    remote_url = _upload_to_cloudinary(
        file,
        folder=f'{cloudinary_folder}/{subfolder}',
        resource_type='image',
    )
    if remote_url:
        return remote_url

    safe_name = secure_filename(file.filename)
    ext = safe_name.rsplit('.', 1)[-1].lower() if '.' in safe_name else ''
    if ext and ext not in IMAGE_ALLOWED_EXTENSIONS:
        raise ValueError('Please upload a valid image file.')

    if Image is None:
        saved_path = save_uploaded_file(file, subfolder)
        if not saved_path:
            raise ValueError('Please upload a valid image file.')
        return saved_path

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise ValueError('Please upload a valid image file.')

    # Fit into a sane resolution to reduce storage and transfer cost.
    image.thumbnail((1920, 1920))

    has_alpha = 'A' in image.getbands() if hasattr(image, 'getbands') else False
    if not has_alpha and image.mode in ('P', 'LA'):
        image = image.convert('RGBA')
        has_alpha = True

    out_ext = 'png' if has_alpha else 'jpg'
    out_name = generate_random_filename(out_ext)
    out_path = resolve_upload_path(out_name, subfolder=subfolder)

    if out_ext == 'png':
        if image.mode not in ('RGB', 'RGBA'):
            image = image.convert('RGBA')
        image.save(out_path, format='PNG', optimize=True)
    else:
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        quality = 85
        while True:
            buf = io.BytesIO()
            image.save(buf, format='JPEG', optimize=True, quality=quality, progressive=True)
            data = buf.getvalue()
            if len(data) <= max_bytes or quality <= 55:
                with open(out_path, 'wb') as f:
                    f.write(data)
                break
            quality -= 5

    if os.path.getsize(out_path) > max_bytes:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise ValueError('Image is too large after compression. Please upload a smaller file.')

    normalized_subfolder = _normalize_subfolder(subfolder)
    return f'uploads/{normalized_subfolder}/{out_name}' if normalized_subfolder else f'uploads/{out_name}'


def save_uploaded_file_any(file, subfolder='', allowed_exts=None, *, allow_remote=True):
    """Save uploaded file with a custom allowlist of extensions."""
    if not file or not file.filename:
        return None
    normalized_allowed = {str(item).lower() for item in (allowed_exts or set())} if allowed_exts is not None else None
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if normalized_allowed is not None and ext not in normalized_allowed:
        return None

    validated = _validate_upload(
        file,
        allowed_exts=normalized_allowed,
        max_bytes=int(current_app.config.get('MAX_CONTENT_LENGTH') or (10 * 1024 * 1024)),
    )
    if validated[0] is None:
        return None
    data, ext = validated

    cloudinary_folder = current_app.config.get('CLOUDINARY_UPLOAD_FOLDER') or 'retroquest'
    if allow_remote:
        remote_url = _upload_to_cloudinary(
            file,
            folder=f'{cloudinary_folder}/{subfolder or "misc"}',
            resource_type='raw',
        )
        if remote_url:
            return remote_url

    filename = generate_random_filename(ext)
    filepath = resolve_upload_path(filename, subfolder=subfolder)
    with open(filepath, 'wb') as handle:
        handle.write(data)

    normalized_subfolder = _normalize_subfolder(subfolder)
    if normalized_subfolder:
        return f'uploads/{normalized_subfolder}/{filename}'
    return f'uploads/{filename}'


def get_current_user():
    """Get current logged in user from session"""
    from flask_login import current_user
    if current_user.is_authenticated:
        return current_user
    return None


def is_admin(user):
    """Check if user is admin"""
    if not user:
        return False
    admin_username = current_app.config.get('ADMIN_USER', 'admin')
    return user.username == admin_username or user.role == 'admin'


def format_datetime(dt):
    """Format datetime to readable string"""
    if not dt:
        return ''
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def format_datetime_ago(dt):
    """Format datetime as 'ago' string"""
    if not dt:
        return ''
    
    now = utc_now()
    diff = now - dt
    
    if diff.days > 365:
        return f"{diff.days // 365} year(s) ago"
    elif diff.days > 30:
        return f"{diff.days // 30} month(s) ago"
    elif diff.days > 0:
        return f"{diff.days} day(s) ago"
    elif diff.seconds > 3600:
        return f"{diff.seconds // 3600} hour(s) ago"
    elif diff.seconds > 60:
        return f"{diff.seconds // 60} minute(s) ago"
    else:
        return "just now"


def count_words(text: str) -> int:
    """Count words in a string."""
    if not text:
        return 0
    return len([w for w in text.strip().split() if w])


def calculate_deadline(hours=24):
    """Calculate deadline datetime"""
    return utc_now() + timedelta(hours=hours)


def paginate_query(query, page=1, per_page=20):
    """Paginate SQLAlchemy query"""
    return query.paginate(page=page, per_page=per_page, error_out=False)


def get_user_stats(user_id):
    """Get user statistics"""
    from app.models import UserMission, Post, Deposit, WithdrawRequest
    
    completed_missions = UserMission.query.filter_by(
        user_id=user_id, 
        status='completed'
    ).count()
    
    total_posts = Post.query.filter_by(user_id=user_id).count()
    total_deposits = Deposit.query.filter(
        Deposit.user_id == user_id,
        Deposit.status.in_(['success', 'completed'])
    ).count()
    total_withdraws = WithdrawRequest.query.filter_by(
        user_id=user_id, 
        status='approved'
    ).count()
    
    return {
        'completed_missions': completed_missions,
        'total_posts': total_posts,
        'total_deposits': total_deposits,
        'total_withdraws': total_withdraws
    }


def generate_qr_code(data):
    """Generate QR code for deposit address"""
    from io import BytesIO
    import base64
    try:
        import qrcode

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        img_base64 = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/png;base64,{img_base64}"
    except Exception:
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='320' height='320' viewBox='0 0 320 320'>"
            "<rect width='320' height='320' fill='white'/>"
            "<rect x='16' y='16' width='288' height='288' fill='none' stroke='#4f6fb4' stroke-width='8'/>"
            "<text x='160' y='120' text-anchor='middle' font-family='monospace' font-size='22' fill='#2d3b55'>QR Unavailable</text>"
            "<text x='160' y='160' text-anchor='middle' font-family='monospace' font-size='15' fill='#60708e'>Copy the wallet address</text>"
            "<text x='160' y='188' text-anchor='middle' font-family='monospace' font-size='15' fill='#60708e'>and exact amount below.</text>"
            "</svg>"
        )
        encoded = base64.b64encode(svg.encode('utf-8')).decode('ascii')
        return f"data:image/svg+xml;base64,{encoded}"


def get_leaderboard(limit=10, game_id='emperors_circle'):
    """Get leaderboard rankings"""
    from app.models import GameScore, User
    
    scores = GameScore.query.filter_by(game_id=game_id)\
        .order_by(GameScore.score.desc())\
        .limit(limit)\
        .all()
    
    leaderboard = []
    for rank, score in enumerate(scores, 1):
        leaderboard.append({
            'rank': rank,
            'user_id': score.user_id,
            'username': score.user.username if score.user else 'Unknown',
            'score': score.score
        })
    
    return leaderboard
