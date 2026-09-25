#!/usr/bin/env python3
"""Scaffold a new skill in the stack.

    python3 scripts/new-skill.py <name> "<one-sentence description: what it does and when to use it>"

Creates skills/<name>/ with SKILL.md (frontmatter + the section skeleton every zstack
skill follows), scripts/, reference/ and templates/, then refreshes the README catalog.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKELETON = '''---
name: {name}
description: "{description}"
---

# {name}

One paragraph: what this skill produces, and which check proves it worked.

Resolve `<skill>` to the directory holding this file. Work in a run directory
`runs/<id>/` inside the user's project (never inside this skill), so a run can stop
and resume and every step leaves a file behind.

## 1. Inputs

What the user must supply or confirm, and the file it is written to.

## 2. Steps

Numbered, each naming the script it runs (`python3 <skill>/scripts/<step>.py runs/<id>`)
and the file it writes.

## 3. Checks

The measurable gates. A failure is fixed in the work, never by lowering a threshold.

## Reporting

What to report after each round, and what must be said plainly when a gate is not met.
'''


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    name, description = argv[0], argv[1].replace('"', "'")
    if not re.match(r'^[a-z0-9][a-z0-9-]{0,63}$', name):
        print('name must be lowercase letters, digits and hyphens (max 64)')
        return 2
    d = ROOT / 'skills' / name
    if d.exists():
        print(f'skills/{name} already exists')
        return 1
    for sub in ('scripts', 'reference', 'templates'):
        (d / sub).mkdir(parents=True)
        (d / sub / '.gitkeep').touch()
    (d / 'SKILL.md').write_text(SKELETON.format(name=name, description=description))
    print(f'created skills/{name}/')
    return subprocess.call([sys.executable, str(ROOT / 'scripts' / 'validate.py'), '--write-catalog'])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
