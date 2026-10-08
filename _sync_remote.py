#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys

REPO = "slatinwine/aidb"


def gh(path, body=None, method="POST"):
    cmd = ["gh", "api", "repos/%s/%s" % (REPO, path), "-X", method]
    if body is not None:
        cmd += ["--input", "-"]
        r = subprocess.run(cmd, input=body.encode(), capture_output=True)
    else:
        r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print("FAILED:", path, r.stderr.decode()[:300])
        sys.exit(1)
    return json.loads(r.stdout.decode())


def main():
    head = gh("commits/main", None, "GET")["sha"]
    print("head:", head)
    entries = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            p = os.path.relpath(os.path.join(root, f)).replace(os.sep, "/")
            if p == "_push_api.py":
                continue
            data = open(p, "rb").read()
            b = gh("git/blobs", json.dumps({
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64"}))
            entries.append({"path": p, "mode": "100644", "type": "blob",
                            "sha": b["sha"]})
            print("blob:", p)
    tree = gh("git/trees", json.dumps({"tree": entries}))
    print("tree:", tree["sha"])
    msg = ("AIDB: concrete-style database workbench\n\n"
           "SQLite offline (sql.js embedded) + MySQL/PostgreSQL/SQL Server bridge, "
           "full CRUD, dangerous-statement confirmation, auto-resume, online demo db")
    commit = gh("git/commits", json.dumps({"message": msg, "tree": tree["sha"],
                                           "parents": [head]}))
    print("commit:", commit["sha"])
    r = gh("git/refs/heads/main", json.dumps({"sha": commit["sha"]}), method="PATCH")
    print("main updated:", r["object"]["sha"])


if __name__ == "__main__":
    main()
