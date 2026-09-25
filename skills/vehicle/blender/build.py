"""zstack/vehicle - build a vehicle from its run directory, end to end.

    blender -b -P <skill>/blender/build.py -- --run runs/<id> [--stages model,checks,export,render]
            [--views front34,side_left] [--preview] [--samples 96]

Inside Blender MCP (live GUI), the same thing:
    import sys; sys.path.insert(0, '<skill>/blender'); import build; build.main(['--run', 'runs/<id>'])

Reads runs/<id>/spec.json, curves.json, parts.json. Writes:
    runs/<id>/<id>.blend                  the rigged model
    runs/<id>/checks/doors.json           door sweep (door_check.py)
    runs/<id>/checks/crush.json           crumple-zone morph check (crush.py)
    runs/<id>/checks/model_tris.npz       world triangles for scripts/critique.py
    runs/<id>/checks/build.json           counts, timings, pass/fail summary
    runs/<id>/<id>.glb                    export (+ checks/doors-reimport.json)
    runs/<id>/renders/*.png               views
"""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bpy  # noqa: E402

import body  # noqa: E402
import common as C  # noqa: E402
import crush  # noqa: E402
import details  # noqa: E402
import door_check  # noqa: E402
import engine  # noqa: E402
import export  # noqa: E402
import interior  # noqa: E402
import panels  # noqa: E402
import render_views  # noqa: E402
import rig  # noqa: E402
import wheels  # noqa: E402


def load(run):
    j = lambda n: json.load(open(os.path.join(run, n)))  # noqa: E731
    spec, curves, parts = j('spec.json'), j('curves.json'), j('parts.json')
    return spec, curves, parts


