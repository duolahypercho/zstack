# three views read the nose as too big: set the upper nose face back 3 cm more, drop the nose-most crest 1.5 cm
def nose(bm):
    lr = bm.verts.layers.int['cage_row']
    for v in bm.verts:
        if v[lr] in (-1, 0, 1) and v.co.z > 0.25:
            k = min(1.0, (v.co.z - 0.25) / 0.25)
            v.co.y += (0.03 if v[lr] <= 0 else 0.015) * k
K.edit_cage(nose)
K.edit_cage(lambda bm: K.move(bm, rows=range(0, 3), cols=range(14, 19), dz=-0.015, falloff_rows=1))
