#!/usr/bin/env python3
"""zstack/vehicle - record, gate and hand out the reference views for one vehicle.

    python3 refs.py plan    <run>            the exact image_gen prompts, in order
    python3 refs.py adopt   <run> --view V --image PATH
                                           file a generated view and run the gate
    python3 refs.py check   <run>            gate every recorded view
    python3 refs.py status  <run>

The images come from the agent's built-in image generation tool, not from a
hosted API: image_gen needs no key, and the agent is the only thing that can
call it. So generation is the agent's step and this script is the bookkeeping
around it - it writes the prompt sheet the agent hands to image_gen, records
which views exist, and refuses to let a run start on a view that would break the
silhouette mask. calibrate.py cannot do this job: a view whose background is a
gradient, or whose car runs off the frame, produces a mask of the studio rather
than of the car, and the pixel-to-metre scale is then quietly wrong.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import critique  # noqa: E402

PROMPTS = json.loads(open(os.path.join(SKILL, 'templates', 'prompts.json')).read())
ORDER = ['side_left', 'front', 'rear', 'top', 'front34', 'rear34']
ORTHO = {'side_left', 'front', 'rear', 'top'}


def prompt_for(spec, view, with_ref):
    text = PROMPTS['common'].format(label=spec.get('label', 'car'), design=spec.get('design', ''))
    text += ' ' + PROMPTS['views'][view]
    text += (f" Real proportions: length {spec['length']} m, width {spec['width']} m, height {spec['height']} m,"
             f" wheelbase {spec['wheelbase']} m, front overhang {spec.get('frontOverhang', '?')} m.")
    if with_ref:
        text += ' ' + PROMPTS['consistency']
    return text


def load(run):
    spec = json.load(open(os.path.join(run, 'spec.json')))
    state_path = os.path.join(run, 'refs-state.json')
    state = json.load(open(state_path)) if os.path.exists(state_path) else {'views': {}}
    return spec, state, state_path


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd, run = argv[0], os.path.abspath(argv[1])
    os.makedirs(run, exist_ok=True)
    spec, state, state_path = load(run)
    refs = os.path.join(run, 'refs')

    if cmd == 'plan':
        os.makedirs(refs, exist_ok=True)
        plan = []
        for view in ORDER:
            with_ref = view != 'side_left' and 'side_left' in state['views']
            plan.append({'view': view, 'kind': 'ortho' if view in ORTHO else 'photo',
                         'withSideRef': with_ref, 'prompt': prompt_for(spec, view, with_ref)})
        out = {'spec': os.path.relpath(os.path.join(run, 'spec.json')),
               'order': ORDER,
               'note': 'Generate each view with the image_gen tool in this order. Feed the saved '
                       'side_left.png in as the input image for every later view. Write the result to '
                       'the path below, then run: refs.py adopt <run> --view <view> --image <path>',
               'views': plan}
        p = os.path.join(refs, 'plan.json')
        json.dump(out, open(p, 'w'), indent=1)
        print('wrote', p)
        for v in plan:
            print(f"\n=== {v['view']} ({v['kind']}, side_ref={v['withSideRef']}) ===\n{v['prompt']}")
        return 0

    if cmd == 'adopt':
        if '--view' not in argv or '--image' not in argv:
            sys.exit('adopt needs --view V --image PATH')
        view = argv[argv.index('--view') + 1]
        src = os.path.abspath(argv[argv.index('--image') + 1])
        if view not in ORDER:
            sys.exit(f'unknown view {view}; expected one of {", ".join(ORDER)}')
        os.makedirs(refs, exist_ok=True)
        dst = os.path.join(refs, f'{view}.png')
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
        with open(os.path.join(refs, 'prompts.jsonl'), 'a') as fh:
            fh.write(json.dumps({'view': view, 'source': 'codex built-in image_gen tool',
                                 'withSideRef': view != 'side_left'}) + '\n')
        print('recorded', dst)
        views_path = os.path.join(refs, 'views.json')
        views = json.load(open(views_path)) if os.path.exists(views_path) else {}
        views[view] = {'image': f'{view}.png', 'kind': 'ortho' if view in ORTHO else 'photo',
                       'axis': view if view in ORTHO else None}
        if view in ORTHO:
            views[view] = {'image': f'{view}.png', 'kind': 'ortho', 'axis': view}
        else:
            views[view] = {'image': f'{view}.png', 'kind': 'photo',
                           'bbox': None, 'anchors': {}}
        json.dump(views, open(views_path, 'w'), indent=1)
        state['views'][view] = {'image': dst, 'adopted': True}
        json.dump(state, open(state_path, 'w'), indent=1)
        print(f'gating {view}...')
        import check_refs
        rc = check_refs.main(['check_refs', run, '--refs', refs])
        return rc

    if cmd in ('check', 'status'):
        print(f'run    {run}')
        print(f'refs   {refs}')
        missing = [v for v in ORDER if v not in state['views']]
        for view in ORDER:
            rec = state['views'].get(view)
            kind = 'ortho' if view in ORTHO else 'photo'
            if rec:
                print(f'  {view:10s} {kind:6s} recorded  {os.path.basename(rec["image"])}')
            else:
                print(f'  {view:10s} {kind:6s} MISSING')
        if missing:
            print('\nnext: python3 refs.py plan ' + run)
        else:
            print('\nall six views recorded; now: python3 check_refs.py ' + run)
        return 1 if (cmd == 'status' and missing) else 0

    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
