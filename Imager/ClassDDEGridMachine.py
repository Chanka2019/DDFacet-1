import numpy as np
from Gridder import _pyGridder
#import pylab
from pyrap.images import image
import MyPickle
import os
import MyLogger
import ClassTimeIt
import ModCF
import ModToolBox

import ModColor
import ClassCasaImage
import ClassApplyJones
import ToolsDir.GiveMDC
from ModToolBox import EstimateNpix
from Array import ModLinAlg
import copy

import ClassApplyJones
from ClassME import MeasurementEquation
import MyLogger
log=MyLogger.getLogger("ClassDDEGridMachine")

import ToolsDir

import ClassData
def GiveGM():
    GD=ClassData.ClassGlobalData("ParsetDDFacet.txt")
    MDC,GD=ToolsDir.GiveMDC.GiveMDC(GD=GD)
    MS=MDC.giveMS(0)
    MS.ReadData()
    GM=ClassDDEGridMachine(GD,MDC,DoDDE=False,WProj=True,lmShift=(0.,0.))
    return GM
import ClassTimeIt

def testGrid(GM):
    MDC=GM.MDC
    MS=GM.MDC.giveMS(0)

    row0,row1=0,-1

    uvw=np.float64(MS.uvw)[row0:row1]
    times=np.float64(MS.times)[row0:row1]
    data=np.complex64(MS.data)[row0:row1]
    data.fill(1.)
    A0=np.int32(MS.A0)[row0:row1]
    A1=np.int32(MS.A1)[row0:row1]

    
    flag=np.bool8(MS.flag_all)[row0:row1,:,:].copy()
    flag.fill(0)
    print uvw
    print data
    print flag

    print
    print "================"
    print

    T=ClassTimeIt.ClassTimeIt("main")
    Grid=GM.put(times,uvw,data,flag,(A0,A1),W=None,PointingID=0,DoNormWeights=True)
    T.timeit("grid")
    Grid.fill(0)
    _,_,n,n=Grid.shape
    Grid[:,:,n/2,n/2]=1
    
    vis=GM.get(times,uvw,data,flag,(A0,A1),Grid)
    T.timeit("degrid")



