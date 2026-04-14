#!/usr/bin/env python3
"""
Git Tool API — fetch files and commit to GitHub/GitLab via REST.
Swagger UI available at http://localhost:8000/docs
"""
import base64, os
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import Response
from pydantic import BaseModel

# re-use core logic from git_tool.py
from git_tool import api, headers as build_headers
from urllib import parse

app = FastAPI(
    title="Git Tool API",
    description="Fetch files from and commit files to GitHub/GitLab at any specific path — no local git needed.",
    version="1.0.0",
)

Platform = Literal["github", "gitlab"]

# ── Models ────────────────────────────────────────────────────────────────────

class FetchRequest(BaseModel):
    repo: str           = "owner/repo"
    path: str           = "src/utils/helpers.py"
    ref: str            = "main"
    platform: Platform  = "github"

    model_config = {"json_schema_extra": {"example": {
        "repo": "octocat/Hello-World", "path": "README.md", "ref": "main", "platform": "github"
    }}}

class FetchedFile(BaseModel):
    name: str
    path: str
    content_base64: str
    size: int
    url: str

class FetchResponse(BaseModel):
    files: list[FetchedFile]

class CommitRequest(BaseModel):
    repo: str           = "owner/repo"
    remote_path: str    = "src/new_file.py"
    content_base64: str = "<base64-encoded file content>"
    message: str        = "add file via git-tool API"
    branch: str         = "main"
    platform: Platform  = "github"

    model_config = {"json_schema_extra": {"example": {
        "repo": "octocat/Hello-World", "remote_path": "src/hello.py",
        "content_base64": "cHJpbnQoJ2hlbGxvJyk=",
        "message": "add hello.py", "branch": "main", "platform": "github"
    }}}

class CommitResponse(BaseModel):
    url: str
    branch: str
    remote_path: str

# ── Helpers ───────────────────────────────────────────────────────────────────

def _token_headers(platform: Platform, token: Optional[str]) -> dict:
    """Build auth headers; prefer explicit token over env var."""
    hdrs = build_headers(platform)
    if token:
        if platform == "github":
            hdrs["Authorization"] = f"Bearer {token}"
        else:
            hdrs["PRIVATE-TOKEN"] = token
    return hdrs

def _gitlab_base() -> str:
    return os.environ.get("GITLAB_URL", "https://gitlab.com")

def _gh_fetch_recursive(repo, path, ref, hdrs) -> list[FetchedFile]:
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    data, status = api(url, hdrs=hdrs)
    if status != 200:
        raise HTTPException(status, detail=data.get("message", str(data)))
    if isinstance(data, list):
        files = []
        for item in data:
            if item["type"] == "file":
                files.extend(_gh_fetch_recursive(repo, item["path"], ref, hdrs))
        return files
    return [FetchedFile(
        name=os.path.basename(data["path"]),
        path=data["path"],
        content_base64=data["content"].replace("\n", ""),
        size=data["size"],
        url=data["html_url"],
    )]

def _gl_fetch(repo, path, ref, hdrs) -> list[FetchedFile]:
    base = _gitlab_base()
    proj = parse.quote(repo, safe="")
    enc  = parse.quote(path, safe="")
    url  = f"{base}/api/v4/projects/{proj}/repository/files/{enc}?ref={ref}"
    data, status = api(url, hdrs=hdrs)
    if status != 200:
        raise HTTPException(status, detail=data.get("message", str(data)))
    return [FetchedFile(
        name=os.path.basename(path),
        path=path,
        content_base64=data["content"],
        size=len(base64.b64decode(data["content"])),
        url=f"{base}/{repo}/-/blob/{ref}/{path}",
    )]

# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(
    "/fetch",
    response_model=FetchResponse,
    summary="Fetch file(s) at a specific path",
    tags=["operations"],
)
def fetch(
    body: FetchRequest,
    x_token: Optional[str] = Header(None, description="GitHub or GitLab personal access token (overrides env var)"),
):
    """
    Download one file or all files in a directory from a remote repo at a given path and ref.

    - **GitHub** — pass `GITHUB_TOKEN` env var or `X-Token` header
    - **GitLab** — pass `GITLAB_TOKEN` env var or `X-Token` header; set `GITLAB_URL` for self-hosted instances
    """
    hdrs = _token_headers(body.platform, x_token)
    if body.platform == "github":
        files = _gh_fetch_recursive(body.repo, body.path, body.ref, hdrs)
    else:
        files = _gl_fetch(body.repo, body.path, body.ref, hdrs)
    return FetchResponse(files=files)


@app.get(
    "/fetch/raw",
    summary="Download a single file as raw bytes",
    tags=["operations"],
    response_class=Response,
)
def fetch_raw(
    repo: str,
    path: str,
    ref: str = "main",
    platform: Platform = "github",
    x_token: Optional[str] = Header(None),
):
    """
    Stream a single file as raw bytes (suitable for direct download).
    Returns the file with `Content-Disposition: attachment`.
    """
    hdrs = _token_headers(platform, x_token)
    if platform == "github":
        files = _gh_fetch_recursive(repo, path, ref, hdrs)
    else:
        files = _gl_fetch(repo, path, ref, hdrs)
    if not files:
        raise HTTPException(404, "no file found at that path")
    f = files[0]
    content = base64.b64decode(f.content_base64)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{f.name}"'},
    )


@app.post(
    "/commit",
    response_model=CommitResponse,
    summary="Commit & push a file to a specific remote path",
    tags=["operations"],
)
def commit(
    body: CommitRequest,
    x_token: Optional[str] = Header(None, description="GitHub or GitLab personal access token (overrides env var)"),
):
    """
    Create or update a file at `remote_path` in the given repo and branch.
    File content must be **base64-encoded**.

    - Creates the file if it doesn't exist
    - Updates (overwrites) if it already exists
    - No local git clone required — the API call is the commit+push
    """
    hdrs = _token_headers(body.platform, x_token)
    content = body.content_base64

    if body.platform == "github":
        url = f"https://api.github.com/repos/{body.repo}/contents/{body.remote_path}"
        existing, status = api(url + f"?ref={body.branch}", hdrs=hdrs)
        payload = {"message": body.message, "content": content, "branch": body.branch}
        if status == 200:
            payload["sha"] = existing["sha"]
        data, status = api(url, method="PUT", body=payload, hdrs=hdrs)
        if status not in (200, 201):
            raise HTTPException(status, detail=data.get("message", str(data)))
        file_url = data["content"]["html_url"]

    else:
        base = _gitlab_base()
        proj = parse.quote(body.repo, safe="")
        enc  = parse.quote(body.remote_path, safe="")
        url  = f"{base}/api/v4/projects/{proj}/repository/files/{enc}"
        payload = {"branch": body.branch, "content": content, "encoding": "base64", "commit_message": body.message}
        _, probe = api(url + f"?ref={body.branch}", hdrs=hdrs)
        method = "PUT" if probe == 200 else "POST"
        data, status = api(url, method=method, body=payload, hdrs=hdrs)
        if status not in (200, 201):
            raise HTTPException(status, detail=data.get("message", str(data)))
        file_url = f"{base}/{body.repo}/-/blob/{body.branch}/{body.remote_path}"

    return CommitResponse(url=file_url, branch=body.branch, remote_path=body.remote_path)


@app.get("/health", tags=["meta"], summary="Health check")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
