"""to_uvx: I/O for writing UVX data into HDF5 schema."""

import os

import h5py
import numpy as np
import pandas as pd
from astropy.coordinates import AltAz, Angle, EarthLocation, SkyCoord
from astropy.time import Time
from astropy.units import Quantity
from loguru import logger
from ska_ost_low_uv import __version__ as ska_ost_low_uv_version
from ska_ost_low_uv.datamodel.uvx import (
    UVX,
    create_antenna_data_array,
    create_empty_context_dict,
    create_empty_provenance_dict,
    create_visibility_array,
)
from ska_ost_low_uv.io.mccs_yaml import station_location_from_platform_yaml
from ska_ost_low_uv.utils import get_aa_config, get_software_versions, load_yaml


def load_observation_metadata(filename: str, yaml_config: str = None, load_config: str = None) -> dict:
    """Load observation metadata from correlator output HDF5.

    Args:
        filename (str): Path to HDF5 file
        yaml_config (str): Path to YAML station configuration file
        load_config (str): Name of config to load from ska_ost_low_uv package

    Notes:
        One of either `yaml_config` or `load_config` should be set. If `yaml_config`
        is set, it will take precedence over internal config
    """
    # Load metadata from config and HDF5 file
    md = get_hdf5_metadata(filename)

    if yaml_config is None:
        logger.info(f'Using internal config {load_config}')
        yaml_config = get_aa_config(load_config)

    md_yaml = load_yaml(yaml_config)
    md.update(md_yaml)

    md['history'] = f'Created with ska_ost_low_uv {ska_ost_low_uv_version}'

    # Update path to antenna location files to use absolute path
    config_abspath = os.path.dirname(os.path.abspath(yaml_config))
    md['antenna_locations_file'] = os.path.join(config_abspath, md['antenna_locations_file'])
    md['baseline_order_file'] = os.path.join(config_abspath, md['baseline_order_file'])
    md['station_config_file'] = os.path.abspath(yaml_config)
    return md


def get_hdf5_metadata(filename: str) -> dict:
    """Extract metadata from HDF5 and perform checks."""
    with h5py.File(filename, mode='r') as datafile:
        expected_keys = [
            'n_antennas',
            'ts_end',
            'n_pols',
            'n_beams',
            'tile_id',
            'n_chans',
            'n_samples',
            'type',
            'data_type',
            'data_mode',
            'ts_start',
            'n_baselines',
            'n_stokes',
            'channel_id',
            'timestamp',
            'date_time',
            'n_blocks',
        ]

        # Check that keys are present
        if set(expected_keys) - set(datafile.get('root').attrs.keys()) != set():  # pragma: no cover
            raise Exception('Missing metadata in file')

        # All good, get metadata
        metadata = {k: v for (k, v) in datafile.get('root').attrs.items()}
        metadata['n_integrations'] = metadata['n_blocks'] * metadata['n_samples']
        metadata['data_shape'] = datafile['correlation_matrix']['data'].shape

        # Overwrite ts_start with value from sample_timestamps/data
        # This is a WAR as h['root'].attrs['ts_start'] does not update
        # if a long observation spans multiple HDF5 files
        # (see https://github.com/ska-sci-ops/aa_uv/issues/48)
        metadata['ts_start'] = datafile['sample_timestamps/data'][0][0]
    return metadata


