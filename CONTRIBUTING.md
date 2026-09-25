# Contributing a skill

Every zstack skill follows the same contract, so any of them can be picked up, resumed, and
trusted the same way.

## Shape

```
skills/<name>/
  SKILL.md        frontmatter (name, description) + numbered steps + checks + reporting
  scripts/        the code each step runs (stdlib Python where possible)
  reference/      contracts and background the steps link to
  templates/      starting files the first step copies into a run directory
```

`python3 scripts/new-skill.py <name> "<description>"` creates this skeleton.

## Rules

1. **Frontmatter.** `name` equals the directory name (lowercase, digits, hyphens). `description`
   says what the skill does *and when to use it*: it is how Claude decides to load the skill.
2. **Run directories.** A skill writes into `runs/<id>/` in the user's project, never into the
   skill itself. Every step leaves a file behind, so work can stop and resume.
3. **Checks that can fail.** Each claim ("1:1", "doors open", "passes") is backed by a script with
   thresholds. A failure is fixed in the work, never by lowering a threshold.
4. **Honest reporting.** Report what the checks measured and say plainly what did not pass.
   A render or a screenshot is not a proof of correctness.
5. **Private references stay private.** Photos, third-party images, and anything identifying a
   real person or a brand's marks never enter this repository. Skills write such material
   outside the run directory; `scripts/validate.py` rejects `refs/` directories, photos inside
   skills, `*.local.*` files, and anything that looks like an API key.
6. **Referenced paths exist.** Write paths inside SKILL.md as `<skill>/path`; the validator
   checks each one.
7. **Small files.** No file over 5 MB inside `skills/`. Large outputs belong in examples or
   releases.

## Before opening a pull request

```bash
python3 scripts/validate.py --write-catalog
```

It must end with `0 error(s)`. CI runs the same check.
