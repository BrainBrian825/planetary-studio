from pathlib import Path
from importlib.metadata import distributions
import sys
import tomllib

root = Path(SPECPATH).parent
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
native = root / 'src/planetary_studio/native'
assets = root / 'src/planetary_studio/assets'
binaries = [(str(p), 'planetary_studio/native') for p in native.glob('*') if p.suffix in ('.dylib', '.so', '.dll')]
license_data = []
for distribution in distributions():
    for file in distribution.files or []:
        if any(word in file.name.lower() for word in ('license', 'copying', 'notice', 'copyright')):
            source = distribution.locate_file(file)
            if source.is_file():
                license_data.append((str(source), 'licenses/dependencies/' + distribution.metadata['Name'] + '/' + str(file.parent)))
a = Analysis([str(root / 'packaging/entry.py')], pathex=[str(root / 'src')], binaries=binaries,
    datas=[(str(assets / 'planetary-studio.png'), 'planetary_studio/assets'),
           (str(root / 'LICENSE'), 'licenses'), (str(root / 'THIRD_PARTY.md'), 'licenses'),
           (str(root / 'native/vendor/libuvc/LICENSE.txt'), 'licenses/libuvc'),
           (str(root / 'native/vendor/licenses/libusb-LGPL-2.1.txt'), 'licenses/libusb'), *license_data],
    hiddenimports=['PySide6.QtMultimedia', 'planetary_studio.cameras.uvc', 'planetary_studio.cameras.asi',
                   'planetary_studio.cameras.qhy', 'planetary_studio.cameras.indi', 'planetary_studio.cameras.alpaca'],
    hookspath=[str(root / 'packaging/hooks')], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'pytest', 'astropy.visualization'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PlanetaryStudio',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, hide_console='hide-early', disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None,
          icon=str(assets / 'planetary-studio.ico') if sys.platform == 'win32' else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PlanetaryStudio')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Planetary Studio.app', bundle_identifier='org.planetary-studio.desktop',
        icon=str(assets / 'planetary-studio.icns'),
        info_plist={'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                    'LSBackgroundOnly': False, 'LSUIElement': False,
                    'LSApplicationCategoryType': 'public.app-category.photography',
                    'NSCameraUsageDescription': 'Planetary Studio uses your camera to capture astronomy images.',
                    'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '27.0'})
