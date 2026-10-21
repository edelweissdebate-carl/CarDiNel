"""Earth-fixed 3D presentation of archived positions; no propagation or downloads."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.path import Path as PolygonPath
from PySide6.QtCore import Qt, QTimer
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

EARTH_RADIUS_KM = 6371.0  # Presentation sphere, not the SGP4 gravity model.
BACKGROUND = '#071321'
FOREGROUND = '#dce9f8'
REFERENCE = '#63d8ff'
PREDICTED = '#ffb45d'


def displayed_prediction(reference, prediction, magnification):
    """Magnify only the position difference, never the geocentric position."""
    return np.asarray(reference) + float(magnification)*(np.asarray(prediction)-np.asarray(reference))


def visible_above_globe(positions, azimuth, elevation):
    """Orthographic visibility: hide far-side points eclipsed by the sphere."""
    points = np.asarray(positions)
    azimuth, elevation = np.radians([azimuth,elevation])
    toward_viewer = np.array([np.cos(elevation)*np.cos(azimuth),np.cos(elevation)*np.sin(azimuth),np.sin(elevation)])
    depth = points @ toward_viewer
    limb_distance_squared = np.sum(points*points,axis=-1)-depth*depth
    return (np.sum(points*points,axis=-1)>=EARTH_RADIUS_KM**2) & ((depth>=0) | (limb_distance_squared>=EARTH_RADIUS_KM**2))


@lru_cache(maxsize=1)
def earth_mesh():
    """Render a small offline Natural Earth land mask on a spherical surface."""
    lon = np.linspace(-180, 180, 129)
    lat = np.linspace(-90, 90, 65)
    longitude, latitude = np.meshgrid(lon, lat)
    centres = np.column_stack(((longitude[:-1,:-1]+longitude[1:,1:]).ravel()/2,
                               (latitude[:-1,:-1]+latitude[1:,1:]).ravel()/2))
    land = np.zeros(len(centres), dtype=bool)
    data_path = Path(__file__).resolve().parents[2]/'assets'/'earth'/'ne_110m_land.geojson'
    if data_path.exists():
        data = json.loads(data_path.read_text(encoding='utf-8'))
        for feature in data['features']:
            geometry = feature['geometry']
            polygons = [geometry['coordinates']] if geometry['type']=='Polygon' else geometry['coordinates']
            for rings in polygons:
                inside = PolygonPath(rings[0]).contains_points(centres)
                for hole in rings[1:]:
                    inside &= ~PolygonPath(hole).contains_points(centres)
                land |= inside
    colors = np.empty((len(lat)-1,len(lon)-1,4))
    colors[:] = (0.055,0.25,0.43,1.0)
    colors[land.reshape(colors.shape[:2])] = (0.19,0.45,0.35,1.0)
    longitude, latitude = np.radians(longitude), np.radians(latitude)
    return (EARTH_RADIUS_KM*np.cos(latitude)*np.cos(longitude),
            EARTH_RADIUS_KM*np.cos(latitude)*np.sin(longitude),
            EARTH_RADIUS_KM*np.sin(latitude), colors)


class EarthErrorView(QWidget):
    """Two synchronized views: global context and an actual-scale local offset.

    The selected object has two position estimates, not two physical satellites.
    All absolute positions use the saved geocentric ITRS coordinates, so Earth
    stays fixed. No unrelated cases are overlaid at incompatible timestamps.
    """
    def __init__(self, parent=None, presentation=False):
        super().__init__(parent)
        self.series = None
        self.case = None
        self.predicted = self.reference = None
        self.frame_index = 0
        self.displayed_position = None
        self.local_offset = None
        self.extent_key = None
        self.timer = QTimer(self); self.timer.setInterval(120); self.timer.timeout.connect(self.advance)
        layout = QVBoxLayout(self); layout.setContentsMargins(8,8,8,8); layout.setSpacing(7)
        options = QHBoxLayout()
        self.play = QPushButton('Play'); self.play.clicked.connect(self.toggle_play); options.addWidget(self.play)
        self.restart = QPushButton('Restart'); self.restart.clicked.connect(lambda:self.slider.setValue(0)); options.addWidget(self.restart)
        options.addWidget(QLabel('Speed'))
        self.speed = QComboBox(); self.speed.setMinimumWidth(85)
        for title,step in [('Slow',1),('Medium',5),('Fast',15)]:self.speed.addItem(title,step)
        options.addWidget(self.speed)
        options.addWidget(QLabel('Globe error scale'))
        self.scale = QComboBox(); self.scale.setMinimumWidth(115)
        for title,value in [('True scale ×1',1),('Magnified ×100',100),('Magnified ×500',500),('Magnified ×1000',1000)]:self.scale.addItem(title,value)
        self.scale.setCurrentIndex(2); self.scale.currentIndexChanged.connect(self.update_frame); options.addWidget(self.scale)
        self.reset_camera = QPushButton('Reset view'); self.reset_camera.clicked.connect(self.reset_view); options.addWidget(self.reset_camera); options.addStretch()
        layout.addLayout(options)
        self.scale_note = QLabel('Globe separation ×500 for visibility. Local offset and reported error stay at actual scale.')
        self.scale_note.setWordWrap(True); layout.addWidget(self.scale_note)
        self.figure = Figure(figsize=(10,4), facecolor=BACKGROUND)
        self.figure.subplots_adjust(left=.01,right=.99,bottom=.03,top=.93,wspace=.03)
        self.axes = self.figure.add_subplot(121,projection='3d',computed_zorder=False)
        self.local_axes = self.figure.add_subplot(122,projection='3d',computed_zorder=False)
        self.canvas = FigureCanvasQTAgg(self.figure); self.canvas.setMinimumHeight(200)
        layout.addWidget(NavigationToolbar2QT(self.canvas,self)); layout.addWidget(self.canvas,1)
        self.readout = QLabel('Select a saved case.'); self.readout.setWordWrap(True); layout.addWidget(self.readout)
        timeline = QHBoxLayout(); timeline.addWidget(QLabel('Time'))
        self.slider = QSlider(Qt.Horizontal); self.slider.valueChanged.connect(self.update_frame); timeline.addWidget(self.slider,1)
        self.jump = QPushButton('First >1 km'); self.jump.clicked.connect(self.jump_to_crossing); timeline.addWidget(self.jump); layout.addLayout(timeline)
        self.expand = QPushButton('Exit full screen' if presentation else 'Expand 3D view')
        self.expand.clicked.connect(self.window().close if presentation else self.open_presentation)
        timeline.addWidget(self.expand)
        explanation = QLabel('Cyan: precise reference  •  Amber: SGP4 prediction  •  Two estimates of the same satellite\nEarth-fixed ITRS; drag to rotate. Trails show up to 90 saved minutes. Satellite symbols are enlarged.')
        explanation.setWordWrap(True); layout.addWidget(explanation)
        self.create_scene()
        self.canvas.mpl_connect('button_release_event',self.update_frame)
        self.set_series(None,None)

    def create_scene(self):
        self.timer.stop()
        ax = self.axes; local = self.local_axes
        for view in (ax,local):
            view.clear(); view.set_facecolor(BACKGROUND); view.tick_params(colors=FOREGROUND,labelsize=7)
            for axis in (view.xaxis,view.yaxis,view.zaxis):
                axis.label.set_color(FOREGROUND)
                axis.pane.set_facecolor((.03,.08,.14,1)); axis.pane.set_edgecolor((.2,.3,.4,.4))
                axis._axinfo['grid']['color'] = (.25,.4,.55,.3)
            view.set_box_aspect((1,1,1))
            view.set_proj_type('ortho')
        x,y,z,colors = earth_mesh()
        ax.plot_surface(x,y,z,facecolors=colors,rstride=1,cstride=1,linewidth=0,antialiased=False,shade=True,zorder=1)
        # A fixed graticule helps identify the terrestrial reference frame.
        angles = np.linspace(-np.pi,np.pi,181)
        for latitude in (-60,-30,0,30,60):
            lat = np.radians(latitude); r = EARTH_RADIUS_KM*1.002
            ax.plot(r*np.cos(lat)*np.cos(angles),r*np.cos(lat)*np.sin(angles),np.full(len(angles),r*np.sin(lat)),color='#4e91b0',alpha=.3,linewidth=.4,zorder=2)
        ax.set_axis_off()
        ax.set_box_aspect((1,1,1),zoom=1.6)
        ax.set_title('Earth context · magnified separation',color=FOREGROUND,fontsize=10,pad=2)
        local.set_title('Actual error offset · kilometres',color=FOREGROUND,fontsize=10,pad=2)
        local.set_xlabel('ITRS ΔX (km)',fontsize=8,labelpad=-2); local.set_ylabel('ITRS ΔY (km)',fontsize=8,labelpad=-2); local.set_zlabel('ITRS ΔZ (km)',fontsize=8,labelpad=-2)
        self.ref_trail, = ax.plot([],[],[],color=REFERENCE,alpha=.8,linewidth=1.0,zorder=3)
        self.pred_trail, = ax.plot([],[],[],color=PREDICTED,alpha=.8,linewidth=1.0,zorder=4)
        self.ref_marker, = ax.plot([],[],[],marker='D',markersize=7,color=REFERENCE,linestyle='',zorder=7)
        self.pred_marker, = ax.plot([],[],[],marker='s',markersize=7,color=PREDICTED,linestyle='',zorder=8)
        # Short crossbars read as solar panels while remaining lightweight.
        self.ref_panels, = ax.plot([],[],[],color=REFERENCE,linewidth=4,zorder=7)
        self.pred_panels, = ax.plot([],[],[],color=PREDICTED,linewidth=4,zorder=8)
        self.connector, = ax.plot([],[],[],color='#f1f5ff',linewidth=1,linestyle=':',zorder=6)
        local.plot([0],[0],[0],marker='D',color=REFERENCE,markersize=6)
        self.local_marker, = local.plot([],[],[],marker='s',markersize=7,color=PREDICTED,linestyle='')
        self.local_vector, = local.plot([],[],[],color=PREDICTED,linewidth=2)
        self.reset_view()

    def reset_view(self):
        if self.reference is not None:
            point = self.reference[self.slider.value()]
            self.axes.view_init(elev=float(np.degrees(np.arctan2(point[2],np.hypot(*point[:2])))),azim=float(np.degrees(np.arctan2(point[1],point[0]))))
        else:
            self.axes.view_init(elev=22,azim=35)
        self.local_axes.view_init(elev=23,azim=-55)
        self.update_frame()

    def set_series(self, case, series):
        self.timer.stop(); self.play.setText('Play')
        self.case = case; self.series = series
        enabled = series is not None
        for widget in (self.play,self.restart,self.slider,self.jump,self.scale,self.speed):widget.setEnabled(enabled)
        self.slider.blockSignals(True)
        self.slider.setRange(0,len(series.hours)-1 if enabled else 0); self.slider.setValue(0)
        self.slider.blockSignals(False)
        if enabled:
            self.predicted = np.asarray(series.predicted_itrs_km,dtype=float)
            self.reference = np.asarray(series.reference_itrs_km,dtype=float)
            self.jump.setEnabled(any(e>1 for e in series.errors))
            self.reset_view()
        else:
            self.predicted = self.reference = None
            self.readout.setText('Select a saved case.'); self.displayed_position = self.local_offset = None
            for artist in (self.ref_trail,self.pred_trail,self.ref_marker,self.pred_marker,self.ref_panels,self.pred_panels,self.connector,self.local_marker,self.local_vector):artist.set_data_3d([],[],[])
        self.update_frame()

    def update_frame(self,*_):
        if self.series is None:
            self.canvas.draw_idle(); return
        index = self.slider.value(); self.frame_index = index
        factor = self.scale.currentData()
        ref = self.reference[index]; pred = self.predicted[index]
        displayed = displayed_prediction(ref,pred,factor)
        delta = pred-ref
        self.displayed_position = displayed.copy(); self.local_offset = delta.copy()
        first = int(np.searchsorted(self.series.hours,max(0,self.series.hours[index]-1.5)))
        trail_ref = self.reference[first:index+1]
        trail_pred = displayed_prediction(trail_ref,self.predicted[first:index+1],factor)
        trail_ref = np.where(visible_above_globe(trail_ref,self.axes.azim,self.axes.elev)[:,None],trail_ref,np.nan)
        trail_pred = np.where(visible_above_globe(trail_pred,self.axes.azim,self.axes.elev)[:,None],trail_pred,np.nan)
        self.ref_trail.set_data_3d(*trail_ref.T); self.pred_trail.set_data_3d(*trail_pred.T)
        self.ref_marker.set_data_3d(*ref.reshape(3,1)); self.pred_marker.set_data_3d(*displayed.reshape(3,1))
        ref_visible = bool(visible_above_globe(ref,self.axes.azim,self.axes.elev))
        pred_visible = bool(visible_above_globe(displayed,self.axes.azim,self.axes.elev))
        self.ref_marker.set_visible(ref_visible); self.ref_panels.set_visible(ref_visible)
        self.pred_marker.set_visible(pred_visible); self.pred_panels.set_visible(pred_visible)
        for artist,position in ((self.ref_panels,ref),(self.pred_panels,displayed)):
            # The icon is schematic; its orientation is not an attitude estimate.
            tangent = np.cross(position,[0,0,1]); length = np.linalg.norm(tangent)
            tangent = tangent/length if length>1e-12 else np.array([1,0,0])
            artist.set_data_3d(*(position[None,:]+np.array([-1,1])[:,None]*tangent[None,:]*125).T)
        self.connector.set_data_3d(*np.stack((ref,displayed)).T)
        self.connector.set_visible(ref_visible and pred_visible)
        self.local_marker.set_data_3d(*delta.reshape(3,1)); self.local_vector.set_data_3d(*np.stack((np.zeros(3),delta)).T)
        # A stable per-case globe extent avoids apparent motion from auto zoom.
        limit = max(EARTH_RADIUS_KM*1.25,float(np.max(np.linalg.norm(self.reference,axis=1)))+factor*max(self.series.errors)+400)
        local_limit = max(.1,max(self.series.errors)*1.2)
        extent_key = (id(self.series),factor)
        if self.extent_key!=extent_key:
            for setter in (self.axes.set_xlim,self.axes.set_ylim,self.axes.set_zlim):setter(-limit,limit)
            for setter in (self.local_axes.set_xlim,self.local_axes.set_ylim,self.local_axes.set_zlim):setter(-local_limit,local_limit)
            self.extent_key = extent_key
        self.axes.set_title(f'{self.case.summary["satellite_name"]} · Earth context',color=FOREGROUND,fontsize=10,pad=2)
        self.scale_note.setText(f'Globe separation ×{factor:g}{" (true scale)" if factor==1 else " for visibility"}. Local offset and reported error stay at actual scale.')
        self.readout.setText(f'{self.series.times_utc[index]}   |   Elapsed {self.series.hours[index]:.3f} h   |   Actual 3D error {self.series.errors[index]:.6f} km ({1000*self.series.errors[index]:.1f} m)')
        self.canvas.draw_idle()

    def toggle_play(self):
        if self.series is None:return
        if self.timer.isActive():
            self.timer.stop();self.play.setText('Play')
        else:
            if self.slider.value()==self.slider.maximum():self.slider.setValue(0)
            self.timer.start();self.play.setText('Pause')

    def advance(self):
        if self.series is None:return
        following = min(self.slider.maximum(),self.slider.value()+self.speed.currentData())
        self.slider.setValue(following)
        if following==self.slider.maximum():
            self.timer.stop();self.play.setText('Play')

    def jump_to_crossing(self):
        if self.series is None:return
        self.timer.stop();self.play.setText('Play')
        self.slider.setValue(next((i for i,e in enumerate(self.series.errors) if e>1),0))

    def pause(self):
        # Qt can deliver hideEvent while a parent is destroying its children.
        # Never call methods on C++ controls already deleted during shutdown.
        if isValid(self.timer):self.timer.stop()
        if isValid(self.play):self.play.setText('Play')

    def open_presentation(self):
        if self.series is None:return
        self.pause()
        dialog = QDialog(self); dialog.setWindowTitle('CarDiNel · 3D Earth presentation')
        layout = QVBoxLayout(dialog); layout.setContentsMargins(0,0,0,0)
        view = EarthErrorView(dialog,presentation=True); layout.addWidget(view)
        view.scale.setCurrentIndex(self.scale.currentIndex()); view.set_series(self.case,self.series)
        view.slider.setValue(self.slider.value()); view.reset_view()
        dialog.finished.connect(lambda *_:view.pause())
        dialog.showFullScreen(); dialog.exec(); view.pause(); dialog.deleteLater()

    def hideEvent(self,event):
        self.pause();super().hideEvent(event)
