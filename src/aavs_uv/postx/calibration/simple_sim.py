from __future__ import annotations
import numpy as np
from astropy.constants import c
from astropy.coordinates import get_sun
from aavs_uv.vis_utils import  vis_arr_to_matrix
from ..sky_model import RadioSource

LIGHT_SPEED = c.to('m/s').value
cos, sin = np.cos, np.sin

def simulate_visibilities(ant_arr: ApertureArray, sky_model: dict):
    """ Simulate model visibilities for an antenna array

    Args:
        ant_arr (AntArray): Antenna array to use
        sky_model (dict): Sky model to use

    Returns:
        model_vis_matrix (np.array): Model visibilities that should be expected given the known applied delays, (Nchan, Nant, Nant)
    """
    phsmat = None
    for srcname, src in sky_model.items():
        phs = ant_arr.generate_phase_vector(src, conj=True).squeeze()
        if hasattr(src, "mag"):
            phs *= np.sqrt(src.mag)

        if phsmat is None:
            phsmat = np.outer(phs, np.conj(phs))
        else:
            phsmat += np.outer(phs, np.conj(phs))
    return phsmat
