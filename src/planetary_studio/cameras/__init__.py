from .base import CameraError, Device, Frame, Mode

__all__ = ["CameraError", "Device", "Frame", "Mode", "discover", "open_camera"]


def discover(settings=None, include_system=True):
    from . import simulator, uvc, system, asi, qhy, alpaca, indi

    settings = settings or {}
    devices, messages = simulator.discover(), []
    scanners = [("Direct UVC", uvc.discover)]
    if include_system:
        scanners.append(("System cameras", system.discover))
    for name, module in [("ASI", asi), ("QHY", qhy)]:
        if settings.get(name + "_sdk"):
            scanners.append((name, lambda m=module, n=name: m.discover(settings[n + "_sdk"])))
    if settings.get("alpaca"):
        scanners.append(("Alpaca", lambda: alpaca.discover(settings["alpaca"])))
    if settings.get("indi"):
        scanners.append(("INDI", lambda: indi.discover(settings["indi"])))
    for name, scanner in scanners:
        try:
            found = scanner()
            devices.extend(found)
            messages.append(f"{name}: {len(found)} camera(s) found.")
        except Exception as e:
            messages.append(f"{name}: {e}")
    return devices, messages


def open_camera(device):
    from .uvc import UvcCamera
    from .system import SystemCamera
    from .simulator import SimulatedCamera
    from .asi import AsiCamera
    from .qhy import QhyCamera
    from .alpaca import AlpacaCamera
    from .indi import IndiCamera

    return {
        "UVC": UvcCamera,
        "System": SystemCamera,
        "Simulator": SimulatedCamera,
        "ASI": AsiCamera,
        "QHY": QhyCamera,
        "Alpaca": AlpacaCamera,
        "INDI": IndiCamera,
    }[device.backend](device)
