"""Text printed on the shipping label. Labels always show metric dimensions."""

from . import units


def dimension_label(length, width, height, unit):
    cm = [units.to_cm(v, unit) for v in (length, width, height)]
    return " x ".join(f"{v:.1f}" for v in cm) + " cm"
