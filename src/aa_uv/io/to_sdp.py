
import numpy as np
import pandas as pd
import h5py

from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time

from pyuvdata import UVData

from ska_sdp_datamodels.visibility import Visibility
from ska_sdp_datamodels.configuration import Configuration
from ska_sdp_datamodels.science_data_model import ReceptorFrame, PolarisationFrame

from aa_uv.io.to_uvx import hdf5_to_uvx
from aa_uv.uvw_utils import calc_uvw, calc_zenith_tracking_phase_corr, calc_zenith_apparent_coords

def hdf5_to_sdp_vis(fn_raw: str, yaml_config: str=None, telescope_name: str=None, conj: bool=True,
                    scan_id: int=0, scan_intent: str="", execblock_id: str="", flip_uvw=True,
                    apply_phasing: bool=True) -> Visibility:
    """ Generate a SDP Visibility object from a AAVS2 HDF5 file

    Args:
        fn_raw (str): Filename of raw HDF5 data to load.
        yaml_raw (str): YAML config data with telescope information.
        telescope_name (str=None): If set, aa_uv will try and use internal config file
                                   for telescope_name, e.g. 'aavs2' or 'aavs3'
        conj (bool): Conjugate visibility data (default True).
        flip_uvw (bool): Negate UVW coordinates (``uvw *= -1``). Default True, as needed to match
                         SDP convention (as of 2023/12).
        apply_phasing (bool): Apply phasing (zenith tracking corrections) to visibility data.
                              Default True. Needs to be applied for valid SDP data, generally
                              only set to False for testing purposes.
        scan_id (int): Optional. Adds scan ID to file.
        scan_intent (str): Optional. Adds description / intent of observation
        execblock_id (str): optional. Adds the execution block ID, e.g. eb-aavs-20231219-00001

    Notes:
        The HDF5 files generated by AAVS2/3 are NOT the same format as that found in
        ska-sdp-datamodels HDF5 visibilty specification.
        The AAVS DAQ receiver code in aavs-system has some info on the HDF5 format, here:
        https://gitlab.com/ska-telescope/aavs-system/-/blob/master/python/pydaq/persisters/corr.py

    """
    uv = hdf5_to_uvx(fn_raw, yaml_config=yaml_config, telescope_name=telescope_name, conj=conj)
    md = uv.provenance['input_metadata']


    # Create Configuration and Frames
    config    = Configuration()
    rec_frame = ReceptorFrame('linear')
    pol_frame = PolarisationFrame('linear')

    antpos_ENU  = uv.antennas.enu.values
    antpos_ECEF = uv.antennas.ecef.values
    telescope_earthloc = uv.origin

    config = config.constructor(
        name=md['telescope_name'],
        names=uv.antennas.attrs['identifier'].values,
        stations=[f'{x}' for x in range(md['n_antennas'])],
        location=telescope_earthloc,
        xyz=antpos_ECEF,
        receptor_frame=rec_frame,
        diameter=1.0,  # Errors if not set
        mount='altaz')

    baselines = pd.MultiIndex.from_arrays(
        (uv.data.baseline.ant1.values, uv.data.baseline.ant2.values), names=("antenna1", "antenna2")
    )

    # Time and frequency
    t  = uv.timestamps
    t_int = np.ones_like(t) * md['tsamp']
    fc = uv.data.frequency.values
    fbw = np.ones_like(fc) * uv.data.frequency.attrs['channel_bandwidth']

    # Phase center
    zen_sc = uv.phase_center.icrs
    source_name = f"Zenith_at_mjd_{t[0].mjd}"

    # UVW coordinates
    uvw = calc_uvw(uv)
    if flip_uvw:
        uvw *= -1

    # Load data from file
    vis_data = uv.data.transpose('time', 'baseline', 'frequency', 'polarization').values
    phs_corr = calc_zenith_tracking_phase_corr(uv)

    if apply_phasing:
        vis_data *= phs_corr

    # Create SDP visibility
    v = Visibility.constructor(
            uvw=uvw,
            time=t.mjd * 86400,
            frequency=fc,
            vis=vis_data,
            weight=np.ones(vis_data.shape),
            baselines=baselines,
            flags=np.zeros(vis_data.shape, dtype="int"),
            integration_time=t_int,
            channel_bandwidth=fbw,
            polarisation_frame=pol_frame,
            source=source_name,
            scan_id=scan_id,
            scan_intent=scan_intent,
            execblock_id=execblock_id,
            meta=md,
            phasecentre=zen_sc,
            configuration=config,
        )

    return v


