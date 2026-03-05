# skills-data-mcp-server

MCP server that provides persistent file storage scoped per skill. Solves the problem of Claude.ai skills having no way to persist state across sessions.

## Tools

| Tool | Params | Description |
|---|---|---|
| `write_skill_file` | `skill`, `filename`, `content`, `description` | Write/update a file at `/data/skills/{skill}/{filename}` |
| `read_skill_file` | `skill`, `filename` | Read a file from the skill's directory |
| `list_skill_files` | `skill` | List filenames + descriptions for a skill |

## Storage

Files are stored at `/data/skills/{skill}/{filename}` in a Docker volume. Metadata (descriptions, timestamps) is kept in a `.meta.json` file per skill directory.

## Running

```bash
# HTTP mode (production)
python -m skills_data_mcp --http --host 0.0.0.0 --port 6666

# stdio mode (Claude Desktop / VS Code)
python -m skills_data_mcp --stdio
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SKILLS_DATA_DIR` | `/data/skills` | Root directory for skill data files |
