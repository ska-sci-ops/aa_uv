
import numpy as np
import pandas as pd
import h5py

from astropy.coordinates import EarthLocation, AltAz, Angle, SkyCoord
from astropy.time import Time
import pyuvdata.utils as uvutils

from ska_sdp_datamodels.visibility import Visibility, create_visibility
from ska_sdp_datamodels.configuration import Configuration
from ska_sdp_datamodels.science_data_model import ReceptorFrame, PolarisationFrame

from aavs_uv.aavs_uv import load_observation_metadata

def hdf5_to_sdp_vis(fn_raw: str, yaml_raw: str) -> Visibility:
    """ Generate a SDP Visibility object from a AAVS2 HDF5 file

    Args:
        fn_raw (str): Filename of raw HDF5 data to load.
        yaml_raw (str): YAML config data with telescope information
                        See https://github.com/ska-low/aavs_uv/tree/main/config#uv_configyaml

    Notes:
        The HDF5 files generated by AAVS2/3 are NOT the same format as that found in
        ska-sdp-datamodels HDF5 visibilty specification. 
        The AAVS DAQ receiver code in aavs-system has some info on the HDF5 format, here: 
        https://gitlab.com/ska-telescope/aavs-system/-/blob/master/python/pydaq/persisters/corr.py

    """
    # Load metadata
    md = load_observation_metadata(fn_raw, yaml_raw)
    
    # Create Configuration and Frames
    config    = Configuration()
    rec_frame = ReceptorFrame('linear')
    pol_frame = PolarisationFrame('linear')

    # Generate telescope XYZ ECEF position from YAML file, and create EarthLocation
    xyz = np.array(list(md[f'telescope_ECEF_{q}'] for q in ('X', 'Y', 'Z')))
    telescope_earthloc = EarthLocation.from_geocentric(*xyz, unit='m')
    
    # Load antenna ENU locations from file
    df_ant = pd.read_csv(md['antenna_locations_file'], delimiter=' ', skiprows=4, names=['name', 'X', 'Y', 'Z'])

    # Convert antennas into XYZ ECEF coordinates from ENU data
    antpos_ENU  = np.column_stack((df_ant['X'], df_ant['Y'], df_ant['Z']))
    antpos_ECEF = uvutils.ECEF_from_ENU(antpos_ENU, 
                                        latitude=telescope_earthloc.lat.to('rad').value, 
                                        longitude=telescope_earthloc.lon.to('rad').value,
                                        altitude=telescope_earthloc.height.to('m').value) 
    antpos_ECEF -= ((telescope_earthloc['x'].value, telescope_earthloc['y'].value, telescope_earthloc['z'].value))

    # Instantiate config
    config = config.constructor(
        name=md['telescope_name'], 
        names=df_ant['name'],
        stations=[f'{x}' for x in range(md['n_antennas'])],
        location=telescope_earthloc,
        xyz=antpos_ECEF,
        receptor_frame=rec_frame,
        diameter=1.0,  # Errors if not set
        mount='altaz')
    
    # times (ndarray) - 1D numpy array of floating point numbers representing hour angles in radians. 
    t  = Time(np.arange(md['n_integrations'], dtype='float64') * md['tsamp'] + md['ts_start'], 
              format='unix', location=telescope_earthloc)
    t_hourangle = t.sidereal_time('apparent').to('rad').value
    
    #frequency (ndarray) - 1D numpy array containing channel centre frequencies in Hz
    f_c  = (np.arange(md['n_chans'], dtype='float64') + 1) * md['channel_spacing'] * md['channel_id']
    # channel_bandwidth (ndarray) - 1D numpy array containing channel bandwidths in Hz
    f_bw = np.ones_like(f_c) * md['channel_width']
    
    # phasecentre (astropy.coordinates.SkyCoord) - Phase centre coordinates
    # Compute zenith phase center
    zen_aa = AltAz(alt=Angle(90, unit='degree'), az=Angle(0, unit='degree'), obstime=t[0], location=t.location)
    zen_sc = SkyCoord(zen_aa).icrs
    source_name = f"Zenith_at_mjd_{t[0].mjd}"
    
    #integration_time (float, optional) – Only used in the specific case where times only has one element
    t_int = md['tsamp'] if md['n_integrations'] == 1 else None

    # Instantiate SDP Visibility object
    v = create_visibility(
        config=config,
        times=t_hourangle,
        frequency=f_c,
        phasecentre=zen_sc,
        channel_bandwidth=f_bw,
        weight=1.0,
        polarisation_frame=pol_frame,
        integration_time=t_int,  # Only used in specific case of
        zerow=False,
        elevation_limit=None,
        source=source_name,
        scan_id=0,
        scan_intent=None,
        execblock_id=0,
        meta=md,
        utc_time=t[0],
        times_are_ha=True,
    ) 

    # Load data from file
    # Note: this assumes baseline order matches
    with h5py.File(fn_raw, mode='r') as h:
        d = h['correlation_matrix']['data'][:]
        d = np.transpose(d, (0, 2, 1, 3)) # (t, f, bl, p) -> (t, bl, f, p)
        v.vis.data = d

    return v