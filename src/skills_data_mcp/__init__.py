"""
Resources MCP Server

Provides persistent file storage with versioning.
Files are stored flat at /data/resources/{filename} with automatic version history.

Versioning rules:
- Max 10 versions per file
- Same-day edits overwrite the current version (no new version created)
- Edits on a new day create a new version
- Oldest versions are dropped when the cap is reached
"""

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_DIR = Path(os.environ.get("RESOURCES_DATA_DIR",
                os.environ.get("SKILLS_DATA_DIR", "/data/resources")))

mcp = FastMCP("resources")

MAX_VERSIONS = 10

# Allow letters, digits, hyphens, underscores, dots — no path traversal
_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _validate_filename(filename: str) -> None:
    if not _SAFE_FILENAME.match(filename) or ".." in filename:
        raise ValueError(f"Invalid filename: {filename!r}. Use only letters, digits, hyphens, underscores, dots.")
    if filename.startswith("_"):
        raise ValueError(f"Filenames starting with '_' are reserved for internal use.")


def _resources_dir() -> Path:
    return DATA_DIR


def _versions_dir() -> Path:
    return DATA_DIR / "_versions"


def _index_path() -> Path:
    return DATA_DIR / "_index.json"


def _read_index() -> dict:
    p = _index_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _write_index(index: dict) -> None:
    _index_path().write_text(json.dumps(index, indent=2, ensure_ascii=False))


def _version_filename(filename: str, version: int) -> str:
    """Generate versioned filename: foo.md -> foo.v3.md"""
    stem, dot, ext = filename.rpartition(".")
    if dot:
        return f"{stem}.v{version}.{ext}"
    return f"{filename}.v{version}"


def _ensure_dirs() -> None:
    _resources_dir().mkdir(parents=True, exist_ok=True)
    _versions_dir().mkdir(parents=True, exist_ok=True)


@mcp.tool()
def get_resource_files() -> list[dict]:
    """List all resource files with their descriptions and metadata.

    Returns a list of all stored files with filename, description,
    last modified timestamp, and version count.
    """
    index = _read_index()
    results = []
    for filename in sorted(index.keys()):
        entry = index[filename]
        results.append({
            "filename": filename,
            "description": entry.get("description", ""),
            "last_modified": entry.get("last_modified", ""),
            "versions": entry.get("versions", 1),
        })
    return results


@mcp.tool()
def get_file_contents(filename: str, version: int | None = None) -> str:
    """Read the contents of a resource file.

    Args:
        filename: The filename to read (e.g., 'daily-plan.md')
        version: Optional version number to read. Defaults to latest.
    """
    _validate_filename(filename)

    if version is not None:
        # Read a specific version
        vfile = _versions_dir() / _version_filename(filename, version)
        if not vfile.exists():
            raise FileNotFoundError(f"Version {version} of {filename!r} not found.")
        return vfile.read_text(encoding="utf-8")

    # Read latest (current file)
    path = _resources_dir() / filename
    if not path.exists():
        raise FileNotFoundError(f"File {filename!r} not found.")
    return path.read_text(encoding="utf-8")


@mcp.tool()
def edit_file(filename: str, content: str, description: str | None = None) -> dict:
    """Create or update a resource file with automatic versioning.

    Same-day edits overwrite the current version. Edits on a new day
    create a new version (up to 10 retained). This is the single tool
    for both creating and updating files.

    Args:
        filename: The filename to write (e.g., 'daily-plan.md', 'journal-backlog.md')
        content: The full file content to store
        description: One-line description of what this file contains. Required for new files.
    """
    _validate_filename(filename)
    _ensure_dirs()

    index = _read_index()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    current_path = _resources_dir() / filename
    is_new_file = filename not in index

    if is_new_file:
        if not description:
            raise ValueError("Description is required when creating a new file.")
        # New file — write it, create version 1
        current_path.write_text(content, encoding="utf-8")

        v1_path = _versions_dir() / _version_filename(filename, 1)
        v1_path.write_text(content, encoding="utf-8")

        index[filename] = {
            "description": description,
            "last_modified": now.isoformat(),
            "versions": 1,
            "current_version_date": today,
        }
        _write_index(index)

        return {
            "status": "created",
            "filename": filename,
            "version": 1,
            "is_new_version": True,
            "bytes": len(content),
        }

    # Existing file — check if same-day edit or new version
    entry = index[filename]
    current_version = entry.get("versions", 1)
    current_version_date = entry.get("current_version_date", "")

    if current_version_date == today:
        # Same-day edit — overwrite current file and current version
        current_path.write_text(content, encoding="utf-8")

        vfile = _versions_dir() / _version_filename(filename, current_version)
        vfile.write_text(content, encoding="utf-8")

        entry["last_modified"] = now.isoformat()
        if description:
            entry["description"] = description
        _write_index(index)

        return {
            "status": "updated",
            "filename": filename,
            "version": current_version,
            "is_new_version": False,
            "bytes": len(content),
        }

    # New day — create a new version
    new_version = current_version + 1

    if new_version > MAX_VERSIONS:
        # Rotate: drop version 1, shift all down, new version becomes MAX_VERSIONS
        _rotate_versions(filename, current_version)
        new_version = MAX_VERSIONS

    current_path.write_text(content, encoding="utf-8")

    vfile = _versions_dir() / _version_filename(filename, new_version)
    vfile.write_text(content, encoding="utf-8")

    entry["last_modified"] = now.isoformat()
    entry["versions"] = new_version
    entry["current_version_date"] = today
    if description:
        entry["description"] = description
    _write_index(index)

    return {
        "status": "updated",
        "filename": filename,
        "version": new_version,
        "is_new_version": True,
        "bytes": len(content),
    }


