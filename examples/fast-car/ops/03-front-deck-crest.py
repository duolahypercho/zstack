# the front-deck silhouette (fender crests) sits ~5 cm higher than the model from the front wheel to the A-pillar
K.edit_cage(lambda bm: K.move(bm, rows=range(3, 9), cols=range(15, 18), dz=0.04, falloff_rows=1))