def model(run, spec, curves, parts, log):
    C.reset_scene()
    mats = C.materials(parts.get('materials'))
    t = time.time()
    if curves.get('surface') == 'mesh':
        # hand-shaped cage (cage_kit.py through Blender MCP): mirror + subdivide + apply
        shell = body.build_from_cage(os.path.join(run, curves.get('cage', 'cage.json')), curves.get('subdivLevels', 3))
        log['cageOpenEdges'] = shell.get('cageOpenEdges')
    else:
        shell = body.build(curves, 'BodyShell', step=parts.get('step', 0.021))
    log['photoParts'] = panels.resolve_photo_parts(parts, run, shell)
    panels.shell(shell, mats, parts.get('wall', 0.004))
    panels.use_body(body.BodySpec(curves))
    liners = panels.arches(shell, parts.get('arches', []), mats)
    log['shell_s'] = round(time.time() - t, 1)
    t = time.time()
    glass = panels.openings(shell, parts.get('openings', []), mats)
    log['openings_s'] = round(time.time() - t, 1)
    t = time.time()
    rec = panels.recesses(shell, parts.get('recesses', []), mats)
    log['recesses_s'] = round(time.time() - t, 1)
    t = time.time()
    pre_cut = C.duplicate(shell, '_pre_cut')
    pre_cut.hide_set(True)
    body_ob, skins = panels.cut_panels(shell, parts.get('panels', []), parts.get('gap', 0.004))
    missing = list(body_ob.get('panelsMissing', []))
    log['panelsMissing'] = missing
    if missing:
        # a panel that does not separate is a failed build: find where its outline leaks
        log['panelLeaks'] = {m: panels.leak_report(pre_cut, next(p for p in panels.expand(parts['panels']) if p['name'] == m))
                             for m in missing}
    bpy.data.objects.remove(pre_cut, do_unlink=True)
    log['panels_s'] = round(time.time() - t, 1)
    log['panelsSeparated'] = sorted(skins)
    log['paintZones'] = panels.paint_zones([body_ob] + list(skins.values()), parts.get('paintZones', []), mats)
    # extras that ride with each panel
    extras = {}
    for g in glass.values():
        if g.get('parentPanel'):
            extras.setdefault(g['parentPanel'], []).append(g)
    for objs in rec.values():
        for o in objs:
            par = next((p['name'] for p in panels.expand(parts.get('recesses', [])) if p['name'] == o['recess'] and p.get('parent')), None)
            if par:
                pdef = next(p for p in panels.expand(parts['recesses']) if p['name'] == o['recess'])
                extras.setdefault(pdef['parent'], []).append(o)
    for par, objs in details.mirrors(parts.get('mirrors', []), mats).items():
        extras.setdefault(par, []).extend(objs)
    for name, skin in skins.items():
        if name.startswith('Door_'):
            extras.setdefault(name, []).extend(interior.door_card('DoorCard' + name[4:], skin, mats))
    log['sealsTrimmed'] = panels.trim_seals(glass, {n: [sk] + extras.get(n, []) for n, sk in skins.items()})
    ext = (details.exhaust(parts.get('exhaust', {}), mats) + details.blades(parts.get('blades', []), mats)
           + details.spoiler(parts.get('spoiler'), mats))
    wheel_pivots = wheels.build(spec, mats)
    cab = interior.build(spec, parts['cabin'], mats) if parts.get('cabin') else {'meshes': []}
    eng = []
    if parts.get('engine'):
        e = json.loads(json.dumps(parts['engine']))
        bay = e.get('bay')
        if bay:
            # the bay follows the body: its walls stop 10 cm under the lowest deck line above them,
            # so reshaping the body can never drive the engine cover into the bay
            bs = body.BodySpec(curves)
            ys = [bay['y'][0] + (bay['y'][1] - bay['y'][0]) * i / 20 for i in range(21)]
            bay['z'][1] = min(bay['z'][1], min(bs.keys(y)[8][1] for y in ys) - 0.10)
        eng = engine.build(e, mats)
    pivots = rig.build(panels.expand(parts.get('panels', [])), skins, extras)
    # crush morphs on the exterior: body, panels and everything that rides on them
    wheel_meshes = [o for p in wheel_pivots.values() for o in _desc(p) if o.type == 'MESH']
    # the field is zero outside the crumple zones, so keying the engine bay is free, and it keeps
    # parts that sit inside a zone (front radiator fans) from poking out of a crushed nose
    skip = set(wheel_meshes) | set(cab['meshes'])
    exterior = [o for o in bpy.data.objects if o.type == 'MESH' and o not in skip and o.get('zstack') not in ('interior', 'stage')]
    C.update()
    crush.add_keys(exterior, spec, parts.get('crush'))
    log['crushKeyed'] = len(exterior)
    body_ob.data.materials[0] = mats['Paint']
    return {'pivots': pivots, 'wheels': wheel_pivots, 'exterior': exterior, 'wheel_meshes': wheel_meshes, 'cabin': cab,
            'body': body_ob}


def save_blend(path):
    """Write the scene and its data, but no UI / window-manager state: a regular save also stores
    operator memory such as the last file-browser directory (the author's home path)."""
    bpy.data.libraries.write(path, {bpy.context.scene}, path_remap='RELATIVE_ALL', fake_user=True, compress=True)


def _desc(ob):
    out = []
    for c in ob.children:
        out.append(c)
        out += _desc(c)
    return out


def checks(run, spec, parts, st, log):
    os.makedirs(os.path.join(run, 'checks'), exist_ok=True)
    d = door_check.check(os.path.join(run, 'checks', 'doors.json'))
    cz = parts.get('crushCabin') or {'y': [parts['cabin']['front'] + 0.05, parts['cabin']['rear']],
                                     'halfWidth': 0.55, 'z': [parts['cabin']['floorZ'], 1.0]}
    c = crush.check(st['exterior'], spec, cz, st['wheel_meshes'])
    json.dump(c, open(os.path.join(run, 'checks', 'crush.json'), 'w'), indent=2)
    n = export.silhouette_tris(os.path.join(run, 'checks', 'model_tris.npz'))
    export.silhouette_tris(os.path.join(run, 'checks', 'model_tris_lo.npz'), ratio=0.12)
    # parts the body curves do not shape (wheels, mirrors, spoiler, blades, exhaust): critique.py fitshape
    # rasterises these once and re-lofts only the body per candidate
    export.silhouette_tris(os.path.join(run, 'checks', 'model_tris_fixed.npz'), ratio=0.25,
                           include=lambda o: o.get('zstack') in ('wheel', 'mirror', 'exterior'))
    log['doors'] = {k: v['pass'] for k, v in d['parts'].items()}
    log['doorsPass'] = d['pass']
    log['doorFailures'] = {k: v['fail'] for k, v in d['parts'].items() if v['fail']}
    log['crushPass'] = c['pass']
    log['crush'] = c['keys']
    log['silhouetteTris'] = n
    log['fairness'] = fairness(st.get('body'))


