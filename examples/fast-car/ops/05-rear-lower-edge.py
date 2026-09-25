# the rear lower edge (bumper / diffuser line) reads ~4 cm too high in the rear three-quarter photo
def tail(bm):
    lr = bm.verts.layers.int['cage_row']
    for v in bm.verts:
        r = v[lr]
        if r == -2 or r >= 20:
            if v.co.z < 0.40:
                wr = 1.0 if (r == -2 or r >= 22) else (0.6 if r == 21 else 0.3)
                wz = 1.0 - max(0.0, (v.co.z - 0.20) / 0.20)
                v.co.z -= 0.04 * wr * wz
K.edit_cage(tail)
