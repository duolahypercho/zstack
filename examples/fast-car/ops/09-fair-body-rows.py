# fair the body rows: the seed refit carried the curve fit's small ripples into the surface
# (bullseyes and wobble in the stripe checks); Taubin smoothing keeps the volume. The nose and
# tail caps and their rings stay put so the overall length holds.
K.edit_cage(lambda bm: K.fair(bm, rows=range(1, 23), iterations=8))
