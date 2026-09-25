"""zstack/vehicle - one hand-shaping round on the body cage, headless or from Blender MCP.

    blender -b --factory-startup -P <skill>/blender/cage_round.py -- --run runs/<id> --tag r03
            [--ops ops/r03-crest.py ...] [--seed] [--replay] [--refs DIR] [--no-renders]

Default: load runs/<id>/cage.json, apply each --ops file in order, then measure, export, checkpoint
and render the checks. --seed starts from a fresh cage seeded from curves.json (the ops history is
reset to the given ops); --replay rebuilds the cage from curves.json plus every op listed in
runs/<id>/cage_ops.json, so the accepted cage can always be regenerated and reviewed edit by edit.

An op file is plain Python run with `K` (cage_kit), `RUN` and `REFS` defined, e.g.
    K.edit_cage(lambda bm: K.move(bm, rows=range(3, 9), cols=range(15, 18), dz=0.04, falloff_rows=1))
Keep each op small and say in a comment which reference it answers.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bpy  # noqa: E402

import cage_kit as K  # noqa: E402


def _apply(run, op, refs):
    path = op if os.path.isabs(op) else os.path.join(run, op)
    print(json.dumps({'op': os.path.relpath(path, run)}))
    exec(compile(open(path).read(), path, 'exec'), {'K': K, 'RUN': run, 'REFS': refs, 'bpy': bpy, '__name__': 'cage_op'})


def main(argv=None):
    argv = argv if argv is not None else (sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    args, ops, flags = {}, [], set()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--seed', '--replay', '--no-renders'):
            flags.add(a)
            i += 1
        elif a == '--ops':
            i += 1
            while i < len(argv) and not argv[i].startswith('--'):
                ops.append(argv[i])
                i += 1
        else:
            args[a] = argv[i + 1]
            i += 2
    run = os.path.abspath(args['--run'])
    tag = args.get('--tag', 'round')
    refs = os.path.abspath(args['--refs']) if args.get('--refs') else None
    hist_path = os.path.join(run, 'cage_ops.json')
    history = json.load(open(hist_path))['ops'] if os.path.exists(hist_path) else []
    K.setup(run)
    if '--replay' in flags:
        K.cage_from_curves(run)
        for op in history:
            _apply(run, op, refs)
    elif '--seed' in flags or not os.path.exists(os.path.join(run, 'cage.json')):
        K.cage_from_curves(run)
        history = []
    else:
        K.load_cage(run, levels=json.load(open(os.path.join(run, 'curves.json'))).get('subdivLevels', 2))
    for op in ops:
        _apply(run, op, refs)
        history.append(os.path.relpath(op if os.path.isabs(op) else os.path.join(run, op), run))
    json.dump({'note': 'curves.json seeds the cage; these ops, in order, reshape it (cage_round.py --replay)',
               'ops': history}, open(hist_path, 'w'), indent=1)
    out = {'metrics': K.metrics(run)}
    K.export_cage(run)
    K.checkpoint(run, tag)
    if '--no-renders' not in flags:
        if refs and os.path.exists(os.path.join(run, 'checks', 'cameras.json')):
            out['overlays'] = K.photo_overlays(run, refs, tag)
        out['checks'] = K.render_checks(run, tag, views=('front34', 'side', 'front', 'top'),
                                        matcaps=('check_reflection_horizontal.exr',), res=(960, 540))
    return out


if __name__ == '__main__':
    main()