def _rotate_versions(filename: str, current_version: int) -> None:
    """Drop the oldest version and shift all versions down by 1."""
    vdir = _versions_dir()

    # Delete version 1
    oldest = vdir / _version_filename(filename, 1)
    if oldest.exists():
        oldest.unlink()

    # Shift 2..current_version down to 1..current_version-1
    for v in range(2, current_version + 1):
        old_path = vdir / _version_filename(filename, v)
        new_path = vdir / _version_filename(filename, v - 1)
        if old_path.exists():
            old_path.rename(new_path)


@mcp.tool()
def get_resource_versions(filename: str) -> list[dict]:
    """Get the version history of a resource file.

    Returns a list of all retained versions with their version number,
    date, and size. Use get_file_contents(filename, version=N) to read
    a specific version.

    Args:
        filename: The filename to check versions for
    """
    _validate_filename(filename)

    index = _read_index()
    if filename not in index:
        raise FileNotFoundError(f"File {filename!r} not found.")

    entry = index[filename]
    total_versions = entry.get("versions", 1)
    vdir = _versions_dir()
    results = []

    for v in range(1, total_versions + 1):
        vpath = vdir / _version_filename(filename, v)
        if vpath.exists():
            stat = vpath.stat()
            results.append({
                "version": v,
                "date": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d"),
                "size_bytes": stat.st_size,
                "is_current": v == total_versions,
            })

    return results


@mcp.tool()
def delete_file(filename: str) -> str:
    """Delete a resource file and all its versions.

    Args:
        filename: The filename to delete
    """
    _validate_filename(filename)

    index = _read_index()
    if filename not in index:
        raise FileNotFoundError(f"File {filename!r} not found.")

    # Delete current file
    current_path = _resources_dir() / filename
    if current_path.exists():
        current_path.unlink()

    # Delete all versions
    entry = index[filename]
    total_versions = entry.get("versions", 1)
    vdir = _versions_dir()
    for v in range(1, total_versions + 1):
        vpath = vdir / _version_filename(filename, v)
        if vpath.exists():
            vpath.unlink()

    # Remove from index
    del index[filename]
    _write_index(index)

    return f"Deleted {filename} and {total_versions} version(s)."


# ─── Migration ───────────────────────────────────────────────────────────────

def _migrate_from_skills_layout() -> None:
    """One-time migration from old /data/skills/{skill}/{file} layout.

    Detects the old layout by checking for skill subdirectories with
    .meta.json files. Migrates all files to flat namespace with
    {skill}-{filename} naming. Moves old directories to _migrated_backup/.
    """
    base = _resources_dir()
    if not base.exists():
        return

    migrated_any = False
    _ensure_dirs()
    index = _read_index()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_"):
            continue

        meta_path = entry / ".meta.json"
        if not meta_path.exists():
            continue

        # This is an old skill directory
        try:
            old_meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            old_meta = {}

        for fpath in sorted(entry.iterdir()):
            if not fpath.is_file() or fpath.name == ".meta.json":
                continue

            # Determine new filename
            new_name = f"{entry.name}-{fpath.name}" if fpath.name != "daily-plan.md" or entry.name != "daily-tracker" else fpath.name
            # Avoid collisions — if both assistant/daily-plan.md and daily-tracker/daily-plan.md exist
            if new_name in index:
                new_name = f"{entry.name}-{fpath.name}"

            content = fpath.read_text(encoding="utf-8")
            desc = old_meta.get(fpath.name, {}).get("description", f"Migrated from {entry.name}/{fpath.name}")
            updated = old_meta.get(fpath.name, {}).get("updated", now.isoformat())

            # Write current file
            (base / new_name).write_text(content, encoding="utf-8")

            # Write as version 1
            v1_path = _versions_dir() / _version_filename(new_name, 1)
            v1_path.write_text(content, encoding="utf-8")

            index[new_name] = {
                "description": desc,
                "last_modified": updated,
                "versions": 1,
                "current_version_date": today,
            }

            migrated_any = True

        # Move old skill directory to backup
        backup_dir = base / "_migrated_backup"
        backup_dir.mkdir(exist_ok=True)
        target = backup_dir / entry.name
        if target.exists():
            shutil.rmtree(target)
        entry.rename(target)

    if migrated_any:
        _write_index(index)


# ─── Entry ───────────────────────────────────────────────────────────────────

def main():
    """Initialize the MCP server and run in selected mode."""
    import sys

    parser = argparse.ArgumentParser(description="Resources MCP Server")
    parser.add_argument("--stdio", action="store_true", help="Run in stdio mode (Claude Desktop, VS Code)")
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode (Streamable HTTP transport)")
    parser.add_argument("--port", type=int, default=6666, help="Port for HTTP mode (default: 6666)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host for HTTP mode (default: 0.0.0.0)")
    parser.add_argument("--migrate", action="store_true", help="Run migration from old skills layout, then exit")

    args = parser.parse_args()

    # Run migration on startup if old layout detected
    _migrate_from_skills_layout()

    if args.migrate:
        print("[INFO] Migration complete.", file=sys.stderr)
        return

    if args.http:
        from skills_data_mcp.transport.http import run_http_server
        print(f"[INFO] Starting Resources MCP Server (HTTP) on {args.host}:{args.port}/mcp ...", file=sys.stderr)
        run_http_server(mcp, host=args.host, port=args.port)
    else:
        print("[INFO] Starting Resources MCP Server (stdio) ...", file=sys.stderr)
        mcp.run()


if __name__ == "__main__":
    main()
