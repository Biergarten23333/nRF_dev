# SDP+NLS Standalone Results

Output directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_v4_20260504/sdp_solve_20260506_171743`

cvxpy version: `1.8.2`; installed solvers: `['CLARABEL', 'HIGHS', 'OSQP', 'SCIPY', 'SCS']`

## SDP vs Other Solvers - ID02 3D std (mm)

| Config | SDP+NLS | MDS+NLS | Ridolfi | V4-io | V3-full |
| --- | --- | --- | --- | --- | --- |
| Dual-layer 8anc | 45.6 | 41.3 | 41.3 | 40.8 | 40.7 |
| Upper only EFGH | 109.5 | 109.5 | 109.5 | 109.7 | 109.7 |
| Lower only ABCD | 67.6 | 67.6 | 67.6 | 67.0 | 67.0 |
| Best6 no DH | 48.3 | 44.0 | 44.0 | 41.5 | 44.4 |
| Upper+AB | 48.6 | 48.6 | 48.6 | 48.6 | 47.9 |
| Lower+EF | 43.9 | 43.9 | 43.9 | 43.3 | 43.5 |


## SDP Diagnostics

| Config | Solver | Status | Objective | Top 6 eigenvalues | Rank-3 gap | Initial RMS | Final RMS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Dual-layer 8anc | SCS | optimal | 0.521487 | 40.1064, 17.3070, 11.2122, 4.7618, 0.6340, 0.3700 | 0.4247 | 599.1 | 209.3 |
| Upper only EFGH | SCS | optimal | 0.000000 | 20.1084, 8.9764, 7.9370, 0.1450 | 0.0183 | 16.8 | 0.0 |
| Lower only ABCD | SCS | optimal | 0.000000 | 19.6893, 8.8993, 7.8943, 0.4885 | 0.0619 | 56.4 | 0.0 |
| Best6 no DH | SCS | optimal | 0.201074 | 30.7009, 9.4605, 7.8477, 3.5238, 0.5964, 0.0000 | 0.4490 | 670.8 | 189.6 |
| Upper+AB | SCS | optimal | 0.093065 | 30.3384, 12.0989, 9.9015, 2.8879, 0.2353, -0.0000 | 0.2917 | 506.9 | 15.4 |
| Lower+EF | SCS | optimal | 0.450228 | 30.0017, 12.8629, 10.0706, 2.6647, 0.4128, -0.0000 | 0.2646 | 441.6 | 28.4 |


## Key Question

Does SDP initialization give better results than MDS initialization?

- Dual-layer 8anc: SDP+NLS ID02 3D=45.6 mm vs MDS+NLS=41.3 mm; difference=+4.3 mm.
- Upper only EFGH: SDP+NLS ID02 3D=109.5 mm vs MDS+NLS=109.5 mm; difference=-0.0 mm.
- Lower only ABCD: SDP+NLS ID02 3D=67.6 mm vs MDS+NLS=67.6 mm; difference=+0.0 mm.
- Best6 no DH: SDP+NLS ID02 3D=48.3 mm vs MDS+NLS=44.0 mm; difference=+4.3 mm.
- Upper+AB: SDP+NLS ID02 3D=48.6 mm vs MDS+NLS=48.6 mm; difference=+0.0 mm.
- Lower+EF: SDP+NLS ID02 3D=43.9 mm vs MDS+NLS=43.9 mm; difference=-0.0 mm.

If SDP+NLS and MDS+NLS converge to the same ID02 3D std, then initialization method is not the limiting factor for this dataset.