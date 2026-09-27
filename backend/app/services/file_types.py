"""Какие файлы CRM принимает и каким типом их считает.

Отдельным модулем, потому что это данные, а не логика: список правят чаще,
чем код вокруг, и он нужен и загрузке, и шлюзу.
"""

# Расширение → MIME. Тип назначает сервер: заявленный браузером не проверяется
# вовсе — его легко подделать, а по нему потом решается, показывать ли файл
# в браузере. Исполняемые и «активные» форматы (exe, sh, js, html, svg…) сюда
# не входят: в CRM и клиенту они не нужны, а вреда могут наделать.
EXTENSION_MIME: dict[str, str] = {
    # картинки
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "heic": "image/heic",
    "heif": "image/heif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    # видео
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "m4v": "video/x-m4v",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    "3gp": "video/3gpp",
    # аудио
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    # документы
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "rtf": "application/rtf",
    "txt": "text/plain",
    "csv": "text/csv",
    "epub": "application/epub+zip",
    "fb2": "application/x-fictionbook+xml",
    "djvu": "image/vnd.djvu",
    # архивы
    "zip": "application/zip",
    "rar": "application/vnd.rar",
    "7z": "application/x-7z-compressed",
}
ALLOWED_EXTENSIONS = frozenset(EXTENSION_MIME)

IMAGE_EXTENSIONS = frozenset(
    {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif", "bmp", "tif", "tiff"}
)
VIDEO_EXTENSIONS = frozenset({"mp4", "mov", "m4v", "webm", "mkv", "avi", "3gp"})
AUDIO_EXTENSIONS = frozenset({"mp3", "m4a", "aac", "ogg", "oga", "opus", "wav", "flac"})
