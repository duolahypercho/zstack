#!/usr/bin/env python3
"""Validate every skill in the stack, and keep the README catalog in sync.

    python3 scripts/validate.py                  # check everything, exit 1 on any error
    python3 scripts/validate.py --write-catalog  # also rewrite the catalog table in README.md
    python3 scripts/validate.py vehicle          # check one skill

A skill is a directory skills/<name>/ holding SKILL.md with YAML frontmatter:

    ---
    name: <name>              # must equal the directory name: lowercase, digits, hyphens, <= 64 chars
    description: "<what it does and when to use it>"   # <= 1024 chars
    ---

Checks (stdlib only):
  - frontmatter present, name matches the directory, description within limits
  - every `<skill>/path` mentioned in SKILL.md exists inside the skill
  - every .py compiles, every .json parses
  - no file over 5 MB inside skills/, no photos inside skills/ (skills ship code, not references)
  - nothing on the deny list (reference photos, *.local.*, credentials) is present anywhere
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / 'skills'
README = ROOT / 'README.md'
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
MAX_BYTES = 5 * 1024 * 1024
PHOTO_EXT = {'.jpg', '.jpeg', '.heic', '.webp', '.tif', '.tiff'}
DENY = [
    (re.compile(r'(^|/)refs/'), 'reference material belongs outside the repository'),
    (re.compile(r'\.local\.'), 'local-only file'),
    (re.compile(r'(^|/)\.env$'), 'environment file'),
    (re.compile(r'\.(pem|key|p12)$'), 'key material'),
]
SECRET_RE = re.compile(r'(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,})')
CAT_BEGIN, CAT_END = '<!-- catalog:begin -->', '<!-- catalog:end -->'


def frontmatter(text):
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---', 4)
    if end < 0:
        return None
    meta = {}
    for line in text[4:end].splitlines():
        m = re.match(r'^([A-Za-z_-]+):\s*(.*)$', line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1]
            meta[m.group(1)] = v
    return meta


def check_skill(d):
    errs = []
    md = d / 'SKILL.md'
    if not md.exists():
        return [f'{d.name}: missing SKILL.md'], None
    text = md.read_text()
    meta = frontmatter(text)
    if meta is None:
        return [f'{d.name}: SKILL.md has no --- frontmatter ---'], None
    name, desc = meta.get('name', ''), meta.get('description', '')
    if name != d.name:
        errs.append(f'{d.name}: frontmatter name "{name}" must equal the directory name')
    if not NAME_RE.match(name or ''):
        errs.append(f'{d.name}: name must be lowercase letters, digits and hyphens (<= 64 chars)')
    if not desc:
        errs.append(f'{d.name}: description is empty')
    elif len(desc) > 1024:
        errs.append(f'{d.name}: description is {len(desc)} chars (max 1024)')
    for ref in sorted(set(re.findall(r'<skill>/([A-Za-z0-9_./-]+)', text))):
        ref = ref.rstrip('.')
        if not (d / ref).exists():
            errs.append(f'{d.name}: SKILL.md references <skill>/{ref}, which does not exist')
    for f in d.rglob('*'):
        if not f.is_file() or '__pycache__' in f.parts:
            continue
        rel = f.relative_to(ROOT)
        if f.stat().st_size > MAX_BYTES:
            errs.append(f'{rel}: over 5 MB; skills ship code and small templates only')
        if f.suffix.lower() in PHOTO_EXT:
            errs.append(f'{rel}: photos do not belong inside a skill')
        if f.suffix == '.py':
            try:
                compile(f.read_text(), str(f), 'exec')
            except SyntaxError as e:
                errs.append(f'{rel}: does not compile: line {e.lineno}: {e.msg}')
        if f.suffix == '.json':
            try:
                json.loads(f.read_text())
            except ValueError as e:
                errs.append(f'{rel}: invalid JSON: {e}')
    return errs, {'name': name, 'description': desc}


def check_repo():
    errs = []
    for f in ROOT.rglob('*'):
        if not f.is_file() or '.git' in f.parts or '__pycache__' in f.parts:
            continue
        rel = f.relative_to(ROOT).as_posix()
        for pat, why in DENY:
            if pat.search(rel):
                errs.append(f'{rel}: {why}')
        if f.suffix in {'.py', '.md', '.json', '.sh', '.txt', '.yml', '.yaml'} and f.stat().st_size < MAX_BYTES:
            if SECRET_RE.search(f.read_text(errors='ignore')):
                errs.append(f'{rel}: looks like it contains an API key')
    for name in ('plugin.json', 'marketplace.json'):
        p = ROOT / '.claude-plugin' / name
        try:
            json.loads(p.read_text())
        except (OSError, ValueError) as e:
            errs.append(f'.claude-plugin/{name}: {e}')
    return errs


def one_line(desc, limit=150):
    s = desc.split('. ')[0].rstrip('.')
    return s if len(s) <= limit else s[:limit - 1].rstrip() + '…'


def catalog(metas):
    rows = ['| Skill | What it does |', '|-------|--------------|']
    rows += [f'| [`{m["name"]}`](skills/{m["name"]}/SKILL.md) | {one_line(m["description"])}. |' for m in metas]
    return '\n'.join(rows)


def main(argv):
    write = '--write-catalog' in argv
    only = [a for a in argv if not a.startswith('--')]
    dirs = sorted(p for p in SKILLS.iterdir() if p.is_dir() and not p.name.startswith(('.', '_')))
    if only:
        dirs = [d for d in dirs if d.name in only]
    errs, metas = [], []
    for d in dirs:
        e, meta = check_skill(d)
        errs += e
        if meta:
            metas.append(meta)
        print(f'{"FAIL" if e else "ok  "}  {d.name}')
    if not only:
        errs += check_repo()
        if README.exists():
            text = README.read_text()
            if CAT_BEGIN in text and CAT_END in text:
                a, b = text.index(CAT_BEGIN) + len(CAT_BEGIN), text.index(CAT_END)
                table = '\n' + catalog(metas) + '\n'
                if text[a:b] != table:
                    if write:
                        README.write_text(text[:a] + table + text[b:])
                        print('README catalog rewritten')
                    else:
                        errs.append('README.md: skill catalog is stale (run scripts/validate.py --write-catalog)')
            else:
                errs.append('README.md: catalog markers missing')
    for e in errs:
        print('  -', e)
    print(f'{len(dirs)} skill(s), {len(errs)} error(s)')
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
