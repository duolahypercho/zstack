# the two side views disagree on the front-deck crest (camera noise): split the difference (+2 cm net),
# and lift the A-pillar base region where coupe_left still reads low
K.edit_cage(lambda bm: K.move(bm, rows=range(3, 9), cols=range(15, 18), dz=-0.02, falloff_rows=1))
K.edit_cage(lambda bm: K.move(bm, rows=range(7, 10), cols=range(14, 19), dz=0.03, falloff_rows=1))
