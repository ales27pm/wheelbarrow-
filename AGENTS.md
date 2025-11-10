# Repository Guidelines

## Scope
These rules apply to the entire repository.

## Code Style
- Prefer Python 3.10+ syntax where applicable.
- Keep functions focused and documented with short docstrings when non-obvious.
- Use f-strings for string formatting.
- Avoid introducing external dependencies unless strictly necessary.

## Testing
- When Python files are modified, run `python -m compileall .` from the repository root to ensure there are no syntax errors.

## Documentation
- Update `README.md` whenever new commands, scripts, or usage details are introduced.

## Pull Requests
- Summaries should highlight functional user-facing changes and any new scripts or commands.
- Note any limitations or manual steps required to exercise the changes.

## Session Setup
- Always run `setup.sh` at the beginning of each session.
