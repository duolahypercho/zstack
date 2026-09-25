# the lower fascia sweeps up toward the front wheels: lift the outer lower nose corners
def corners(bm):
    lr = bm.verts.layers.int['cage_row']
    lc = bm.verts.layers.int['cage_col']
    for v in bm.verts:
        r, c = v[lr], v[lc]
        if r == -1 or 0 <= r <= 3:
            if v.co.z < 0.30 and v.co.x > 0.40:
                wx = min(1.0, (v.co.x - 0.40) / 0.45)
                wr = 1.0 if r <= 2 else 0.5
                wz = 1.0 - max(0.0, (v.co.z - 0.12) / 0.18)
                v.co.z += 0.10 * wx * wr * wz
K.edit_cage(corners)
