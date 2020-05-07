"""Pre and post processing associated with ww3 model runs."""
import testbedutils.anglesLib
from prepdata import inputOutput
from prepdata.prepDataLib import PrepDataTools
from getdatatestbed.getDataFRF import getDataTestBed
import datetime as DT
import os, glob, makenc, sys, shutil
from subprocess import check_output
import netCDF4 as nc
import numpy as np
import plotting.operationalPlots as oP
from testbedutils import sblib as sb
from testbedutils import waveLib as sbwave
from plotting.operationalPlots import obs_V_mod_TS
from testbedutils import geoprocess as gp
from testbedutils import fileHandling

def ww3simSetup(startTime, inputDict, allWind , allWL, allWave, gaugelocs=None):
    """This Function is the master call for the  data preparation for the Coastal Model
    Test Bed (CMTB) for ww3.

    Designed for unstructured grids (wave)

    NOTE: input to the function is the end of the duration.  All Files are labeled by this convention
    all time stamps otherwise are top of the data collection

    Args:
        startTime (str): this is a string of format YYYY-mm-ddTHH:MM:SSZ (or YYYY-mm-dd) in UTC time
        inputDict (dict): this is a dictionary that is read from the yaml read function
        allWind(dict): from get data with all wind from entire project set
        allWL(dict): from get data with all waterlevel from entire project set
        allWave(dict): from get data with all wave from entire project set

    """
    # begin by setting up input parameters
    simulationDuration = int(inputDict.get('simulationDuration', 24))
    plotFlag = inputDict.get('plotFlag', True)
    version_prefix = inputDict['modelSettings'].get('version_prefix', 'base').lower()
    model = inputDict['modelSettings'].get('model', 'ww3').lower()
    path_prefix = inputDict.get('path_prefix')
    rawspec = allWave
    rawwind = allWind
    rawWL = allWL
    # ______________________________________________________________________________
    # do versioning stuff here
    if model in ['ww3']:
        full = True
    # _______________________________________________________________________________
    # set times
    d1 = DT.datetime.strptime(startTime, '%Y-%m-%dT%H:%M:%SZ')
    d2 = d1 + DT.timedelta(0, simulationDuration * 3600, 0)
    dateString = d1.strftime('%Y-%m-%dT%H%M%SZ')  # used to be endtime
    
    print("Model Time Start : %s  Model Time End:  %s" % (d1, d2))
    
    # __________________Make Diretories_____________________________________________
    fileHandling.makeCMTBfileStructure(path_prefix, dateString)

    # ____________________________ begin model data gathering __________________________________________________________
    gdTB = getDataTestBed(d1, d2)        # initialize get data test bed (bathy)
    ww3io = inputOutput.ww3IO(path_prefix=path_prefix, fileNameBase=dateString)
    prepdata = PrepDataTools()

    # _____________________WAVES, wind, WL ____________________________
    assert rawspec is not None, "\n++++\nThere's No Wave data between %s and %s \n++++\n" % (d1, d2)
    # rotate and lower resolution of directional wave spectra
    _, waveTimeList, wlTimeList, _, windTimeList = prepdata.createDifferentTimeLists(d1, d2, rawspec, rawWL,
                                                                                     rawWind=rawwind)

    wavepacket = prepdata.prep_spec(rawspec, version_prefix, datestr=dateString, plot=plotFlag, full=full, deltaangle=5,
                                    outputPath=path_prefix, model=model, waveTimeList=waveTimeList)
    # use generated time lists for these to provide accurate temporal values
    windpacket = prepdata.prep_wind(rawwind, windTimeList, model=model)  # vector average, rotate winds, correct to 10m
    WLpacket = prepdata.prep_WL(rawWL, wlTimeList)                       # scalar average WL

    # ____________ BATHY   _____________________________________________
    bathy = gdTB.getBathyIntegratedTransect(method=1)
    _ = ww3io.readWW3_msh(inputDict['modelSettings']['grid'])
    gridNodes = sb.Bunch({'points':ww3io.points})              # we will remove this when meshio is working as expected
    
    if plotFlag: bathyPlotFname = os.path.join(path_prefix, dateString, 'figures', dateString+'_bathy.png');
    else: bathyPlotFname=False
    bathy = prepdata.prep_Bathy(bathy, gridNodes, unstructured=True, plotFname=bathyPlotFname)
    
    # ____________________________ set model save points _______________________________________________________________
    # _________________________ Create observation locations ___________________________________________________________
    sys.path.append('/home/spike/repos/TDSlocationGrabber')
    from frfTDSdataCrawler import query
    print('  TODO: handle TDS location grabber')
    dataLocations = query(d1, d2, inputName='/home/spike/repos/TDSlocationGrabber/database', type='waves')
    # # get gauge nodes x/y new idea: put gauges into input/output instance for the model, then we can save it
    gaugelocs = []
    for ii, gauge in enumerate(dataLocations['Sensor']):
         gaugelocs.append([dataLocations['Lon'][ii], dataLocations['Lat'][ii], gauge])
    ww3io.savePoints = gaugelocs

    # ____________________________ begin writing model Input ___________________________________________________________
    ww3io.WL = WLpacket['avgWL']

    print('_________________ writing input files _________________')
    specFname = ww3io.writeWW3_spec(wavepacket)                            # write individual spec file
    # write grid file
    ww3io.writeWW3_grid(grid_inbindFname=inputDict['modelSettings']['grid'].split('.')[0]+'.inbnd',
                        spectrumNTH=wavepacket['spec2d'].shape[2], spectrumNK=wavepacket['spec2d'].shape[1])
    # write mesh file
    # ww3io.writeWW3_mesh(gridNodes=bathy)                                 # write gmesh file using gmsh library
    ww3io.write_msh(points=bathy.points, cell_data=ww3io.cell_data)        # write gmesh using manual function
    # write name list file -- shouldn't change
    ww3io.writeWW3_namelist()                                              # will write with defaults with no input args
    specListFileName = ww3io.writeWW3_speclist(ofname='spec.list', specFiles=specFname)   # write specFile
    # write files used as spectral boundary
    ww3io.writeWW3_bouncfile(specListFilename=specListFileName)                           # write boundary files
    ww3io.writeWW3_shel(d1, d2, windpacket, WLpacket, outputInterval=1800)                # write shel file
    ww3io.writeWW3_ounf()                                                                 # process field data file
    ww3io.writeWW3_ounp()                                                                 # process point data file
    ww3io.dateString = dateString
    return ww3io

