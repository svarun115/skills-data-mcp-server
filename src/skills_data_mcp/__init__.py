"""
Skills Data MCP Server

Provides persistent file storage scoped per skill.
Stores files at /data/skills/{skill}/{filename} on the VM.
"""

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_DIR = Path(os.environ.get("SKILLS_DATA_DIR", "/data/skills"))

mcp = FastMCP("skills-data")

# Only allow simple names: letters, digits, hyphens, underscores
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_\-]+$")
# Allow dots in filenames (e.g., weekly-plan.md), but not .meta.json
_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _validate_skill(skill: str) -> None:
    if not _SAFE_NAME.match(skill):
        raise ValueError(f"Invalid skill name: {skill!r}. Use only letters, digits, hyphens, underscores.")


def _validate_filename(filename: str) -> None:
    if not _SAFE_FILENAME.match(filename) or ".." in filename or filename == ".meta.json":
        raise ValueError(f"Invalid filename: {filename!r}. Use only letters, digits, hyphens, underscores, dots.")


def _skill_dir(skill: str) -> Path:
    return DATA_DIR / skill


def _meta_path(skill: str) -> Path:
    return _skill_dir(skill) / ".meta.json"


def _read_meta(skill: str) -> dict:
    p = _meta_path(skill)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _write_meta(skill: str, meta: dict) -> None:
    _meta_path(skill).write_text(json.dumps(meta, indent=2))


@mcp.tool()
def write_skill_file(skill: str, filename: str, content: str, description: str) -> str:
    """Write or update a file in the skill's persistent storage directory.

    Files are stored at /data/skills/{skill}/{filename} on the server.
    Use this instead of local file writes so state is accessible across sessions
    and from both Claude.ai and Claude Code.

    Args:
        skill: The skill name (e.g., 'fitness-coach', 'daily-tracker', 'cos')
        filename: The filename to write (e.g., 'weekly-plan.md', 'daily-context.json')
        content: The full file content to store
        description: One-line description of what this file contains
    """
    _validate_skill(skill)
    _validate_filename(filename)

    skill_dir = _skill_dir(skill)
    skill_dir.mkdir(parents=True, exist_ok=True)

    (skill_dir / filename).write_text(content, encoding="utf-8")

    meta = _read_meta(skill)
    meta[filename] = {
        "description": description,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    _write_meta(skill, meta)

    return f"Written {len(content)} bytes to {skill}/{filename}"


@mcp.tool()
def read_skill_file(skill: str, filename: str) -> str:
    """Read a file from the skill's persistent storage directory.

    Args:
        skill: The skill name (e.g., 'fitness-coach', 'daily-tracker', 'cos')
        filename: The filename to read (e.g., 'weekly-plan.md', 'daily-context.json')
    """
    _validate_skill(skill)
    _validate_filename(filename)

    path = _skill_dir(skill) / filename
    if not path.exists():
        raise FileNotFoundError(f"No file {filename!r} found for skill {skill!r}")

    return path.read_text(encoding="utf-8")


@mcp.tool()
def list_skill_files(skill: str) -> list[dict]:
    """List all files stored for a skill, with their descriptions and last-updated timestamps.

    Args:
        skill: The skill name (e.g., 'fitness-coach', 'daily-tracker', 'cos')
    """
    _validate_skill(skill)

    skill_dir = _skill_dir(skill)
    if not skill_dir.exists():
        return []

    meta = _read_meta(skill)
    files = [
        f.name
        for f in skill_dir.iterdir()
        if f.is_file() and f.name != ".meta.json"
    ]

    return [
        {
            "filename": f,
            "description": meta.get(f, {}).get("description", ""),
            "updated": meta.get(f, {}).get("updated", ""),
        }
        for f in sorted(files)
    ]


@mcp.tool()
def delete_skill_file(skill: str, filename: str) -> str:
    """Delete a file from the skill's persistent storage directory.

    Use this to clean up files that are no longer needed by a skill.

    Args:
        skill: The skill name (e.g., 'fitness-coach', 'daily-tracker', 'cos')
        filename: The filename to delete (e.g., 'weekly-plan.md')
    """
    _validate_skill(skill)
    _validate_filename(filename)

    path = _skill_dir(skill) / filename
    if not path.exists():
        raise FileNotFoundError(f"No file {filename!r} found for skill {skill!r}")

    path.unlink()

    meta = _read_meta(skill)
    if filename in meta:
        del meta[filename]
        _write_meta(skill, meta)

    return f"Deleted {skill}/{filename}"


def main():
    """Initialize the MCP server and run in selected mode."""
    import sys

    parser = argparse.ArgumentParser(description="Skills Data MCP Server")
    parser.add_argument("--stdio", action="store_true", help="Run in stdio mode (Claude Desktop, VS Code)")
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode (Streamable HTTP transport)")
    parser.add_argument("--port", type=int, default=6666, help="Port for HTTP mode (default: 6666)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host for HTTP mode (default: 0.0.0.0)")

    args = parser.parse_args()

    if args.http:
        from skills_data_mcp.transport.http import run_http_server
        print(f"[INFO] Starting Skills Data MCP Server (HTTP) on {args.host}:{args.port}/mcp ...", file=sys.stderr)
        run_http_server(mcp, host=args.host, port=args.port)
    else:
        print("[INFO] Starting Skills Data MCP Server (stdio) ...", file=sys.stderr)
        mcp.run()


if __name__ == "__main__":
    main()
