"""Job names.

A job is written with a display name ("Build docs") and identified by its
normalized key: whitespace collapsed and case folded ("build docs"). Keys are
used for lookups and dependencies; display names are what users see in logs,
reports and files (docs/workflows.md).
"""


def normalize(name):
    return " ".join(str(name).split()).casefold()
