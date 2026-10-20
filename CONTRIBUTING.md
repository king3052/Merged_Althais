# Contributing to Althais

## Branches

- `main` is what runs at app.althais.com. Railway deploys it on every push, so `main` must always work.
- Never commit straight to `main`. Make a branch for each change:
  - `feature/<short-name>` for new work, e.g. `feature/denials-filters`
  - `fix/<short-name>` for bug fixes, e.g. `fix/login-redirect`
  - `ui/<short-name>` for design-only changes, e.g. `ui/landing-hero`
- Keep branches short-lived. Merge them within a few days and delete them after merging.

## Making a change

```bash
git checkout main
git pull
git checkout -b feature/your-change
# ...edit...
python -m pytest tests -q
git add -A
git commit -m "Denials: filter by payer"
git push -u origin feature/your-change
```

Then open a pull request on GitHub into `main` and ask one teammate to review it.

## Commit messages

Write what changed, from the user's point of view, in the present tense. Start with the area when it helps.

- Good: `Claims: show the payer's filing deadline on each claim`
- Good: `Fix the login page redirect after a password reset`
- Avoid: `update`, `fixes`, `Add files via upload`, `?`

Use the command line or GitHub Desktop to commit. The GitHub website's file uploader can cut off large files.

## Pull requests

- One topic per pull request.
- Describe what changed and how you checked it. Add a screenshot for anything visual.
- Tests must pass.
- Check the page in both light and dark mode, and at phone width, for UI changes.

## Data, privacy and secrets

- Use synthetic test data only. Never put a real patient's name, date of birth, insurance details or chart in code, tests, screenshots or commits.
- Keys and passwords go in environment variables (`.env` locally, Railway in production), never in code or comments.
- If you commit a secret by mistake, tell Kanishk right away so it can be revoked. Deleting it in a later commit is not enough, because it stays in the git history.

## Where things live

See the project layout in [README.md](README.md). Feature notes are in [docs/](docs/).

## Questions

Ask Kanishk (kyankarla@althais.com) about the code, or Kevin (kevinqu@althais.com) about the product.
