import win32com.client
import pythoncom

pythoncom.CoInitialize()
wmi = win32com.client.GetObject("winmgmts:")

devices = wmi.InstancesOf("Win32_PnPEntity")
print(f"Total devices found: {len(devices)}")

found = False

EARBUD_NAME_CONTAINS = "Noise"
for device in devices:
    if EARBUD_NAME_CONTAINS in device.Caption:
        print(f"Found Bluetooth device: {device.Caption}, ID: {device.DeviceID}")
        found = True
        break

if not found:
    print("No Bluetooth device found matching criteria.")