"""Capture-specific surface placement proxies, distinct from sensor axes.

Pelvis anterior location was explicitly confirmed by the operator on 2026-09-06.
Lateral ankle placement is in the sealed C2 donning record and amendment 004.
Mesh points are uncertain geometry proxies, never measured sensor centres.
"""
C2_VERTICES = [3160,1962,5431,1135,4621]

PLACEMENT_EVIDENCE = {
    'pelvis': 'anterior abdomen/belt; direct user confirmation 2026-09-06; published belt vertex 3160',
    'forearms': 'above protruding wrist joint; original published wrist surface proxies retained',
    'shanks': 'lateral, not anterior; sealed C2 donning and wear amendment 004',
    'shank_vertex_selection': 'same axial band as old above-ankle proxy within 10 mm; extreme lateral SMPL surface vertex, template transverse radius below 100 mm',
    'axial_sensor_distance': 'not measured; old above-ankle height preserved to isolate known surface correction',
    'uncertainty': 'surface model, sensor-centre and exact attachment offsets remain uncertain; physical calibration must validate them',
    'orientation_observation_model': 'calibrated rigid bone rotation; placement vertex does not convert the orientation to a deforming surface frame',
    'pelvis_surface_coupling': 'not modeled; front-belt SMPL surface deformation is a model sensitivity, not measured belt-to-pelvis motion',
}
