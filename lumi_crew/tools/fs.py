"""File system tools, all confined to the workspace."""
from __future__ import annotations

import difflib
import fnmatch
import os
import re

from ..permissions import resolve_in_workspace
from .base import Tool, ToolContext, schema

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
                ".idea", ".vscode", "dist", "build", ".DS_Store"}


def _rel(ctx: ToolContext, path) -> str:
    return os.path.relpath(path, ctx.workspace)


def _walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for f in sorted(filenames):
            if f not in IGNORED_DIRS:
                yield os.path.join(dirpath, f)


# ----------------------------------------------------------------------------- read
async def read_file(args, ctx):
    p = resolve_in_workspace(ctx.workspace, args["path"])
    if not p.is_file():
        return f"ERROR: `{args['path']}` does not exist or is not a file"
    lines = p.read_text(errors="replace").splitlines()
    offset = max(int(args.get("offset", 1)), 1)
    limit = int(args.get("limit", 400))
    chunk = lines[offset - 1: offset - 1 + limit]
    body = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(chunk, start=offset))
    more = f"\n[… {len(lines) - (offset - 1 + limit)} more lines, use offset]" if offset - 1 + limit < len(lines) else ""
    return f"{_rel(ctx, p)} ({len(lines)} lines)\n{body}{more}" if lines else f"{_rel(ctx, p)} is empty"


async def list_dir(args, ctx):
    root = resolve_in_workspace(ctx.workspace, args.get("path", "."))
    if not root.is_dir():
        return f"ERROR: `{args.get('path')}` is not a directory"
    if args.get("recursive", True):
        files = [_rel(ctx, f) for f in _walk(root)]
    else:
        files = [_rel(ctx, root / n) + ("/" if (root / n).is_dir() else "") for n in sorted(os.listdir(root))
                 if n not in IGNORED_DIRS]
    if not files:
        return "(empty directory)"
    extra = f"\n[… {len(files) - 300} more]" if len(files) > 300 else ""
    return "\n".join(files[:300]) + extra


async def grep(args, ctx):
    root = resolve_in_workspace(ctx.workspace, args.get("path", "."))
    try:
        rx = re.compile(args["pattern"])
    except re.error as e:
        return f"ERROR: invalid regex: {e}"
    glob = args.get("glob")
    hits = []
    for f in (_walk(root) if root.is_dir() else [str(root)]):
        if glob and not fnmatch.fnmatch(os.path.basename(f), glob):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                for no, line in enumerate(fh, 1):
                    if rx.search(line):
                        hits.append(f"{_rel(ctx, f)}:{no}: {line.rstrip()[:200]}")
                        if len(hits) >= 100:
                            return "\n".join(hits) + "\n[… stopped after 100 matches]"
        except (UnicodeDecodeError, OSError):
            continue
    return "\n".join(hits) or "(no matches)"


# ----------------------------------------------------------------------------- write
def _diff(old: str, new: str, name: str) -> str:
    d = difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}")
    return "".join(d) or "(no changes)"


def _new_content_for_edit(args, ctx) -> tuple[str, str, str | None]:
    p = resolve_in_workspace(ctx.workspace, args["path"])
    if not p.is_file():
        return "", "", f"ERROR: `{args['path']}` does not exist (use write_file to create it)"
    old = p.read_text()
    count = old.count(args["old_text"])
    if count != 1:
        return old, "", (f"ERROR: old_text found {count} times in `{args['path']}`; it must match exactly once. "
                         "Read the file and include more surrounding lines.")
    return old, old.replace(args["old_text"], args["new_text"], 1), None


def preview_write(args, ctx):
    p = resolve_in_workspace(ctx.workspace, args["path"])
    old = p.read_text(errors="replace") if p.is_file() else ""
    return _diff(old, args.get("content", ""), args["path"])


def preview_edit(args, ctx):
    old, new, err = _new_content_for_edit(args, ctx)
    return err or _diff(old, new, args["path"])


async def write_file(args, ctx):
    p = resolve_in_workspace(ctx.workspace, args["path"])
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"])
    return f"{'Overwrote' if existed else 'Created'} {_rel(ctx, p)} ({len(args['content'].splitlines())} lines)"


async def edit_file(args, ctx):
    _, new, err = _new_content_for_edit(args, ctx)
    if err:
        return err
    p = resolve_in_workspace(ctx.workspace, args["path"])
    p.write_text(new)
    return f"Edited {_rel(ctx, p)}"


PATH = {"type": "string", "description": "path relative to the workspace"}

READ_TOOLS = [
    Tool("read_file", "Read a text file (with line numbers).",
         schema({"path": PATH, "offset": {"type": "integer", "description": "first line (1-based)"},
                 "limit": {"type": "integer", "description": "max lines, default 400"}}, ["path"]),
         "read", read_file),
    Tool("list_dir", "List files in a directory of the workspace (recursive by default).",
         schema({"path": PATH, "recursive": {"type": "boolean"}}), "read", list_dir),
    Tool("grep", "Search files for a regular expression; returns file:line: text.",
         schema({"pattern": {"type": "string"}, "path": PATH,
                 "glob": {"type": "string", "description": "file name filter, e.g. *.py"}}, ["pattern"]),
         "read", grep),
]

WRITE_TOOLS = [
    Tool("write_file", "Create a file or overwrite it completely with `content`.",
         schema({"path": PATH, "content": {"type": "string"}}, ["path", "content"]),
         "write", write_file, preview_write),
    Tool("edit_file", "Replace `old_text` (must occur exactly once) with `new_text` in an existing file.",
         schema({"path": PATH, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                ["path", "old_text", "new_text"]),
         "write", edit_file, preview_edit),
]
