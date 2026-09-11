"""shipkit - parcel shipping quotes."""

from .labels import dimension_label
from .parcel import Parcel
from .quote import quote

__all__ = ["Parcel", "quote", "dimension_label"]
