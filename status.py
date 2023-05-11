import dbus
from dataclasses import dataclass
import shutil

bus = dbus.SystemBus()
systemd = bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
manager = dbus.Interface(systemd, "org.freedesktop.systemd1.Manager")

@dataclass
class UnitState:
    load : str
    active : str
    sub : str
    file : str

UNIT = "org.freedesktop.systemd1.Unit"

def get_state(unit):
    path = manager.GetUnit(unit)
    obj = bus.get_object("org.freedesktop.systemd1", path)
    iface = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
    load = str(iface.Get(UNIT, "LoadState"))
    active = str(iface.Get(UNIT, "ActiveState"))
    sub = str(iface.Get(UNIT, "SubState"))
    file = str(iface.Get(UNIT, "UnitFileState"))
    return UnitState(load, active, sub, file)

def timer_state():
    state = get_state("nightlies.timer")
    if state.load != "loaded":
        return state.load, False
    if state.file not in ["enabled", "static"]:
        return state.file, False
    if state.active != "active":
        return state.active, False
    return state.sub, True

def service_state():
    state = get_state("nightlies.service")
    if state.load != "loaded":
        return state.load, False
    if state.file not in "enabled enabled-runtime linked linked-runtime static":
        return state.file, False
    if state.active not in "active inactive activating deactivating":
        return state.active, False
    return state.active, True

def server_state():
    state = get_state("nightly-server.service")
    if state.load != "loaded":
        return state.load, False
    if state.file not in "enabled enabled-runtime static":
        return state.file, False
    if state.active != "active":
        return state.active, False
    return state.sub, True

def to_html(name, result):
    text, ok = result
    style = "color: red" if not ok else "color: green"
    return f"<li>{name}: <span style='{style}'>\N{BLACK LARGE CIRCLE} {text}</span></li>"

def disk_state(path):
    df = shutil.disk_usage(path)
    pct = usage.free / usage.total * 100
    return f"{pct:.1f}%", usage.free > (10 << 30)

def system_state_html():
    pieces = [
        to_html("Timer", timer_state()),
        to_html("Service", service_state()),
        to_html("Server", server_state()),
        to_html("/data/", disk_state("/data/")),
        to_html("/home/", disk_state("/hone")),
    ]
    return "<ul>" + "".join(pieces) + "</ul>"
        