def uvdata_to_sdp_vis(uv: UVData, scan_id: int=0, scan_intent: str="", execblock_id: str="",
                      conj: bool=False, flip_uvw=True) -> Visibility:
    """ Convert pyuvdata object to SDP Visibility

    Args:
        uv (UVData): pyuvdata UVData object

    Returns:
        v (Visibility): SDP Visibility object
    """

    # Create Configuration and Frames
    # TODO: Derive from UVData
    config    = Configuration()
    rec_frame = ReceptorFrame('linear')
    pol_frame = PolarisationFrame('linear')

    telescope_earthloc = EarthLocation.from_geocentric(*uv.telescope_location, unit='m')

    antpos_ECEF = uv.antenna_positions

    # Instantiate config
    config = config.constructor(
        name=uv.telescope_name,
        names=uv.antenna_names,
        stations=len(uv.antenna_numbers),
        location=telescope_earthloc,
        xyz=antpos_ECEF,
        receptor_frame=rec_frame,
        diameter=1.0,  # Errors if not set
        mount='altaz')

    # Setup time -- note time_array is Nbls*Nt long, we assume it is ordered
    t = Time(uv.time_array[::uv.Nbls], format='jd', location=telescope_earthloc)
    t_lst= t.sidereal_time('apparent').to('rad').value
    t0 = Time(np.round(t[0].mjd), format='mjd')

    # Setup frequency
    f_c  = uv.freq_array[0]   # TODO: handle multiple spectral windows
    f_bw = np.ones_like(f_c) * uv.channel_width

    if len(uv.freq_array) > 1:
        raise NotImplementedError("Only length-1 frequency arrays supported at present.")

    # setup phase center
    pc_id = sorted(uv.phase_center_catalog.keys())[0]
    pcd = uv.phase_center_catalog[pc_id]
    pc_name = pcd['cat_name']
    pc_sc = SkyCoord(pcd['cat_lon'], pcd['cat_lat'], unit=('rad', 'rad'))
    pc_hourangle = t.sidereal_time('apparent').to('rad').value - pc_sc.icrs.ra.to('hourangle').to('rad').value

    #integration_time (float, optional) – Only used in the specific case where times only has one element
    t_int = uv.integration_time[0] if uv.Ntimes == 1 else None

    # Phase center
    pc = SkyCoord(uv.phase_center_app_ra_degrees[0], uv.phase_center_app_dec_degrees[0],
                  unit=('degree', 'degree'))

    # Reshape UVW array -- note this assumes data are organized in same way as SDP
    uvw = uv.uvw_array.reshape((uv.Ntimes, uv.Nbls, 3))
    if flip_uvw:
        uvw *= -1

    # Reshape time array -- only need one entry per timestep
    t = Time(uv.time_array.reshape((uv.Ntimes, uv.Nbls))[:, 0], format='jd', location=telescope_earthloc)
    t_lst= t.sidereal_time('apparent').to('rad').value

    # Reshape visibility array -- should be same layout so no worries here
    vis_data = uv.data_array.reshape((uv.Ntimes, uv.Nbls, 1, 4))


    # Remap XX.YY,XY,YX -> XX,XY,YX,YY
    vis_data = vis_data[..., [0,2,3,1]]

    if conj:
        vis_data = np.conj(vis_data)

    # Generate baseline IDs
    baselines = pd.MultiIndex.from_arrays(
        (uv.ant_1_array[:uv.Nbls], uv.ant_2_array[:uv.Nbls]), names=("antenna1", "antenna2")
    )

    # Get source name (kinda hidden in phase_center_catalog)
    source_name = list(uv.phase_center_catalog.items())[0][1]['cat_name']

    # Create SDP visibility
    v = Visibility.constructor(
            uvw=uvw,
            time=t.mjd * 86400,
            frequency=f_c,
            vis=vis_data,
            weight=np.ones(vis_data.shape),
            baselines=baselines,
            flags=np.zeros(vis_data.shape, dtype="int"),
            integration_time=t_int,
            channel_bandwidth=f_bw,
            polarisation_frame=pol_frame,
            source=source_name,
            scan_id=scan_id,
            scan_intent=scan_intent,
            execblock_id=execblock_id,
            meta={},
            phasecentre=pc,
            configuration=config,
        )

    return v