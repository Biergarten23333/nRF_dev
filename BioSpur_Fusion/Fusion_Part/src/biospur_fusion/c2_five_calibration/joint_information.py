"""Assemble registered linear rows and retain joint calibration in a stream."""
from dataclasses import dataclass
import time
import numpy as np
from scipy.sparse import csr_matrix,hstack,vstack
from .operators import WIDTH
from .streaming_information import StreamingInformation


@dataclass(frozen=True)
class JointLinearization:
    matrix: csr_matrix
    residual: np.ndarray
    start: np.ndarray
    stop: np.ndarray
    pose_columns: int

    def stream(self, point, *, pose_width=21, chunk_frames=20, deadline=None):
        point=np.asarray(point,float)
        if (self.pose_columns%pose_width or chunk_frames<WIDTH
                or point.shape!=(self.matrix.shape[1],)):
            raise ValueError('full-frame point and adequate temporal chunks required')
        frames=self.pose_columns//pose_width
        shared=list(range(self.pose_columns,self.matrix.shape[1]))
        state=StreamingInformation(shared,max_columns=(chunk_frames+WIDTH-1)*pose_width+len(shared))
        counts=np.zeros(len(self.residual),dtype=int)
        for lo in range(0,frames,chunk_frames):
            if deadline is not None and time.monotonic()>deadline:
                raise TimeoutError('joint elimination deadline reached')
            hi=min(lo+chunk_frames,frames);read=max(0,lo-WIDTH+1)
            rows=((self.stop>lo)&(self.stop<=hi)) | ((self.stop<0)&(lo==0))
            if np.any(rows&(self.start>=0)&(self.start<read)):
                raise ValueError('separator omits declared historical support')
            columns=list(range(read*pose_width,hi*pose_width))+shared
            keep=list(range(max(0,hi-WIDTH+1)*pose_width,hi*pose_width))+shared
            outside=np.setdiff1d(np.arange(self.matrix.shape[1]),columns)
            if self.matrix[rows][:,outside].nnz:
                # Explicit stored zeros are harmless, nonzero omitted columns are not.
                if np.any(self.matrix[rows][:,outside].data!=0):
                    raise ValueError('factor depends on an omitted coordinate')
            state.append(self.matrix[rows][:,columns].toarray(),self.residual[rows],columns,point[columns],keep)
            counts[rows]+=1
        if np.any(counts!=1):raise ValueError('each original residual must be consumed exactly once')
        return state


def assemble_joint_rows(pose, actions, shared_jacobian, shared_residual):
    """Column order: per-frame pose, heading coefficients, fifteen levers."""
    p,h=pose['pose'].shape[1],pose['heading'].shape[1]
    shared_jacobian=np.asarray(shared_jacobian,float);shared_residual=np.asarray(shared_residual,float)
    if (actions['pose'].shape[1]!=p or actions['heading'].shape[1]!=h
            or pose['levers'].shape[1]!=15 or shared_jacobian.shape!=(len(shared_residual),h+15)):
        raise ValueError('all owners must use identical coordinate layouts')
    matrix=vstack((hstack((pose['pose'],pose['heading'],pose['levers'])),
                   hstack((actions['pose'],actions['heading'],csr_matrix((len(actions['residual']),15)))),
                   hstack((csr_matrix((len(shared_residual),p)),csr_matrix(shared_jacobian)))),format='csr')
    residual=np.concatenate((pose['residual'],actions['residual'],shared_residual))
    start=np.concatenate((pose['start'],actions['start'],np.full(len(shared_residual),-1)))
    stop=np.concatenate((pose['stop'],actions['start']+1,np.full(len(shared_residual),-1)))
    if (matrix.shape[0]!=len(residual) or start.shape!=residual.shape or stop.shape!=residual.shape
            or not np.isfinite(matrix.data).all() or not np.isfinite(residual).all()):
        raise ValueError('finite complete row ownership required')
    return JointLinearization(matrix,residual,start,stop,p)
