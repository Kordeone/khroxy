# Publish v0.9 to GitHub

Run `python3 -m unittest discover -q` locally. The package is source-only; secrets, Xray binary, Codex credentials, subscription URLs and project files are not included.

From the extracted directory:

```sh
git init -b main
git add .
git status --short
git commit -m "khroxy v0.9: rootless Podman workspace preview"
gh repo create khroxy --private --source=. --remote=origin --push
```

Use a **private repository** while the container kill switch is unverified. Connect the GitHub integration or authenticate `gh` on Debian before trying to publish. Do not put credentials in commit history. `.containerignore` prevents unnecessary build inputs; `.gitignore` excludes local runtime secrets.
