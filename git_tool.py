#!/usr/bin/env python3
import argparse, base64, json, os, sys
from urllib import request, parse, error

def headers(platform):
    if platform == "github":
        token = os.environ.get("GITHUB_TOKEN", "")
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITLAB_TOKEN", "")
    return {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}

def api(url, method="GET", body=None, hdrs=None):
    data = json.dumps(body).encode() if body else None
    req = request.Request(url, data=data, headers=hdrs or {}, method=method)
    try:
        with request.urlopen(req) as r:
            return json.loads(r.read()), r.status
    except error.HTTPError as e:
        return json.loads(e.read()), e.code

# ── GitHub ────────────────────────────────────────────────────────────────────

def gh_fetch(repo, path, ref, out, hdrs):
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    data, status = api(url, hdrs=hdrs)
    if status != 200:
        sys.exit(f"fetch failed ({status}): {data.get('message', data)}")
    if isinstance(data, list):                          # directory
        for item in data:
            if item["type"] == "file":
                gh_fetch(repo, item["path"], ref, out, hdrs)
    else:
        dest = os.path.join(out, os.path.basename(data["path"]))
        os.makedirs(out, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(base64.b64decode(data["content"]))
        print(f"  fetched → {dest}")

def gh_commit(repo, local, remote_path, message, branch, hdrs):
    with open(local, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    url = f"https://api.github.com/repos/{repo}/contents/{remote_path}"
    existing, status = api(url + f"?ref={branch}", hdrs=hdrs)
    body = {"message": message, "content": content, "branch": branch}
    if status == 200:
        body["sha"] = existing["sha"]
    data, status = api(url, method="PUT", body=body, hdrs=hdrs)
    if status not in (200, 201):
        sys.exit(f"commit failed ({status}): {data.get('message', data)}")
    print(f"  committed → {data['content']['html_url']}")

# ── GitLab ────────────────────────────────────────────────────────────────────

def gl_fetch(repo, path, ref, out, hdrs):
    base = os.environ.get("GITLAB_URL", "https://gitlab.com")
    proj = parse.quote(repo, safe="")
    enc  = parse.quote(path, safe="")
    url  = f"{base}/api/v4/projects/{proj}/repository/files/{enc}?ref={ref}"
    data, status = api(url, hdrs=hdrs)
    if status != 200:
        sys.exit(f"fetch failed ({status}): {data.get('message', data)}")
    dest = os.path.join(out, os.path.basename(path))
    os.makedirs(out, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(base64.b64decode(data["content"]))
    print(f"  fetched → {dest}")

def gl_commit(repo, local, remote_path, message, branch, hdrs):
    base = os.environ.get("GITLAB_URL", "https://gitlab.com")
    proj = parse.quote(repo, safe="")
    enc  = parse.quote(remote_path, safe="")
    with open(local, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    url  = f"{base}/api/v4/projects/{proj}/repository/files/{enc}"
    body = {"branch": branch, "content": content, "encoding": "base64", "commit_message": message}
    _, status = api(url, hdrs=hdrs)                     # probe for existence
    method = "PUT" if status == 200 else "POST"
    data, status = api(url, method=method, body=body, hdrs=hdrs)
    if status not in (200, 201):
        sys.exit(f"commit failed ({status}): {data.get('message', data)}")
    print(f"  committed → {base}/{repo}/-/blob/{branch}/{remote_path}")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Minimal git fetch/commit via REST API")
    p.add_argument("--platform", choices=["github", "gitlab"], default="github")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download file(s) from a specific remote path")
    f.add_argument("repo",  help="owner/repo")
    f.add_argument("path",  help="remote path (file or directory)")
    f.add_argument("--ref", default="main", help="branch, tag, or SHA (default: main)")
    f.add_argument("--out", default=".", help="local output directory (default: .)")

    c = sub.add_parser("commit", help="commit & push a local file to a specific remote path")
    c.add_argument("repo",       help="owner/repo")
    c.add_argument("local",      help="local file to upload")
    c.add_argument("remote_path",help="destination path in the repo")
    c.add_argument("-m", "--message", required=True, help="commit message")
    c.add_argument("--branch", default="main", help="target branch (default: main)")

    args = p.parse_args()
    hdrs = headers(args.platform)

    if args.cmd == "fetch":
        fn = gh_fetch if args.platform == "github" else gl_fetch
        fn(args.repo, args.path, args.ref, args.out, hdrs)
    else:
        fn = gh_commit if args.platform == "github" else gl_commit
        fn(args.repo, args.local, args.remote_path, args.message, args.branch, hdrs)

if __name__ == "__main__":
    main()
