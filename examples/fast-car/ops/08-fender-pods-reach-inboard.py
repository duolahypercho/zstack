# the fender pods reach inboard at the front as the hood narrows to its V: pull the inner fender
# wall and the hood-edge columns toward the centre over the front rows, lift the pod walls
def pods(bm):
    lr = bm.verts.layers.int['cage_row']
    lc = bm.verts.layers.int['cage_col']
    wrow = {0: 1.0, 1: 1.0, 2: 1.0, 3: 0.7, 4: 0.35}
    fx = {18: 0.80, 19: 0.72, 20: 0.66, 21: 0.72, 22: 0.78, 23: 0.82}
    dz = {18: 0.03, 19: 0.03, 20: 0.03}
    for v in bm.verts:
        w = wrow.get(v[lr])
        c = v[lc]
        if w is None or c not in fx:
            continue
        v.co.x += (v.co.x * fx[c] - v.co.x) * w
        v.co.z += dz.get(c, 0.0) * w
K.edit_cage(pods)