class ClassDDEGridMachine():
    def __init__(self,GD,MDC,
                 Npix=512,Cell=10.,Support=7,ChanFreq=np.array([6.23047e7],dtype=np.float64),
                 wmax=10000,Nw=11,DoPSF=False,
                 RaDec=None,ImageName="Image",OverS=5,
                 Padding=1.5,WProj=False,lmShift=None,Precision="S",PolMode="I",DoDDE=True):

        self.MDC,self.GD=MDC,GD
        
        self.DoDDE=DoDDE

        if self.DoDDE:
            MME=MeasurementEquation()
            HYPERCAL_DIR=os.environ["HYPERCAL_DIR"]
            execfile("%s/HyperCal/Scripts/ScriptSetMultiRIME.py"%HYPERCAL_DIR)
        
            self.MME=MME

            self.AJM=None
            if RaDec!=None:
                self.AJM=ClassApplyJones.ClassApplyJones(self.MME)
                rac,decc=RaDec
                ra=np.array([rac])
                dec=np.array([decc])
                self.AJM.setRaDec(ra,dec)

        #self.DoPSF=DoPSF
        self.DoPSF=False
        # if DoPSF:
        #     self.DoPSF=True
        #     Npix=Npix*2

        if Precision=="S":
            self.dtype=np.complex64
        elif Precision=="D":
            self.dtype=np.complex128

        self.dtype=np.complex64
        self.ImageName=ImageName

        
        self.Padding=Padding
        self.NonPaddedNpix,Npix=EstimateNpix(Npix,Padding)

        self.PolMode=PolMode
        if PolMode=="I":
            self.npol=1
            self.PolMap=np.array([0,5,5,0],np.int32)
        elif PolMode=="IQUV":
            self.npol=4
            self.PolMap=np.array([0,1,2,3],np.int32)

        self.Npix=Npix
        self.NonPaddedShape=(1,self.npol,self.NonPaddedNpix,self.NonPaddedNpix)
        self.GridShape=(1,self.npol,self.Npix,self.Npix)
        x0=(self.Npix-self.NonPaddedNpix)/2+1
        self.PaddingInnerCoord=(x0,x0+self.NonPaddedNpix)

        Grid=np.zeros(self.GridShape,dtype=self.dtype)

        #self.FFTWMachine=ModFFTW.FFTW_2Donly(Grid, ncores = 1)
        import ModFFTW
        #self.FFTWMachine=ModFFTW.FFTW_2Donly_np(Grid, ncores = 1)
        self.FFTWMachine=ModFFTW.FFTW_2Donly_np(Grid, ncores = 1)


        
        T=ClassTimeIt.ClassTimeIt("ClassImager")
        T.disable()

        self.Cell=Cell
        self.incr=(np.array([-Cell,Cell],dtype=np.float64)/3600.)*(np.pi/180)
        #CF.fill(1.)
        ChanFreq=ChanFreq.flatten()
        self.ChanFreq=ChanFreq
        self.ChanWave=2.99792458e8/self.ChanFreq
        self.UVNorm=2.*1j*np.pi/self.ChanWave
        self.UVNorm.reshape(1,self.UVNorm.size)
        self.Sup=Support
        

        if WProj:
            self.WTerm=ModCF.ClassWTermModified(Cell=Cell,Sup=Support,Npix=Npix,Freqs=ChanFreq,wmax=wmax,Nw=Nw,OverS=OverS,lmShift=lmShift)
        else:
            self.WTerm=ModCF.ClassSTerm(Cell=Cell,Sup=Support,Npix=Npix,Freqs=ChanFreq,wmax=wmax,Nw=Nw,OverS=OverS)
        T.timeit("Wterm")
        self.CF, self.fCF, self.ifzfCF= self.WTerm.CF, self.WTerm.fCF, self.WTerm.ifzfCF

        T.timeit("Rest")

        self.reinitGrid()
        self.CasaImage=None
        self.lmShift=lmShift
        self.DicoATerm=None

    def setSols(self,times,xi):
        self.Sols={"times":times,"xi":xi}


    def ShiftVis(self,uvw,vis,reverse=False):
        #if self.lmShift==None: return uvw,vis
        l0,m0=self.lmShift
        u,v,w=uvw.T
        U=u.reshape((u.size,1))
        V=v.reshape((v.size,1))
        W=w.reshape((w.size,1))
        n0=np.sqrt(1-l0**2-m0**2)-1
        if reverse: 
            corr=np.exp(-self.UVNorm*(U*l0+V*m0+W*n0))
        else:
            corr=np.exp(self.UVNorm*(U*l0+V*m0+W*n0))
        
        U+=W*self.WTerm.Cu
        V+=W*self.WTerm.Cv

        corr=corr.reshape((U.size,self.UVNorm.size,1))
        vis*=corr

        U=U.reshape((U.size,))
        V=V.reshape((V.size,))
        W=W.reshape((W.size,))
        uvw=np.array((U,V,W)).T.copy()

        return uvw,vis


    def setCasaImage(self):
        self.CasaImage=ClassCasaImage.ClassCasaimage(self.ImageName,self.NonPaddedNpix,self.Cell,self.radec1)

    def reinitGrid(self):
        #self.Grid.fill(0)
        self.NChan, self.npol, _,_=self.GridShape
        self.SumWeigths=np.zeros((self.NChan,self.npol),np.float64)

    def CalcAterm(self,times=None,A0A1=None,PointingID=0):
        if self.DoDDE==False:
            return

        nch=self.NChan
        self.norm=np.zeros((nch,2,2),float)
        Xp=self.MME.Xp

        MS=self.MDC.giveMS(PointingID)
        na=MS.na

        if times==None:
            times=self.Sols["times"]
        LTimes=sorted(list(set(times.tolist())))
        NTimes=len(LTimes)

        if A0A1==None:
            A0,A1=np.mgrid[0:na,0:na]
            A0List,A1List=[],[]
            for i in range(na):
                for j in range(i,na):
                    if i==j: continue
                    A0List.append(A0[i,j])
                    A1List.append(A1[i,j])
            A0=np.array(A0List*NTimes)
            A1=np.array(A1List*NTimes)
        else:
            A0,A1=A0A1


        self.DicoATerm={}

        for ThisTime,itime0 in zip(LTimes,range(NTimes)):
            TSols=self.Sols["times"]
            XiSols=self.Sols["xi"]
            itimeSol=np.argmin(np.abs(TSols-ThisTime))
            xi=XiSols[itimeSol]
            Xp.FromVec(xi)

            indThisTime=np.where(times==ThisTime)[0]
            ThisA0=A0[indThisTime]
            ThisA1=A1[indThisTime]
            ThisA0A1=ThisA0,ThisA1
            itimes=(itime0,itime0+1)
            self.AJM.BuildNormJones(Description="Right.noinv",itimes=itimes)

            Jones,JonesH=self.AJM.DicoNormJones[PointingID]["Right.noinv"]["M,MH"]

            JJH=ModLinAlg.BatchDot(Jones[ThisA0,:,:],JonesH[ThisA1,:,:])
            JJH_sq=np.mean(JJH*JJH.conj(),axis=0).reshape(nch,2,2)
            self.norm+=JJH_sq.real
            self.DicoATerm[ThisTime]=copy.deepcopy(self.AJM.DicoNormJones[PointingID]["Right.noinv"]["M,MH"])
        
        self.norm/=NTimes
        self.norm=np.sqrt(self.norm)
        self.norm=self.norm.reshape(nch,2,2)
        self.norm=ModLinAlg.BatchInverse(self.norm)
        self.norm=self.norm.reshape(1,nch,4)
        self.norm[np.abs(self.norm)<1e-6]=1
        self.norm.fill(1)

    def put(self,times,uvw,visIn,flag,A0A1,W=None,PointingID=0,DoNormWeights=True):#,doStack=False):
        #log=MyLogger.getLogger("ClassImager.addChunk")
        vis=visIn#.copy()

        T=ClassTimeIt.ClassTimeIt("put")
        T.disable()
        self.DoNormWeights=DoNormWeights
        if not(self.DoNormWeights):
            self.reinitGrid()

        LTimes=sorted(list(set(times.tolist())))
        NTimes=len(LTimes)
        A0,A1=A0A1

        # if self.DicoATerm==None:
        #     self.CalcAterm(times,A0A1,PointingID=PointingID)
        # if self.DoDDE:
        #     for ThisTime,itime0 in zip(LTimes,range(NTimes)):
        #         Jones,JonesH=self.DicoATerm[ThisTime]
        #         JonesInv=ModLinAlg.BatchInverse(Jones)
        #         JonesHInv=ModLinAlg.BatchInverse(JonesH)
        #         indThisTime=np.where(times==ThisTime)[0]
        #         ThisA0=A0[indThisTime]
        #         ThisA1=A1[indThisTime]
        #         P0=ModLinAlg.BatchDot(JonesInv[ThisA0,:,:],vis[indThisTime])
        #         vis[indThisTime]=ModLinAlg.BatchDot(P0,JonesHInv[ThisA1,:,:])
        #     vis/=self.norm
        
        T.timeit("1")
        # uvw,vis=self.ShiftVis(uvw,vis,reverse=True)


        #if not(doStack):
        #    self.reinitGrid()
        #self.reinitGrid()
        npol=self.npol
        NChan=self.NChan

        NVisChan=vis.shape[1]
        if W==None:
            W=np.ones((uvw.shape[0],NVisChan),dtype=np.float64)
        else:
            W=W.reshape((uvw.shape[0],1))*np.ones((1,NVisChan))

        SumWeigths=self.SumWeigths
        if vis.shape!=flag.shape:
            raise Exception('vis[%s] and flag[%s] should have the same shape'%(str(vis.shape),str(flag.shape)))
        
        u,v,w=uvw.T
        vis[u==0,:,:]=0
        flag[u==0,:,:]=True
        if self.DoPSF:
            vis.fill(0)
            vis[:,:,0]=1
            vis[:,:,3]=1

        T.timeit("2")

        Grid=np.zeros(self.GridShape,dtype=self.dtype)

        l0,m0=self.lmShift
        FacetInfos=np.array([self.WTerm.Cu,self.WTerm.Cv,l0,m0]).astype(np.float64)

        # if not(vis.dtype==np.complex64):
        #     print "vis should be of type complex128 (and has type %s)"%str(vis.dtype)
        #     stop


        #print vis.dtype
        #vis.fill(1)

        self.CheckTypes(Grid=Grid,vis=vis,uvw=uvw,flag=flag,ListWTerm=self.WTerm.Wplanes,W=W)


        T.timeit("3")
        Grid=_pyGridder.pyGridderWPol(Grid,
                                      vis,
                                      uvw,
                                      flag,
                                      W,
                                      SumWeigths,
                                      0,
                                      self.WTerm.Wplanes,
                                      self.WTerm.WplanesConj,
                                      np.array([self.WTerm.RefWave,self.WTerm.wmax,len(self.WTerm.Wplanes),self.WTerm.OverS],dtype=np.float64),
                                      self.incr.astype(np.float64),
                                      self.ChanFreq.astype(np.float64),
                                      [self.PolMap,FacetInfos],
                                      []) # Input the jones matrices

        # print SumWeigths
        # return
        
        T.timeit("4 (grid)")
        ImPadded= self.GridToIm(Grid)
        Dirty = self.cutImPadded(ImPadded)

        T.timeit("5")
        # Grid[:,:,:,:]=Grid.real
        # import pylab
        # pylab.clf()
        # pylab.imshow(np.abs(Grid[0,0]))
        # pylab.draw()
        # pylab.show(False)
        # stop

        return Dirty

    def CheckTypes(self,Grid=None,vis=None,uvw=None,flag=None,ListWTerm=None,W=None):
        if Grid!=None:
            if not(Grid.dtype==np.complex64):
                raise NameError('Grid.dtype %s %s'%(str(Grid.dtype),str(self.dtype)))
        if vis!=None:
            if not(vis.dtype==np.complex64):
                raise NameError('Grid.dtype %s'%(str(Grid.dtype)))
        if uvw!=None:
            if not(uvw.dtype==np.float64):
                raise NameError('Grid.dtype %s'%(str(Grid.dtype)))
        if flag!=None:
            if not(flag.dtype==np.bool8):
                raise NameError('Grid.dtype %s'%(str(Grid.dtype)))
        if ListWTerm!=None:
            if not(ListWTerm[0].dtype==np.complex64):
                raise NameError('Grid.dtype %s'%(str(Grid.dtype)))
        if W!=None:
            if not(W.dtype==np.float64):
                raise NameError('Grid.dtype %s'%(str(Grid.dtype)))


    def get(self,times,uvw,visIn,flag,A0A1,ModelImage,PointingID=0,Row0Row1=(0,-1)):
        #log=MyLogger.getLogger("ClassImager.addChunk")
        T=ClassTimeIt.ClassTimeIt("get")
        T.disable()

        vis=visIn#.copy()
        LTimes=sorted(list(set(times.tolist())))
        NTimes=len(LTimes)
        A0,A1=A0A1

        Grid=self.dtype(self.setModelIm(ModelImage))
        if np.max(np.abs(Grid))==0: return vis
        T.timeit("1")
        #dummy=np.abs(vis).astype(np.float32)

        npol=self.npol
        NChan=self.NChan
        SumWeigths=self.SumWeigths
        if vis.shape!=flag.shape:
            raise Exception('vis[%s] and flag[%s] should have the same shape'%(str(vis.shape),str(flag.shape)))

        
        u,v,w=uvw.T
        vis[u==0,:,:]=0
        flag[u==0,:,:]=True
      
        uvwOrig=uvw.copy()
        
        # uvw,vis=self.ShiftVis(uvw,vis,reverse=False)
        
        # vis.fill(0)
        
        l0,m0=self.lmShift
        FacetInfos=np.array([self.WTerm.Cu,self.WTerm.Cv,l0,m0]).astype(np.float64)
        Row0,Row1=Row0Row1
        if Row1==-1:
            Row1=u.shape[0]
        RowInfos=np.array([Row0,Row1]).astype(np.int32)

        T.timeit("2")
            
        self.CheckTypes(Grid=Grid,vis=vis,uvw=uvw,flag=flag,ListWTerm=self.WTerm.Wplanes)


        T.timeit("3")
        #print vis
        _ = _pyGridder.pyDeGridderWPol(Grid,
                                         vis,
                                         uvw,
                                         flag,
                                         SumWeigths,
                                         0,
                                         self.WTerm.WplanesConj,
                                         self.WTerm.Wplanes,
                                         np.array([self.WTerm.RefWave,self.WTerm.wmax,len(self.WTerm.Wplanes),self.WTerm.OverS],dtype=np.float64),
                                         self.incr.astype(np.float64),
                                         self.ChanFreq.astype(np.float64),
                                         [self.PolMap,FacetInfos,RowInfos])

        T.timeit("4 (degrid)")
        #print vis
        
        # uvw,vis=self.ShiftVis(uvwOrig,vis,reverse=False)
        
        if self.DoDDE:
            for ThisTime,itime0 in zip(LTimes,range(NTimes)):
                Jones,JonesH=self.DicoATerm[ThisTime]
                indThisTime=np.where(times==ThisTime)[0]
                ThisA0=A0[indThisTime]
                ThisA1=A1[indThisTime]
                P0=ModLinAlg.BatchDot(Jones[ThisA0,:,:],vis[indThisTime])
                vis[indThisTime]=ModLinAlg.BatchDot(P0,JonesH[ThisA1,:,:])
            #vis*=self.norm

        T.timeit("5")
        return vis


    #########################################################
    ########### ADDITIONALS
    #########################################################

    def setModelIm(self,ModelIm):
        _,_,n,n=ModelIm.shape
        x0,x1=self.PaddingInnerCoord
        # self.ModelIm[:,:,x0:x1,x0:x1]=ModelIm
        ModelImPadded=np.zeros(self.GridShape,dtype=self.dtype)
        ModelImPadded[:,:,x0:x1,x0:x1]=ModelIm
        Grid=self.ImToGrid(ModelImPadded)*n**2
        return Grid

    def cutImPadded(self,Dirty):
        x0,x1=self.PaddingInnerCoord
        Dirty=Dirty[:,:,x0:x1,x0:x1]
        # if self.CasaImage!=None:
        #     self.CasaImage.im.putdata(Dirty[0,0].real)
        return Dirty
        

    def getDirtyIm(self):
        Dirty= self.GridToIm()
        x0,x1=self.PaddingInnerCoord
        Dirty=Dirty[:,:,x0:x1,x0:x1]
        # if self.CasaImage!=None:
        #     self.CasaImage.im.putdata(Dirty[0,0].real)
        return Dirty

    def GridToIm(self,Grid):
        #log=MyLogger.getLogger("ClassImager.GridToIm")

        npol=self.npol
        import ClassTimeIt
        T=ClassTimeIt.ClassTimeIt()
        T.disable()

        GridCorr=Grid

        if self.DoNormWeights:
            GridCorr=Grid/self.SumWeigths.reshape((self.NChan,npol,1,1))

        GridCorr*=(self.WTerm.OverS)**2
        T.timeit("norm")
        Dirty=self.FFTWMachine.ifft(GridCorr)
        T.timeit("fft")
        nchan,npol,_,_=Grid.shape
        for ichan in range(nchan):
            for ipol in range(npol):
                Dirty[ichan,ipol][:,:]=Dirty[ichan,ipol][:,:].real/self.ifzfCF
        T.timeit("sphenorm")

        return Dirty

        
    def ImToGrid(self,ModelIm):
        
        npol=self.npol
        ModelImCorr=ModelIm*(self.WTerm.OverS*self.Padding)**2

        nchan,npol,_,_=ModelImCorr.shape
        for ichan in range(nchan):
            for ipol in range(npol):
                ModelImCorr[ichan,ipol][:,:]=ModelImCorr[ichan,ipol][:,:].real/self.ifzfCF


        ModelUVCorr=self.FFTWMachine.fft(ModelImCorr)

        return ModelUVCorr
        

