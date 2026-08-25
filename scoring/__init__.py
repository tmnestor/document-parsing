"""Score model predictions against an exported corpus.

Reads an exported corpus directory and a directory of prediction files, and
emits one row per page. It imports nothing from `generators`: the interface
between generation and scoring is the exported directory, exactly as it was when
the two lived in separate repositories.
"""
