import dbus
from dataclasses import dataclass
import shutil
import subprocess

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
    try:
        path = manager.GetUnit(unit)
    except dbus.exceptions.DBusException:
        return UnitState("not found", "not found", "not found", "not found")
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

def to_html(name, result):
    text, ok = result
    style = "color: red" if not ok else "color: green"
    return f"<li>{name}: <span style='{style}'>{text}</span></li>"

def disk_state(path):
    df = shutil.disk_usage(path)
    pct = df.used / df.total * 100
    return f"{pct:.1f}%", df.free > (10 << 30)

def slurm_state():
    result = subprocess.run(
        ["squeue", "--Format=Name", "--noheader"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return "unavailable", False
    count = len([line for line in result.stdout.splitlines() if line.startswith("nightly-")])
    if count == 0:
        return "ready", True
    return f"{count} jobs", True

def system_state_html():
    pieces = [
        to_html("Timer", timer_state()),
        to_html("Service", service_state()),
        to_html("Slurm", slurm_state()),
        to_html("<tt>/data</tt>", disk_state("/data/")),
        to_html("<tt>/home</tt>", disk_state("/home")),
    ]
    return "<ul>" + "".join(pieces) + "</ul>"
        