def fairness(body, crease_deg=25.0):
    """Surface fairness of the painted body (the reflection-quality proxy): for each outer-skin
    vertex, the angle between its normal and its neighbours' mean normal. Ripples raise it; a clean
    subdivision-style surface keeps it low. Vertices on cut edges or real creases (> crease_deg)
    are excluded. Returns degrees: p50, p95 and the fraction of vertices over 3 degrees."""
    import bmesh
    import math as _m
    if body is None:
        return None
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.normal_update()
    vals = []
    for v in bm.verts:
        fs = v.link_faces
        if not fs or any(f.material_index != 0 for f in fs) or v.is_boundary:
            continue
        nb = [e.other_vert(v) for e in v.link_edges]
        if len(nb) < 3:
            continue
        mean = sum((u.normal for u in nb), v.normal * 0)
        if mean.length < 1e-9:
            continue
        a = _m.degrees(v.normal.angle(mean.normalized(), 0.0))
        if a < crease_deg:
            vals.append(a)
    bm.free()
    if not vals:
        return None
    vals.sort()
    return {'p50Deg': round(vals[len(vals) // 2], 3), 'p95Deg': round(vals[int(len(vals) * 0.95)], 3),
            'over3Deg': round(sum(1 for x in vals if x > 3.0) / len(vals), 4), 'vertices': len(vals)}


def main(argv=None):
    argv = argv if argv is not None else (sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    args = {'--stages': 'model,checks,export,render', '--views': '', '--samples': '96'}
    flags = set()
    i = 0
    while i < len(argv):
        if argv[i] in ('--preview',):
            flags.add(argv[i])
            i += 1
        else:
            args[argv[i]] = argv[i + 1]
            i += 2
    run = os.path.abspath(args['--run'])
    stages = args['--stages'].split(',')
    spec, curves, parts = load(run)
    vid = spec['id']
    log = {'id': vid, 'blender': bpy.app.version_string}
    t0 = time.time()
    st = model(run, spec, curves, parts, log)
    log['triangles'] = C.tri_count([o for o in bpy.data.objects if o.get('zstack') != 'stage'])
    if 'checks' in stages:
        checks(run, spec, parts, st, log)
    save_blend(os.path.join(run, f'{vid}.blend'))
    if 'export' in stages:
        log['export'] = export.export(run, vid, st['pivots'])
    if 'render' in stages:
        views = [v for v in args['--views'].split(',') if v] or None
        render_views.render(run, views, 'preview' if '--preview' in flags else 'beauty',
                            samples=int(args['--samples']), pivots=st['pivots'])
    log['seconds'] = round(time.time() - t0, 1)
    log['pass'] = bool(log.get('doorsPass', False) and log.get('crushPass', False) and not log.get('panelsMissing')
                       and log.get('export', {}).get('reimport', {}).get('doorsPass', 'export' not in stages))
    os.makedirs(os.path.join(run, 'checks'), exist_ok=True)
    json.dump(log, open(os.path.join(run, 'checks', 'build.json'), 'w'), indent=2)
    print(json.dumps(log, indent=1))
    print('BUILD', 'PASS' if log['pass'] else 'FAIL')
    return log


if __name__ == '__main__':
    main()
