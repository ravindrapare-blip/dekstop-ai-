"""Which folder a file belongs in, from its name alone (never its contents).

Work       spreadsheets, presentations, code and data files, and documents named like work (meeting, project, client, ...)
Documents  PDFs, Word and text documents, e-books
Media      audio and video
Images     pictures and design files
Personal   anything named like personal paperwork (resume, passport, tax, insurance, ...), calendar and contact cards
Other      everything else (archives, installers, unknown types)

The lists are plain data so they are easy to read and change.
"""
import re
import unicodedata
from dataclasses import dataclass

CATEGORIES = ("Work", "Documents", "Media", "Images", "Personal", "Other")

EXTENSIONS = {
    "Work": {"xls", "xlsx", "xlsm", "csv", "tsv", "numbers", "ods", "ppt", "pptx", "key", "odp",
             "py", "js", "ts", "jsx", "tsx", "java", "kt", "c", "cc", "cpp", "h", "hpp", "m", "swift", "go", "rs",
             "rb", "php", "json", "xml", "yaml", "yml", "toml", "sql", "ipynb", "html", "css"},
    "Documents": {"pdf", "doc", "docx", "pages", "odt", "rtf", "txt", "md", "tex", "epub", "mobi"},
    "Media": {"mp3", "m4a", "aac", "wav", "flac", "aiff", "aif", "ogg", "opus", "wma",
              "mp4", "mov", "m4v", "avi", "mkv", "webm", "wmv", "mpg", "mpeg", "3gp"},
    "Images": {"png", "jpg", "jpeg", "gif", "heic", "heif", "webp", "bmp", "tif", "tiff", "svg", "psd", "ai",
               "raw", "cr2", "nef", "arw", "dng", "ico", "icns"},
    "Personal": {"ics", "vcf"},
    "Other": {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "dmg", "pkg", "iso"},
}
BY_EXTENSION = {ext: cat for cat, exts in EXTENSIONS.items() for ext in exts}

PERSONAL_WORDS = {"resume", "cv", "passport", "visa", "tax", "taxes", "insurance", "medical", "health", "bank",
                  "statement", "receipt", "birth", "wedding", "family", "vacation", "holiday", "mortgage", "lease",
                  "will", "prescription", "diary", "journal"}
WORK_WORDS = {"project", "meeting", "agenda", "client", "proposal", "budget", "quarterly", "minutes",
              "roadmap", "invoice", "contract", "presentation"}
OVERRIDABLE = {"Documents", "Work", "Images", "Other"}      # media files keep their folder whatever they are called

# Files that are not finished, or that belong to the system or an app: never touched.
PARTIAL_SUFFIXES = (".crdownload", ".download", ".part", ".partial", ".tmp", ".temp", ".opdownload", ".icloud")
SYSTEM_NAMES = {"icon\r", "desktop.ini", "thumbs.db", ".ds_store", ".localized"}


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def extension_of(name: str) -> str:
    return name.rsplit(".", 1)[1].lower() if "." in name.strip(".") and not name.endswith(".") else ""


def words_of(name: str) -> set[str]:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)               # "TaxReturn" -> "Tax Return"
    return {w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w}


def categorize(name: str) -> str:
    category = BY_EXTENSION.get(extension_of(name), "Other")
    if category in OVERRIDABLE:
        words = words_of(name)
        if words & PERSONAL_WORDS:
            return "Personal"
        if category == "Documents" and words & WORK_WORDS:
            return "Work"
    return category


def is_hidden_or_system(name: str) -> bool:
    lower = name.lower()
    return (name.startswith(".") or lower in SYSTEM_NAMES or name.startswith("~$") or name.endswith("~")
            or lower.endswith(PARTIAL_SUFFIXES))


# ---- "only PDFs", "just images and videos" -------------------------------------------------------------------------

@dataclass(frozen=True)
class Filter:
    label: str
    categories: frozenset
    extensions: frozenset

    def matches(self, name: str, category: str) -> bool:
        return category in self.categories or extension_of(name) in self.extensions


_CATEGORY_WORDS = {
    "work": "Work", "documents": "Documents", "document": "Documents", "docs": "Documents",
    "images": "Images", "image": "Images", "pictures": "Images", "picture": "Images", "photos": "Images",
    "photo": "Images", "pics": "Images", "screenshots": "Images", "screenshot": "Images",
    "media": "Media", "personal": "Personal", "other": "Other",
}
_GROUP_WORDS = {
    "pdf": {"pdf"}, "pdfs": {"pdf"}, "word": {"doc", "docx"}, "text": {"txt"}, "spreadsheets": {"xls", "xlsx", "csv", "numbers", "ods"},
    "spreadsheet": {"xls", "xlsx", "csv", "numbers", "ods"}, "presentations": {"ppt", "pptx", "key", "odp"},
    "presentation": {"ppt", "pptx", "key", "odp"}, "slides": {"ppt", "pptx", "key", "odp"},
    "videos": {"mp4", "mov", "m4v", "avi", "mkv", "webm", "wmv", "mpg", "mpeg", "3gp"}, "video": {"mp4", "mov", "m4v", "avi", "mkv", "webm"},
    "movies": {"mp4", "mov", "m4v", "avi", "mkv", "webm"}, "music": {"mp3", "m4a", "aac", "wav", "flac", "aiff", "ogg"},
    "audio": {"mp3", "m4a", "aac", "wav", "flac", "aiff", "ogg"}, "songs": {"mp3", "m4a"},
    "archives": {"zip", "rar", "7z", "tar", "gz", "tgz"}, "zips": {"zip"}, "installers": {"dmg", "pkg"},
}
_FILLER = {"and", "or", "the", "my", "files", "file", "all", "only", "just", "of", "type", "types", "kind", "kinds", "some", "any"}


def parse_only(text: str) -> Filter:
    """"PDFs" -> every .pdf. "images and videos" -> the Images category plus video types. Raises ValueError if a word is not understood."""
    categories, extensions, unknown, labels = set(), set(), [], []
    for token in re.split(r"[^a-z0-9.+]+", str(text).lower()):
        token = token.strip(".")
        if not token or token in _FILLER:
            continue
        if token in _CATEGORY_WORDS:
            categories.add(_CATEGORY_WORDS[token])
        elif token in _GROUP_WORDS:
            extensions |= _GROUP_WORDS[token]
        elif token.rstrip("s") in BY_EXTENSION or token in BY_EXTENSION:
            extensions.add(token if token in BY_EXTENSION else token.rstrip("s"))
        else:
            unknown.append(token)
            continue
        labels.append(token)
    if unknown or not labels:
        shown = " ".join(unknown) or str(text).strip()[:40]
        raise ValueError(f"I don't know what kind of files “{shown}” means. Try PDFs, images, videos, music, spreadsheets, or a type like docx.")
    return Filter(" and ".join(labels), frozenset(categories), frozenset(extensions))
