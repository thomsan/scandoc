import numpy as np
from matplotlib import pyplot as plt
from matplotlib.patches import Polygon

from .polygon_interacter import PolygonInteractor


def interactive_get_contour(cnts, img):
    """
    :param cnts: Contours of the document
    :param img: Image of the document
    :return: New contour points of the document
    """

    poly = Polygon(cnts, animated=True, fill=False, color="yellow", linewidth=5)
    fig, ax = plt.subplots(figsize=(9, 16))  # in inches
    ax.add_patch(poly)
    ax.set_title(("Drag the corners of the box to the corners of the document. \n" "Close the window when finished."))
    p = PolygonInteractor(ax, poly)
    # close the window when ESC is pressed
    fig.canvas.mpl_connect("key_press_event", lambda event: [plt.close() if event.key == "escape" else None])
    plt.imshow(img)
    plt.show()

    new_points = p.get_poly_points()[:4]
    new_points = np.array([[p] for p in new_points], dtype="int32")
    return new_points.reshape(4, 2)