def hdf5_to_uvx(
    fn_data: str,
    telescope_name: str = None,
    yaml_config: str = None,
    from_platform_yaml: bool = False,
    context: dict = None,
    provenance: dict = None,
    load_data: bool = True,
) -> UVX:
    """Create UV from HDF5 data and config file.

    Args:
        fn_data (str): Path to HDF5 data
        yaml_config (str): Path to uv_config.yaml configuration file
        from_platform_yaml (bool=False): If true, uv_config.yaml setting 'antenna_locations'
                                         points to a mccs_platform.yaml file. Otherwise, a
                                         simple CSV text file is used.
        telescope_name (str=None): If set, ska_ost_low_uv will try and use internal config file
                                   for telescope_name, e.g. 'aavs2' or 'aavs3'
        load_data (bool): Set to false to skip loading into memory (don't apply conjugate/transpose)
        context (dict): Dictionary with observation context information
                        should include 'intent', 'notes', 'observer' and 'date' as keys.
        provenance (dict): Dictionary with additional provenance information to add to
                           the provenance dictionary (which is auto-generated)

    Returns:
        uv (UV): A UV dataclass object with xarray datasets

    Notes:
        Data in HDF5 files usually need to be conjugated and cross-pol terms transposed.

            default order: A1A2*, A1B2*, B1A2*, B1B2*
            swapped order: A2A1*, A2B1*, B2A1*, B2B1*

        The dataclass is defined as::

            class UV:
                name: str
                context: dict
                antennas: xp.Dataset - dimensions ('antenna', 'spatial')
                data: xp.DataArray   - dimensions ('time', 'frequency', 'baseline', 'polarization')
                timestamps: Time
                origin: EarthLocation
                phase_center: SkyCoord
                provenance: dict

    Metadata notes:
        The following dict items are required to generate coordinate arrays::

            n_integrations
            tsamp
            ts_start
            n_chans
            channel_spacing
            channel_id
            channel_width
    """
    md = load_observation_metadata(fn_data, yaml_config, load_config=telescope_name)
    with h5py.File(fn_data, mode='r') as h5:
        data = h5['correlation_matrix']['data']

        if from_platform_yaml:
            eloc, antpos = station_location_from_platform_yaml(md['antenna_locations_file'])

        else:
            # Telescope location
            # Also instantiate an EarthLocation observer for LST / Zenith calcs
            xyz = np.array(list(md[f'telescope_ECEF_{q}'] for q in ('X', 'Y', 'Z')))
            eloc = EarthLocation.from_geocentric(*xyz, unit='m')

            # Load baselines and antenna locations (ENU)
            antpos = pd.read_csv(md['antenna_locations_file'], delimiter=' ')

        # Generate time - note addition of ts/2 to move to center of integration
        t = Time(
            np.arange(md['n_integrations'], dtype='float64') * md['tsamp'] + md['ts_start'] + md['tsamp'] / 2,
            format='unix',
            location=eloc,
        )
        f_arr = (np.arange(md['n_chans'], dtype='float64') + 1) * md['channel_spacing'] * md['channel_id']
        f = Quantity(f_arr, unit='Hz')

        # Add station rotation angle info
        rot_ang = Angle(md['receptor_angle'], unit='deg')

        antennas = create_antenna_data_array(antpos, eloc, array_rotation_angle=rot_ang)
        if load_data:
            data = create_visibility_array(
                data,
                f,
                t,
                eloc,
                conj=md['conjugate_hdf5'],
                transpose=md['transpose_hdf5'],
            )
        else:
            # Avoid data load by not conjugating / transposing
            data = create_visibility_array(data, f, t, eloc, conj=False, transpose=False)

        data.attrs['unit'] = md['vis_units']

        # Add extra info about time resolution and frequency resolution from input metadata
        # fmt: off
        data.time.attrs['resolution']             = md['tsamp']
        data.time.attrs['resolution_unit']        = 's'

        data.frequency.attrs['resolution']        = md['channel_spacing']
        data.frequency.attrs['channel_spacing']   = md['channel_spacing']
        data.frequency.attrs['channel_bandwidth'] = md['channel_width']
        data.frequency.attrs['channel_id']        = md['channel_id']
        data.frequency.attrs['resolution_unit']   = 'Hz'
        # fmt: on

        if md['channel_width'] > md['channel_spacing']:
            data.frequency.attrs['oversampled'] = True

        # Create empty provenance dictionary if not passed, then fill with creation info
        provenance = create_empty_provenance_dict() if provenance is None else provenance
        provenance.update({
            'input_files': {
                'data_filename': os.path.abspath(fn_data),
                'config_filename': md['station_config_file'],
            },
            'ska_ost_low_uv_config': get_software_versions(),
            'input_metadata': md,
        })

        # Include observation_info attributes, which includes firmware and software versions
        if 'observation_info' in h5.keys():
            try:
                # fmt: off
                provenance['station_config'] = {
                    'observation_description': h5['observation_info'].attrs['description'],
                    'tpm_firmware_version':    h5['observation_info'].attrs['firmware_version'],
                    'daq_software_version':    h5['observation_info'].attrs['software_version'],
                    'station_config_yaml':     h5['observation_info'].attrs['station_config'],
                }
                # fmt: on

            except KeyError:
                logger.warning('Could not find expected keys in observation_info')
                obs_info_keys = h5['observation_info'].keys()
                logger.warning(f'{obs_info_keys}')

        # Compute zenith RA/DEC for phase center
        zen_aa = AltAz(
            alt=Angle(90, unit='degree'),
            az=Angle(0, unit='degree'),
            obstime=t[0],
            location=t.location,
        )
        zen_sc = SkyCoord(zen_aa).icrs

        # Create empty context dictionary if not passed
        context_dict = create_empty_context_dict() if context is None else context

        # Create UV object
        uv = UVX(
            name=md['telescope_name'],
            antennas=antennas,
            context=context_dict,
            data=data,
            timestamps=t,
            origin=eloc,
            phase_center=zen_sc,
            provenance=provenance,
        )

        return uv
