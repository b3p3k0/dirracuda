"""Authored directory clues for the shipped Dorkbook collection.

Candidate IDs match docs/dev/dorkbook/CANDIDATE_DORKS.md. This module holds
data only; models builds the two explicitly supported query forms from it.
Research and limitations are recorded in docs/dev/dorkbook/CATALOG_RESEARCH.md.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectoryIdea:
    candidate_id: int
    key: str
    nickname: str
    topic: str
    clue: str
    notes: str


DIRECTORY_IDEAS = (
    DirectoryIdea(
        1, "ebook_folders", "Books — ebook folders", "Books", "ebooks",
        "Looks for ebook-labelled directories. Try replacing ebooks with books or an author's name.",
    ),
    DirectoryIdea(
        2, "epub", "Books — EPUB", "Books", ".epub",
        "Looks for EPUB filenames in a directory page. Add an author or subject to explore further.",
    ),
    DirectoryIdea(
        3, "kindle", "Books — Kindle formats", "Books", ".mobi",
        "Starts with MOBI filenames. Try replacing .mobi with .azw3 for another ebook format.",
    ),
    DirectoryIdea(
        4, "pdf", "Books — PDF collections", "Books", ".pdf",
        "A broad PDF starting point. Add a subject, or try a clue such as manuals or magazines.",
    ),
    DirectoryIdea(
        5, "comics", "Comics and manga — CBZ", "Books", ".cbz",
        "Looks for comic archive filenames. Try .cbr or comics in place of .cbz.",
    ),
    DirectoryIdea(
        6, "audiobooks", "Audiobooks — M4B", "Books", ".m4b",
        "Looks for M4B audio files. Try audiobooks to find folders that use other audio formats.",
    ),
    DirectoryIdea(
        7, "movies", "Movies — folders", "Video", "movies",
        "Looks for movie-labelled directories. Try films, a genre, or a release year.",
    ),
    DirectoryIdea(
        8, "tv_series", "TV shows — series folders", "Video", "series",
        "Uses series as a folder clue. Try season, tv, or a show's name.",
    ),
    DirectoryIdea(
        9, "video_mkv", "Video — MKV", "Video", ".mkv",
        "Looks for MKV filenames across video types. Try .mp4 or .avi for different formats.",
    ),
    DirectoryIdea(
        10, "documentaries", "Documentaries — folders", "Video", "documentaries",
        "Looks for documentary-labelled directories. Try documentary or a subject you enjoy.",
    ),
    DirectoryIdea(
        12, "music_folders", "Music — folders", "Music", "music",
        "Looks for music-labelled directories. Try albums, an artist, or a genre.",
    ),
    DirectoryIdea(
        13, "flac", "Music — FLAC", "Music", ".flac",
        "Starts with FLAC filenames for lossless audio. Add an artist or album clue.",
    ),
    DirectoryIdea(
        14, "mp3", "Music and audio — MP3", "Music", ".mp3",
        "Looks for MP3 filenames, including music or speech. Add a performer or topic to narrow it.",
    ),
    DirectoryIdea(
        19, "photos", "Photos — folders", "Images & design", "photos",
        "Looks for photo-labelled directories. Try pictures, albums, or DCIM as another folder clue.",
    ),
    DirectoryIdea(
        20, "images_jpg", "Images — JPEG", "Images & design", ".jpg",
        "Looks for JPEG filenames. Try .jpeg or .png; directory icons can also match image clues.",
    ),
    DirectoryIdea(
        21, "wallpapers", "Wallpapers and artwork — folders", "Images & design", "wallpapers",
        "Starts with wallpaper-labelled directories. Try artwork, illustrations, or a subject.",
    ),
    DirectoryIdea(
        22, "design_svg", "Design assets — SVG", "Images & design", ".svg",
        "Looks for vector artwork filenames. Try .eps, icons, or fonts for related design assets.",
    ),
    DirectoryIdea(
        26, "iso", "OS images — ISO", "Software & games", ".iso",
        "Looks for disc image filenames. Add linux or a distribution name to focus on OS images.",
    ),
    DirectoryIdea(
        27, "software_mirrors", "Software and source — mirrors", "Software & games", "releases",
        "Starts with release-labelled directories. Try packages, source, or .tar.gz for other archives.",
    ),
    DirectoryIdea(
        28, "game_mods", "Game mods and assets — folders", "Software & games", "mods",
        "Looks for mod-labelled directories. Try maps, textures, sprites, or a game's name.",
    ),
    DirectoryIdea(
        29, "models_stl", "CAD and 3D models — STL", "3D & CAD", ".stl",
        "Starts with STL model filenames. Try .obj, .step, or .dxf to explore other model formats.",
    ),
    DirectoryIdea(
        32, "downloads", "Public downloads — folders", "General", "downloads",
        "A deliberately broad download-folder clue. Try public or add a subject that interests you.",
    ),
)
