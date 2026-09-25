# fender crest stands above the lowered hood near the cowl; upper nose face set back
def crest(bm):
    K.move(bm, rows=range(5, 9), cols=range(15, 18), dz=0.05, falloff_rows=1)
def nose(bm):
    for v in K.verts(bm, rows=[-1, 0, 1]):
        if v.co.z > 0.30:
            k = min(1.0, (v.co.z - 0.30) / 0.25)
            lr = bm.verts.layers.int['cage_row']
            v.co.y += (0.07 if v[lr] <= 0 else 0.03) * k
K.edit_cage(crest)
K.edit_cage(nose)
