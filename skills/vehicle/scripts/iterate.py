#!/usr/bin/env python3
"""zstack/vehicle - the critique loop, automated: build -> score -> suggest -> apply, repeated.

    python3 iterate.py <run> --refs DIR [--rounds 6] [--gain 0.7] [--edges top] [--blender PATH]

Cameras are NOT refitted between rounds (run `critique.py fit` once, and again only after large
shape changes): a camera that re-solves every round would absorb the very shape error the loop is
trying to remove. Each round's curves are backed up; if a round lowers the mean IoU, its curves are
reverted and the gain is halved. Stops when every view passes its gate, when the gain falls below
0.1, or after --rounds rounds. The door and crush checks run in every build; a round that breaks
them is reverted too.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main(argv):
    if len(argv) < 1:
        print(__doc__)
        return 2
    run_dir = os.path.abspath(argv[0])
    if json.load(open(os.path.join(run_dir, 'curves.json'))).get('surface') == 'mesh':
        print('iterate: the body is a hand-shaped cage (curves.json "surface": "mesh"); this loop edits curves. '
              'Score with critique.py score and fix the cage by hand (reference/hand-shaping.md).')
        return 2
    opts = {'--refs': None, '--rounds': '6', '--gain': '0.7', '--edges': 'top', '--blender': 'blender'}
    for i in range(1, len(argv) - 1, 2):
        opts[argv[i]] = argv[i + 1]
    rounds, gain = int(opts['--rounds']), float(opts['--gain'])
    crit = [sys.executable, os.path.join(HERE, 'critique.py')]
    refs = ['--refs', opts['--refs']] if opts['--refs'] else []
    rounds_path = os.path.join(run_dir, 'checks', 'rounds.json')
    best = None
    if os.path.exists(rounds_path):      # the last scored round (before this run's first apply) is the bar to beat
        last = json.load(open(rounds_path))[-1]
        backup = os.path.join(run_dir, 'checks', f"curves.round{last['round']:02d}.json")
        if os.path.exists(backup):
            best = (last['meanIoU'], last['round'])
            shutil.copy(backup, os.path.join(run_dir, 'checks', 'curves.best.json'))
    for n in range(rounds):
        code, out = run([opts['--blender'], '-b', '--factory-startup', '-P', os.path.join(SKILL, 'blender', 'build.py'),
                         '--', '--run', run_dir, '--stages', 'model,checks'])
        build = json.load(open(os.path.join(run_dir, 'checks', 'build.json')))
        rigs_ok = build.get('doorsPass') and build.get('crushPass') and not build.get('panelsMissing')
        code, out = run(crit + ['score', run_dir] + refs + ['--note', f'iterate round, gain {gain:.2f}'])
        rec = json.load(open(rounds_path))[-1]
        mean = rec['meanIoU']
        print(f"round {rec['round']}: mean IoU {mean:.4f}  " +
              '  '.join(f"{k} {v['iou']:.3f}" for k, v in rec['views'].items()) +
              f"  doors {'ok' if build.get('doorsPass') else 'FAIL'}  crush {'ok' if build.get('crushPass') else 'FAIL'}")
        if best is None or (mean > best[0] and rigs_ok):
            best = (mean, rec['round'])
            shutil.copy(os.path.join(run_dir, 'curves.json'), os.path.join(run_dir, 'checks', 'curves.best.json'))
        elif mean <= best[0] or not rigs_ok:
            shutil.copy(os.path.join(run_dir, 'checks', 'curves.best.json'), os.path.join(run_dir, 'curves.json'))
            gain *= 0.5
            print(f'  worse than round {best[1]}: reverted curves, gain -> {gain:.2f}')
            if gain < 0.1:
                break
        if rec['pass'] and rigs_ok:
            print('all gates pass')
            break
        run(crit + ['suggest', run_dir] + refs)
        code, out = run(crit + ['apply', run_dir, '--gain', f'{gain:.3f}', '--edges', opts['--edges']])
        print('  ' + out.strip().replace('\n', '\n  '))
    # leave the run on its best curves, rebuilt
    shutil.copy(os.path.join(run_dir, 'checks', 'curves.best.json'), os.path.join(run_dir, 'curves.json'))
    print(f'best: round {best[1]}, mean IoU {best[0]:.4f} (curves.json restored to it; rebuild to export)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
