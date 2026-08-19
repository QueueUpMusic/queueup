import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from ..models import UserProfile


PROFILE_PICTURE_MAX_BYTES = 5 * 1024 * 1024
PROFILE_PICTURE_FORMATS = {
    'JPEG': ('image/jpeg', '.jpg'),
    'PNG': ('image/png', '.png'),
    'GIF': ('image/gif', '.gif'),
    'WEBP': ('image/webp', '.webp'),
}
PROFILE_PICTURE_CONTENT_TYPE_EXTENSIONS = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/pjpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
}


def normalize_picture_bytes(filename, content_type, picture_bytes):
    """Identify the real image format and convert Apple HEIC/HEIF to JPEG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    try:
        with Image.open(io.BytesIO(picture_bytes)) as image:
            image_format = (image.format or '').upper()
            image.load()

            if image_format in {'HEIC', 'HEIF'}:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                if image.mode not in {'RGB', 'L'}:
                    if 'A' in image.getbands():
                        background = Image.new('RGB', image.size, 'white')
                        alpha = image.getchannel('A')
                        background.paste(image.convert('RGB'), mask=alpha)
                        image = background
                    else:
                        image = image.convert('RGB')
                elif image.mode == 'L':
                    image = image.convert('RGB')

                for quality in (90, 82, 74, 66):
                    output = io.BytesIO()
                    image.save(
                        output,
                        format='JPEG',
                        quality=quality,
                        optimize=True,
                    )
                    picture_bytes = output.getvalue()
                    if len(picture_bytes) <= PROFILE_PICTURE_MAX_BYTES:
                        break
                image_format = 'JPEG'
    except (UnidentifiedImageError, OSError, ValueError):
        return filename, content_type, picture_bytes

    normalized = PROFILE_PICTURE_FORMATS.get(image_format)
    if not normalized:
        return filename, content_type, picture_bytes

    normalized_type, normalized_extension = normalized
    base_name = filename.rsplit('.', 1)[0] if '.' in filename else filename
    return f'{base_name}{normalized_extension}', normalized_type, picture_bytes


def uploaded_profile_picture(
    filename,
    content_type,
    picture_bytes,
    add_content_type_extension=False,
):
    """Build the normalized uploaded file consumed by the profile form."""
    filename, content_type, picture_bytes = normalize_picture_bytes(
        filename,
        content_type,
        picture_bytes,
    )
    if add_content_type_extension and '.' not in filename:
        filename += PROFILE_PICTURE_CONTENT_TYPE_EXTENSIONS.get(
            content_type.lower(),
            '',
        )
    return SimpleUploadedFile(
        filename,
        picture_bytes,
        content_type=content_type,
    )


def validate_profile_picture(picture):
    """Apply the existing profile-picture size and image-format rules."""
    if picture.size > PROFILE_PICTURE_MAX_BYTES:
        raise ValidationError('Please choose an image smaller than 5 MB.')

    image_format = getattr(
        getattr(picture, 'image', None),
        'format',
        '',
    ).upper()
    if image_format not in PROFILE_PICTURE_FORMATS:
        raise ValidationError(
            'Please choose a JPG/JPEG, HEIC/HEIF, PNG, GIF, or WebP image.'
        )
    return picture


def replace_profile_picture(user, picture):
    """Store a new profile picture and delete the replaced stored file."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    old_picture = profile.picture
    profile.picture = picture
    profile.save(update_fields=['picture', 'updated_at'])
    if old_picture and old_picture.name != profile.picture.name:
        old_picture.delete(save=False)
    return profile


def remove_profile_picture(user):
    """Delete and clear a user's stored profile picture if one exists."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if not profile.picture:
        return False
    profile.picture.delete(save=False)
    profile.picture = ''
    profile.save(update_fields=['picture', 'updated_at'])
    return True
