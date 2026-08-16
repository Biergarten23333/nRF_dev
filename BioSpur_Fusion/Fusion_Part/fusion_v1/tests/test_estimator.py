import ast, pathlib, numpy as np
from scipy.spatial.transform import Rotation
from fusion_v1.estimation.minimal import interpolate_root
from fusion_v1.factors.residuals import uwb_range,orientation,joint_center,dominant_axis
def test_root_interpolation():
 p,_,_=interpolate_root(np.array([0,10]),np.array([[0,0,0],[2,0,0.]]),np.array([5])); assert np.allclose(p,[ [1,0,0] ])
def test_range_residual(): assert uwb_range([3,0,0],[0,0,0],2)==1
def test_orientation_residual(): assert np.allclose(orientation(Rotation.identity(),Rotation.identity()),0)
def test_soft_joint_and_axis():
 assert np.allclose(joint_center([1,0,0],[0,0,0],.5),[2,0,0]); assert np.allclose(dominant_axis([2,1,0],[1,0,0],.5),[0,2,0])
def test_no_old_body_import_and_no_node_xyz_state():
 src=pathlib.Path('fusion_v1/estimation/minimal.py').read_text(); assert 'biospur_fusion' not in src
 tree=ast.parse(src); assert 'node_xyz' not in {n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}
