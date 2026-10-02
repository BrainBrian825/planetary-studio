"""Collect Astropy without importing optional visualization packages during build.

Astropy 8's optional wcsaxes package raises a pytest Skip exception when matplotlib
is absent. The general upstream hook scans that package even for FITS-only apps.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = collect_data_files("astropy", excludes=["**/tests/**"])
datas += copy_metadata("astropy") + copy_metadata("numpy")
hiddenimports = collect_submodules(
    "astropy", filter=lambda name: ".tests" not in name and not name.startswith("astropy.visualization")
)
hiddenimports += ["numpy.lib.recfunctions"]
datas += [
    (source, target)
    for source, target in collect_data_files("astropy", include_py_files=True)
    if source.endswith(("_parsetab.py", "_lextab.py"))
]
