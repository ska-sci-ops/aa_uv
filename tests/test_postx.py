import numpy as np
import pylab as plt
from aavs_uv.postx.ant_array import RadioArray
from aavs_uv.sdp_uv import hdf5_to_sdp_vis
from aavs_uv.postx.allsky_viewer import AllSkyViewer
from aavs_uv.postx.sky_model import generate_skycat

def setup_test():
    fn_data = '../../aavs3/2023.10.12-Ravi/correlation_burst_100_20231012_13426_0.hdf5'
    fn_yaml = '../config/aavs3/uv_config.yaml'
    v = hdf5_to_sdp_vis(fn_data, fn_yaml)

    # RadioArray basics
    aa = RadioArray(v)
    return aa

def test_postx():
    aa = setup_test()
    print(aa.get_zenith())
    print(aa.zenith)
    
    # RadioArray - various aa.update() calls
    aa.update()
    aa.update(t_idx=0)
    aa.update(f_idx=0)
    aa.update(update_gsm=True)
    
    # RadioArray - images
    img  = aa.make_image()
    hmap = aa.make_healpix(n_side=64)
    gsm  = aa.generate_gsm()

    # Sky catalog
    skycat = generate_skycat(aa)

    # AllSkyViewer
    asv = AllSkyViewer(aa, skycat=skycat)
    asv.load_skycat(skycat)
    asv.update()
    print(asv.get_pixel(aa.zenith))

def test_postx_plotting():
    aa = setup_test()

    # RadioArray - plotting
    aa.plot_corr_matrix()
    plt.show()
    aa.plot_antennas(overlay_names=True)
    plt.show()
    aa.plot_antennas('E', 'U')
    plt.show()

    aa.plot_uvw('U', 'V')
    plt.show()

    aa.plot_uvw('V', 'W', alpha=0.1, c='#333333')
    plt.show()

    # Sky catalog
    skycat = generate_skycat(aa)

    # AllSkyViewer
    asv = AllSkyViewer(aa, skycat=skycat)

    asv.new_fig()
    asv.plot(overlay_srcs=True)
    plt.show()

    asv.mollview(apply_mask=True, fov=np.pi/2)
    plt.show()

if __name__ == "__main__":
    test_postx()
    test_postx_plotting()