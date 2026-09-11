"""A parcel and its billable weight."""

from dataclasses import dataclass

from . import units

# Carriers bill the larger of the actual weight and the volumetric weight:
#     volumetric grams = length x width x height (cm^3) / VOLUMETRIC_DIVISOR * 1000
VOLUMETRIC_DIVISOR = 5000


@dataclass(frozen=True)
class Parcel:
    weight: float
    weight_unit: str
    length: float
    width: float
    height: float
    dim_unit: str = "cm"

    def actual_grams(self):
        return units.to_grams(self.weight, self.weight_unit)

    def dimensions_cm(self):
        return tuple(
            units.to_cm(v, self.dim_unit) for v in (self.length, self.width, self.height)
        )

    def volumetric_grams(self):
        length, width, height = self.dimensions_cm()
        return length * width * height / VOLUMETRIC_DIVISOR * 1000

    def billable_grams(self):
        return max(self.actual_grams(), self.volumetric_grams())
