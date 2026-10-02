import numpy as np
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QPainterPath, QPalette
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QWidget
from .imaging import preview_pixels


class ImageView(QGraphicsView):
    def __init__(self, message="Your image will appear here"):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.item = self.scene.addPixmap(QPixmap())
        self.setBackgroundBrush(self.palette().color(QPalette.Base))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.message = self.scene.addText(message)
        self.message.setDefaultTextColor(self.palette().color(QPalette.PlaceholderText))
        self.fit = True
        self.image = None
        self.setMinimumSize(320, 240)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange) and hasattr(self, "message"):
            self.setBackgroundBrush(self.palette().color(QPalette.Base))
            self.message.setDefaultTextColor(self.palette().color(QPalette.PlaceholderText))
            self.viewport().update()

    def set_image(self, image, stretch=False):
        self.image = image
        pixels = preview_pixels(image, stretch)
        h, w = pixels.shape[:2]
        qimage = QImage(
            pixels.data,
            w,
            h,
            pixels.strides[0],
            QImage.Format_RGB888 if pixels.ndim == 3 else QImage.Format_Grayscale8,
        ).copy()
        old_size = self.item.pixmap().size()
        self.item.setPixmap(QPixmap.fromImage(qimage))
        self.message.hide()
        self.scene.setSceneRect(self.item.boundingRect())
        if self.fit or old_size != self.item.pixmap().size():
            self.fit_image()

    def fit_image(self):
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, Qt.KeepAspectRatio)
        self.fit = True

    def wheelEvent(self, event):
        if self.item.pixmap().isNull():
            return
        self.fit = False
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        amount = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        current = self.transform().m11()
        if 0.02 < current * amount < 32:
            self.scale(amount, amount)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit:
            self.fit_image()

    def mouseDoubleClickEvent(self, event):
        self.fit_image()


class Histogram(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(94)
        self.curves = []
        self.clipped = 0.0

    def set_image(self, image):
        data = image[:: max(1, image.shape[0] // 256), :: max(1, image.shape[1] // 256)]
        channels = [data] if data.ndim == 2 else [data[..., i] for i in range(3)]
        self.curves = [np.log1p(np.histogram(c, 128, (0, 1))[0]) for c in channels]
        self.clipped = float(np.mean(data >= 0.999)) * 100
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().color(QPalette.Base))
        p.setRenderHint(QPainter.Antialiasing)
        maximum = max((c.max() for c in self.curves), default=1)
        for i, curve in enumerate(self.curves):
            p.setPen(
                QPen(
                    QColor(["#d65e5e", "#5c9c70", "#5e8fd6"][i])
                    if len(self.curves) > 1
                    else self.palette().color(QPalette.Text),
                    1.4,
                )
            )
            path = QPainterPath()
            for j, value in enumerate(curve):
                x, y = (
                    6 + j * (self.width() - 12) / 127,
                    self.height() - 22 - value / max(maximum, 1) * (self.height() - 30),
                )
                path.moveTo(x, y) if j == 0 else path.lineTo(x, y)
            p.drawPath(path)
        p.setPen(self.palette().color(QPalette.PlaceholderText))
        p.drawText(8, self.height() - 5, f"Histogram · highlight clipping {self.clipped:.2f}%")


class QualityPlot(QWidget):
    def __init__(self):
        super().__init__()
        self.scores = np.array([])
        self.setMinimumHeight(120)
        self.setMaximumHeight(160)

    def set_scores(self, scores):
        self.scores = np.sort(scores)[::-1]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().color(QPalette.Base))
        p.setPen(self.palette().color(QPalette.PlaceholderText))
        p.drawText(10, 20, "Frame quality · best → worst")
        if not len(self.scores):
            return
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        maximum = max(float(self.scores.max()), 1e-9)
        sampled = self.scores[:: max(1, len(self.scores) // 500)]
        for i, score in enumerate(sampled):
            x, y = (
                10 + i * (self.width() - 20) / max(1, len(sampled) - 1),
                self.height() - 10 - score / maximum * (self.height() - 40),
            )
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(self.palette().color(QPalette.Text), 2))
        p.drawPath(path)
