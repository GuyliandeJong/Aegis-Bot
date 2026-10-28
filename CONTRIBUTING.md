# Contributing

Thanks for wanting to improve Aegis-Bot. Read
[docs/development.md](docs/development.md) first: it explains the
module layout and the rules below.

## Changes

- Open an issue first for anything larger than a fix, so the approach
  can be agreed before the work is done.
- Keep a change to one thing. A new feature is usually a new module.
- Keep Discord code in a module's `commands/` and views, and API code
  in its service files.
- No addresses, names or credentials in code, tests or documentation.
  Use `example.test`, `192.0.2.x` and similar documentation values.
- New settings go in `.env.example`, in the module's section, with
  their default and a comment, in the same change that adds them. The
  checks fail when the code reads a setting the file does not list (see
  "Settings and .env.example" in docs/development.md).
- Keep each file's line endings (some files use CRLF).

## Checks

Every change needs the checks to pass, and a new feature needs a check
of its own (see `tests/README.md`):

```bash
python -m compileall -q .
python tests/run_all.py
```

The same checks run on every pull request.

## Commit messages

```text
<type>(<scope>): <subject>

<what changed and why>
```

`type` is one of feat, fix, refactor, perf, docs, test, build, ci,
chore, style or revert; the subject is imperative, lower case, without
a period and at most 72 characters. The body is required.

## License

By contributing you agree that your contribution is licensed under the
[MIT License](LICENSE).
