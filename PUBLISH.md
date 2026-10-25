# Publish khroxy source to GitHub

Use an **initially private** repository until the Docker isolation tests are complete. No publishing operation is performed by the ZIP itself.

1. Extract `khroxy-v0.7.1.zip` on Debian and enter the extracted `khroxy-v0.7.1` directory.
2. Run the offline tests and review the staged file names before pushing:

```bash
python3 -m unittest discover -p 'test_*.py' -q
git init -b main
git add .
git status --short
# Must NOT include .env, docker/xray, state.json, runtime.json, events.log, backups, UUIDs or subscription URLs.
git diff --cached --name-only
git commit -m 'Initial khroxy v0.7.1 source and audit'
```

3. With GitHub CLI already installed/authenticated, create a private repository and push:

```bash
gh auth status
gh repo create khroxy --private --source=. --remote=origin --push
```

If `gh` is unavailable, create an empty **private** `khroxy` repository on GitHub (without initial README/.gitignore). Then add its exact HTTPS or SSH clone URL:

```bash
git remote add origin <YOUR-GITHUB-REPOSITORY-URL>
git push -u origin main
```

Do not paste repository tokens into commands or into an issue. These steps create a *new* repository; for an existing khroxy repository, inspect its current branches/history first and do not force-push.

GitHub Actions runs Python offline tests and shell syntax checks on pushes and pull requests. It does **not** prove Docker isolation, build a network tunnel, or deploy the service.
