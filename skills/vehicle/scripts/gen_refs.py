#!/usr/bin/env python3
"""Generate consistent all-angle reference views for one vehicle.

    python gen_refs.py probe                  -> prints "available <provider>" or "missing"
    python gen_refs.py all  runs/<id>         -> side_left first, then every other view
    python gen_refs.py view runs/<id> front   -> (re)generate one view

Providers (first one with a key wins; stdlib only, no SDK needed):
    GEMINI_API_KEY   Google Gemini image model   (ZSTACK_GEMINI_MODEL, default gemini-2.5-flash-image)
    OPENAI_API_KEY   OpenAI image edits/generate  (ZSTACK_OPENAI_MODEL, default gpt-image-1)

The side view is generated from text alone. Every other view is generated with
the side view attached as an input image plus a "same car" instruction, which is
what keeps six calls drawing one design instead of six different cars.
Keys are read from the environment only and never written anywhere.
"""
import base64
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPTS = json.loads((HERE.parent / 'templates' / 'prompts.json').read_text())
ORDER = ['side_left', 'front', 'rear', 'top', 'front34', 'rear34']


def provider():
    if os.environ.get('GEMINI_API_KEY'):
        return 'gemini'
    if os.environ.get('OPENAI_API_KEY'):
        return 'openai'
    return None


def post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', **headers})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def gemini(prompt, ref_png=None):
    model = os.environ.get('ZSTACK_GEMINI_MODEL', 'gemini-2.5-flash-image')
    parts = [{'text': prompt}]
    if ref_png:
        parts.append({'inline_data': {'mime_type': 'image/png', 'data': base64.b64encode(ref_png).decode()}})
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    out = post_json(url, {'contents': [{'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE', 'TEXT']}},
                    {'x-goog-api-key': os.environ['GEMINI_API_KEY']})
    for cand in out.get('candidates', []):
        for part in cand.get('content', {}).get('parts', []):
            data = part.get('inline_data') or part.get('inlineData')
            if data:
                return base64.b64decode(data['data'])
    raise RuntimeError('Gemini returned no image: ' + json.dumps(out)[:400])


def openai(prompt, ref_png=None):
    model = os.environ.get('ZSTACK_OPENAI_MODEL', 'gpt-image-1')
    key = os.environ['OPENAI_API_KEY']
    if ref_png is None:
        out = post_json('https://api.openai.com/v1/images/generations',
                        {'model': model, 'prompt': prompt, 'size': '1536x1024'}, {'Authorization': f'Bearer {key}'})
    else:
        boundary = uuid.uuid4().hex
        body = b''
        for name, value in (('model', model), ('prompt', prompt), ('size', '1536x1024')):
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="ref.png"\r\n'
                 'Content-Type: image/png\r\n\r\n').encode() + ref_png + f'\r\n--{boundary}--\r\n'.encode()
        req = urllib.request.Request('https://api.openai.com/v1/images/edits', data=body,
                                     headers={'Authorization': f'Bearer {key}', 'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.loads(r.read())
    return base64.b64decode(out['data'][0]['b64_json'])


def build_prompt(spec, view, with_ref):
    text = PROMPTS['common'].format(label=spec.get('label', 'car'), design=spec.get('design', ''))
    text += ' ' + PROMPTS['views'][view]
    text += (f" Real proportions: length {spec['length']} m, width {spec['width']} m, height {spec['height']} m,"
             f" wheelbase {spec['wheelbase']} m, front overhang {spec.get('frontOverhang', '?')} m.")
    if with_ref:
        text += ' ' + PROMPTS['consistency']
    return text


def generate(run, view):
    run = Path(run)
    spec = json.loads((run / 'spec.json').read_text())
    refs = run / 'refs'
    refs.mkdir(parents=True, exist_ok=True)
    side = refs / 'side_left.png'
    ref_png = side.read_bytes() if view != 'side_left' and side.exists() else None
    prompt = build_prompt(spec, view, ref_png is not None)
    p = provider()
    if p is None:
        sys.exit('missing: no image model key (GEMINI_API_KEY / OPENAI_API_KEY); use the web-reference path in SKILL.md')
    img = gemini(prompt, ref_png) if p == 'gemini' else openai(prompt, ref_png)
    (refs / f'{view}.png').write_bytes(img)
    log = refs / 'prompts.jsonl'
    with open(log, 'a') as fh:
        fh.write(json.dumps({'view': view, 'provider': p, 'prompt': prompt, 'withSideRef': ref_png is not None}) + '\n')
    print('wrote', refs / f'{view}.png')


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == 'probe':
        p = provider()
        print(f'available {p}' if p else 'missing')
    elif cmd == 'all':
        for v in ORDER:
            generate(sys.argv[2], v)
    elif cmd == 'view':
        generate(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