def ww3analyze(startTime, inputDict, ww3io):
    """This runs the post process script for CMS wave
    will create plots and netcdf files at request

    Args:
        inputDict (dict): this is an input dictionary that was generated with the
            keys from the project input yaml file
        startTime (str): input start time with datestring in format YYYY-mm-ddThh:mm:ssZ
        ww3io(class): this is an initialized class from model pre-processing.  This holds specific metadata including
            class objects as listed below:


    Returns:
        plots in the inputDict['workingDirectory'] location
        netCDF files to the inputDict['netCDFdir'] directory

    """
    # ___________________define Global Variables___________________________________
    plotFlag = inputDict.get('plotFlag', True)
    model = inputDict.get('model', 'ww3')
    version_prefix = ww3io.version_prefix
    path_prefix = inputDict.get(os.path.join('path_prefix', "{}".format(version_prefix)), ww3io.path_prefix)
    simulationDuration = inputDict['simulationDuration']
    Thredds_Base = inputDict.get('netCDFdir', '/home/{}/thredds_data/'.format(check_output('whoami', shell=True)[:-1]))

    # _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
    # establishing the resolution of the input datetime
    # try:
    d1 = DT.datetime.strptime(startTime, '%Y-%m-%dT%H:%M:%SZ')
    d2 = d1 + DT.timedelta(0, simulationDuration * 3600, 0)
    datestring = d1.strftime('%Y-%m-%dT%H%M%SZ')  # a string for file names
    simulationDirectory = os.path.join(path_prefix, datestring)
    # ____________________________________________________________________________
    if version_prefix == 'base':
        full = True   # define full plane
    # _____________________________________________________________________________
    print('\nBeginning of Analyze Script\nLooking for file in {}'.format(simulationDirectory))
    print('\nData Start: {}  Finish: {}'.format(d1, d2))
    print('Analyzing simulation')
    # go = getDataFRF.getObs(d1, d2, server)  # setting up get data instance
    prepdata = PrepDataTools()  # initializing instance for rotation scheme
    ######################################################################################################################
    ######################################################################################################################
    ##################################   Load Data Here / Massage Data Here   ############################################
    ######################################################################################################################
    ######################################################################################################################
    t = DT.datetime.now()
    print('Loading files ')
    savePointFname = os.path.join(ww3io.path_prefix, "ww3.{}_spec.nc".format(DT.datetime.strptime(ww3io.dateString,
                                                                                                  '%Y-%m-%dT%H%M%SZ').strftime('%Y%m')))
    ##### point file processing
    pointNc = nc.Dataset(savePointFname)
    idxSorted = np.argsort(pointNc['direction'][:])
    dWEDout = pointNc['efth'][:, :, :, idxSorted]
    # for station in pointNc['station']
    
    
    fieldNc = ww3io.readWW3_field()                                                              # load all files
    pointNc = ww3io.readWW3_point()
    bathyPacket = ww3io.readWW3_msh()
    print('Loaded files in {:.2f} seconds'.format((DT.datetime.now() - t).total_seconds()/60))

    ######################
    #inputs
    globalyaml_fname = "yaml_files/waveModels/ww3/base/point_globalmeta.yml"
    ################ function
    # global metaData to write
    pointNc

    
    ncfile = nc.Dataset(ofname, 'w')
    ncfile.createDimension('time', len(data['time']))
    ncfile.createDimension('station', 1)
    ncfile.createDimension('frequency', len(data['wavefreqbin']))
    ncfile.createDimension('direction', len(data['wavedirbin']))
    ncfile.createDimension('string16', 16)

    dataset = ncfile.createVariable('time', 'f8', ('time',))
    dataset[:] = nc.date2num(data['time'], 'days since 1990-01-01')
    setattr(dataset, 'units', 'days since 1990-01-01 00:00:00')
    setattr(dataset, 'calendar', 'gregorian')

    dataset = ncfile.createVariable('frequency', 'f8', ('frequency',))
    dataset[:] = data['wavefreqbin']
    dataset = ncfile.createVariable('direction', 'f8', ('direction',))
    dataset[:] = data['wavedirbin']
    dataset = ncfile.createVariable('efth', 'f8', ('time', 'station', 'frequency', 'direction',))
    # dataset[:] = efth2*180.0*np.pi
    dataset[:] = np.expand_dims(data['spec2d'], axis=1)

    dataset = ncfile.createVariable('station_name', 'S1', ('string16',))
    dataset[:] = nc.stringtochar(np.array(['wave            '], 'S16'))
    dataset = ncfile.createVariable('longitude', 'f4')
    dataset[:] = data['lon']
    dataset = ncfile.createVariable('latitude', 'f4')
    dataset[:] = data['lat']
    dataset = ncfile.createVariable('x', 'f4')
    dataset[:] = data['xFRF']
    dataset = ncfile.createVariable('y', 'f4')
    dataset[:] = data['yFRF']
    ncfile.close()
    pointNc = prepdata.postProcessWW3pointNc(pointNC)
    makenc.writeWW3Nc
    for station in range(0, np.size(obse_packet['spec'], axis=1)):
        # for dir_ocean in range(0, np.size(obse_packet['spec'], axis=0)):  # interp back to 62 frequencies
        #         f = interpolate.interp2d(obse_packet['wavefreqbin'], obse_packet['directions'],
        #                                  obse_packet['spec'][dir_ocean, station, :, :].T, kind='linear')
        # interp back to frequency bands that FRF data are kept in
        # interp[dir_ocean, station, :, :] = f(wavefreqbin, obse_packet['directions']).T

        # rotate the spectra back to true north
        obse_packet['ncSpec'][:, station, :, :], obse_packet['ncDirs'] = prepdata.grid2geo_spec_rotate(
            obse_packet['directions'],
            obse_packet['spec'][:, station, :, :])  # interp[:, station, :, :]) - this was with interp
        # now converting m^2/Hz/radians back to m^2/Hz/degree
        # note that units of degrees are on the denominator which requires a deg2rad conversion instead of rad2deg
        obse_packet['ncSpec'][:, station, :, :] = np.deg2rad(obse_packet['ncSpec'][:, station, :, :])
    obse_packet['modelfreqbin'] = obse_packet['wavefreqbin']
    obse_packet['wavefreqbin'] = obse_packet[
        'wavefreqbin']  # wavefreqbin  # making sure output frequency bins now match the freq that were interped to

    ######################################################################################################################
    ######################################################################################################################
    ##################################  Spatial Data HERE     ############################################################
    ######################################################################################################################
    ######################################################################################################################
    gridPack = prepdata.makeCMSgridNodes(float(cio.sim_Packet[0]), float(cio.sim_Packet[1]),
                                         float(cio.sim_Packet[2]), dep_pack['dx'], dep_pack['dy'],
                                         dep_pack['bathy'])  # dims [t, x, y]
    # ################################
    #        Make NETCDF files       #
    # ################################
    # STio = stwaveIO()
    if np.median(gridPack['elevation']) < 0:
        gridPack['elevation'] = -gridPack['elevation']

    fldrArch = os.path.join(model, version_prefix)
    spatial = {'time': nc.date2num(wave_pack['time'], units='seconds since 1970-01-01 00:00:00'),
               'station_name': 'Regional Simulation Field Data',
               'waveHs': np.transpose(wave_pack['waveHs'], (0, 2, 1)),  # put into dimensions [t, y, x]
               'waveTm': np.transpose(np.ones_like(wave_pack['waveHs']) * -999, (0, 2, 1)),
               'waveDm': np.transpose(wave_pack['waveDm'], (0, 2, 1)),  # put into dimensions [t, y, x]
               'waveTp': np.transpose(wave_pack['waveTp'], (0, 2, 1)),  # put into dimensions [t, y, x]
               'bathymetry': np.transpose(gridPack['elevation'], (0, 2, 1)),  # put into dimensions [t, y, x]
               'latitude': gridPack['latitude'],  # put into dimensions [t, y, x] - NOT WRITTEN TO FILE
               'longitude': gridPack['longitude'],  # put into dimensions [t, y, x] - NOT WRITTEN TO FILE
               'xFRF': gridPack['xFRF'],  # put into dimensions [t, y, x]
               'yFRF': gridPack['yFRF'],  # put into dimensions [t, y, x]
               ######################
               'DX': dep_pack['dx'],
               'DX': dep_pack['dy'],
               'NI': dep_pack['NI'],
               'NJ': dep_pack['NJ'],
               'grid_azimuth': gridPack['azimuth']
               }

    # TdsFldrBase = os.path.join(Thredds_Base, fldrArch)
    # NCpath = sb.makeNCdir(Thredds_Base, os.path.join(version_prefix, 'Field'), datestring, model=model)
    # # make the name of this nc file
    # NCname = 'CMTB-waveModels_{}_{}_Field_{}.nc'.format(model, version_prefix, datestring)
    fieldOfname = fileHandling.makeTDSfileStructure(Thredds_Base, fldrArch, datestring, 'Field')
    #
    # if not os.path.exists(TdsFldrBase):
    #     os.makedirs(TdsFldrBase)  # make the directory for the thredds data output
    # if not os.path.exists(os.path.join(TdsFldrBase, 'Field', 'Field.ncml')):
    #     inputOutput.makencml(os.path.join(TdsFldrBase, 'Field', 'Field.ncml'))  # remake the ncml if its not there
    # make file name strings
    flagfname = os.path.join(simulationDirectory, 'Flags{}.out.txt'.format(datestring))  # startTime # the name of flag file
    fieldYaml = 'yaml_files/waveModels/%s/Field_globalmeta.yml' % (fldrArch)  # field
    varYaml = 'yaml_files/waveModels/%s/Field_var.yml' % (fldrArch)
    assert os.path.isfile(fieldYaml), 'NetCDF yaml files are not created'  # make sure yaml file is in place
    makenc.makenc_field(data_lib=spatial, globalyaml_fname=fieldYaml, flagfname=flagfname,
                        ofname=fieldOfname, var_yaml_fname=varYaml)
    ###################################################################################################################
    ###############################   Plotting  Below   ###############################################################
    ###################################################################################################################
    dep_pack['bathy'] = np.transpose(dep_pack['bathy'], (0, 2, 1))  # dims [t, y, x]
    plotParams = [('waveHs', '$m$'), ('bathymetry', 'NAVD88 $[m]$'), ('waveTp', '$s$'), ('waveDm', '$degTn$')]
    if plotFlag == True:
        for param in plotParams:
            print('    plotting %s...' % param[0])
            spatialPlotPack = {'title': 'Regional Grid: %s' % param[0],
                               'xlabel': 'Longshore distance [m]',
                               'ylabel': 'Cross-shore distance [m]',
                               'field': spatial[param[0]],
                               'xcoord': spatial['xFRF'],
                               'ycoord': spatial['yFRF'],
                               'cblabel': '%s - %s' % (param[0], param[1]),
                               'time': nc.num2date(spatial['time'], 'seconds since 1970-01-01')}
            fnameSuffix = 'figures/CMTB_CMS_%s_%s' % (version_prefix, param[0])
            if param[0] == 'waveHs':
                oP.plotSpatialFieldData(dep_pack, spatialPlotPack, os.path.join(simulationDirectory, fnameSuffix), nested=0, directions=spatial['waveDm'])
            else:
                oP.plotSpatialFieldData(dep_pack, spatialPlotPack, os.path.join(simulationDirectory, fnameSuffix), nested=0)
            # now make a gif for each one, then delete pictures
            fList = sorted(glob.glob(simulationDirectory + '/figures/*%s*.png' % param[0]))
            sb.makegif(fList, simulationDirectory + '/figures/CMTB_%s_%s_%s.gif' % (version_prefix, param[0], datestring))
            [os.remove(ff) for ff in fList]

    ######################################################################################################################
    ######################################################################################################################
    ##################################  Wave Station Files HERE (loop) ###################################################
    ######################################################################################################################
    ######################################################################################################################

    # this is a list of file names to be made with station data from the parent simulation
    stationList = ['waverider-26m', 'waverider-17m', 'awac-11m', '8m-array', 'awac-6m', 'awac-4.5m', 'adop-3.5m',
                   'xp200m', 'xp150m', 'xp125m']
    for gg, station in enumerate(stationList):
        # stationName = 'CMTB-waveModels_CMS_%s_%s' % (version_prefix, station)  # xp 125

        # this needs to be the same order as the run script
        stat_yaml_fname = 'yaml_files/waveModels/{}/Station_var.yml'.format(fldrArch)
        globalyaml_fname = 'yaml_files/waveModels/{}/Station_globalmeta.yml'.format(fldrArch)
        # getting lat lon, easting northing idx's
        # Idx_i = len(gridPack['i']) - np.argwhere(gridPack['i'] == stat_packet['iStation'][
        #     gg]).squeeze() - 1  # to invert the coordinates from offshore 0 to onshore 0
        # Idx_j = np.argwhere(gridPack['j'] == stat_packet['jStation'][gg]).squeeze()
        if plotFlag == True:
            w = go.getWaveSpec(station)  # go get all data
        else:
            w = go.getWaveGaugeLoc(station)
        # print '   Comparison location taken from thredds, check positioning '
        stat_data = {'time': nc.date2num(stat_packet['time'][:], units='seconds since 1970-01-01 00:00:00'),
                     'waveHs': stat_packet['waveHs'][:, gg],
                     'waveTm': np.ones_like(stat_packet['waveHs'][:, gg]) * -999,
                     # this isn't output by model, but put in fills to stay consitant
                     'waveDm': stat_packet['WaveDm'][:, gg],
                     'waveTp': stat_packet['Tp'][:, gg],
                     'waterLevel': stat_packet['waterLevel'][:, gg],
                     'swellHs': stat_packet['swellHs'][:, gg],
                     'swellTp': stat_packet['swellTp'][:, gg],
                     'swellDm': stat_packet['swellDm'][:, gg],
                     'seaHs': stat_packet['seaHs'][:, gg],
                     'seaTp': stat_packet['seaTp'][:, gg],
                     'seaDm': stat_packet['seaDm'][:, gg],
                     'station_name': station,
                     'directionalWaveEnergyDensity': obse_packet['ncSpec'][:, gg, :, :],
                     'waveDirectionBins': obse_packet['ncDirs'],
                     'waveFrequency': obse_packet['wavefreqbin'],
                     ###############################
                     'DX': dep_pack['dx'],
                     'DY': dep_pack['dy'],
                     'NI': dep_pack['NI'],
                     'NJ': dep_pack['NJ'],
                     'grid_azimuth': gridPack['azimuth']}
        try:
            stat_data['Latitude'] = w['latitude']
            stat_data['Longitude'] = w['longitude']
        except KeyError:  # this should be rectified
            stat_data['Latitude'] = w['lat']
            stat_data['Longitude'] = w['lon']
        # Name files and make sure server directory has place for files to go
        print('making netCDF for model output at %s ' % station)
        outFileName = fileHandling.makeTDSfileStructure(Thredds_Base, fldrArch, datestring, 'Field')

        # TdsFldrBase = os.path.join(Thredds_Base, fldrArch, station)
        #
        # NCpath = sb.makeNCdir(Thredds_Base, os.path.join(version_prefix, station), datestring, model='CMS')
        # # make the name of this nc file
        # NCname = 'CMTB-waveModels_{}_{}_{}_{}.nc'.format(model, version_prefix, station, datestring)
        # outFileName = os.path.join(NCpath, NCname)
        #
        # if not os.path.exists(TdsFldrBase):
        #     os.makedirs(TdsFldrBase)  # make the directory for the file/ncml to go into
        # if not os.path.exists(os.path.join(TdsFldrBase, station + '.ncml')):
        #     inputOutput.makencml(os.path.join(TdsFldrBase, station + '.ncml'))
        # make netCDF
        makenc.makenc_Station(stat_data, globalyaml_fname=globalyaml_fname, flagfname=flagfname,
                              ofname=outFileName, stat_yaml_fname=stat_yaml_fname)

        print("netCDF file's created for station: %s " % station)
        ###################################################################################################################
        ###############################   Plotting  Below   ###############################################################
        ###################################################################################################################

        if plotFlag == True and 'time' in w:
            if full == False:
                w['dWED'], w['wavedirbin'] = prepdata.HPchop_spec(w['dWED'], w['wavedirbin'], angadj=70)
            obsStats = sbwave.waveStat(w['dWED'], w['wavefreqbin'], w['wavedirbin'])

            modStats = sbwave.waveStat(obse_packet['ncSpec'][:, gg, :, :], obse_packet['wavefreqbin'],
                                       obse_packet['ncDirs'])  # compute model stats here

            time, obsi, modi = sb.timeMatch(nc.date2num(w['time'], 'seconds since 1970-01-01'),
                                            np.arange(w['time'].shape[0]),
                                            nc.date2num(stat_packet['time'][:], 'seconds since 1970-01-01'),
                                            np.arange(len(stat_packet['time'])))  # time match

            for param in modStats:  # loop through each bulk statistic
                if len(time) > 1 and param in ['Hm0', 'Tm', 'sprdF', 'sprdD', 'Tp', 'Dm']:
                    print('    plotting %s: %s' % (station, param))
                    if param in ['Tp', 'Tm10']:
                        units = 's'
                        title = '%s period' % param
                    elif param in ['Hm0']:
                        units = 'm'
                        title = 'Wave Height %s ' % param
                    elif param in ['Dm', 'Dp']:
                        units = 'degrees'
                        title = 'Direction %s' % param
                    elif param in ['sprdF', 'sprdD']:
                        units = '_.'
                        title = 'Spread %s ' % param

                    # now run plots
                    p_dict = {'time': nc.num2date(time, 'seconds since 1970-01-01'),
                              'obs': obsStats[param][obsi.astype(int)],
                              'model': modStats[param][modi.astype(int)],
                              'var_name': param,
                              'units': units,  # ) -> this will be put inside a tex math environment!!!!
                              'p_title': title}

                    ofname = os.path.join(simulationDirectory, 'figures/Station_%s_%s_%s.png' % (station, param, datestring))
                    stats = obs_V_mod_TS(ofname, p_dict, logo_path='ArchiveFolder/CHL_logo.png')

                    if station == 'waverider-26m' and param == 'Hm0':
                        # this is a fail safe to abort run if the boundary conditions don't
                        # meet quality standards below
                        bias = 0.1  # bias has to be within 10 centimeters
                        RMSE = 0.1  # RMSE has to be within 10 centimeters
                        if isinstance(p_dict['obs'], np.ma.masked_array) and ~p_dict['obs'].mask.any():
                            p_dict['obs'] = np.array(p_dict['obs'])
                        # try:
                        #     # assert stats['RMSE'] < RMSE, 'RMSE test on spectral boundary energy failed'
                        #     # assert np.abs(stats['bias']) < bias, 'bias test on spectral boundary energy failed'
                        # except:
                        #     print '!!!!!!!!!!FAILED BOUNDARY!!!!!!!!'
                        #     print 'deleting data from thredds!'
                        #     os.remove(fieldOfname)
                        #     os.remove(outFileName)
                        #     raise RuntimeError('The Model Is not validating its offshore boundary condition')
