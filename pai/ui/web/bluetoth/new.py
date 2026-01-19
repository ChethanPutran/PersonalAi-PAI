#!/usr/bin/env python3
"""
Bluetooth Earbud Touch Event Detector
------------------------------------
This script works with already-connected Bluetooth earbuds to detect touch events.
It uses Windows-specific Bluetooth libraries to interact with connected devices.

Install required dependencies:
pip install pywinusb
"""

import sys
import logging
import time
import platform
import ctypes
from ctypes import wintypes
import win32com.client
import pythoncom

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Set your earbud's name or part of the name here
EARBUD_NAME_CONTAINS = "Noise"  # Change this to match your device name

# Constants for Windows API
WM_DEVICECHANGE = 0x0219
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DEV_BROADCAST_HDR_SIZE = ctypes.sizeof(wintypes.DWORD) * 2 + ctypes.sizeof(wintypes.HANDLE)

class DEV_BROADCAST_HDR(ctypes.Structure):
    _fields_ = [
        ('dbch_size', wintypes.DWORD),
        ('dbch_devicetype', wintypes.DWORD),
        ('dbch_reserved', wintypes.DWORD)
    ]

class EarbudTouchDetector:
    def __init__(self, device_name_contains=EARBUD_NAME_CONTAINS):
        self.device_name_contains = device_name_contains
        self.device = None
        self.device_path = None
        self.running = False
        
    def find_connected_device(self):
        """Find the already-connected earbud device."""
        logger.info(f"Looking for connected device containing '{self.device_name_contains}' in the name...")
        
        try:
            pythoncom.CoInitialize()
            wmi = win32com.client.GetObject("winmgmts:")
            bluetooth_devices = wmi.InstancesOf("Win32_PnPEntity")
            
            for device in bluetooth_devices:
                if hasattr(device, 'Caption') and self.device_name_contains in device.Caption:
                    self.device = {
                        'name': device.Caption,
                        'id': device.DeviceID
                    }
                    logger.info(f"Found connected device: {device.Caption}")
                    
                    # Try to get device path which might be used for direct communication
                    if hasattr(device, 'PNPDeviceID'):
                        self.device_path = device.PNPDeviceID
                        logger.info(f"Device path: {self.device_path}")
                    
                    return True
                    
            logger.error("No connected earbud device found.")
            return False
        except Exception as e:
            logger.error(f"Error finding device on Windows: {e}")
            return False

    def setup_touch_monitoring(self):
        """Set up monitoring for touch events using a Raw Input approach."""
        logger.info("Setting up touch event monitoring...")
        
        try:
            # Try using PyWinUSB if available
            try:
                import pywinusb.hid as hid
                return self._setup_pywinusb_monitoring()
            except ImportError:
                logger.warning("PyWinUSB not available. Using Windows API directly.")
                return self._setup_windows_api_monitoring()
        except Exception as e:
            logger.error(f"Error setting up monitoring: {e}")
            return False
    
    def _setup_pywinusb_monitoring(self):
        """Set up monitoring using PyWinUSB."""
        import pywinusb.hid as hid
        
        logger.info("Looking for HID devices that might be your earbud...")
        all_hid_devices = hid.find_all_hid_devices()
        
        target_devices = []
        for device in all_hid_devices:
            print(device)
            if (device.product_name and self.device_name_contains in device.product_name) or \
               (device.vendor_name and self.device_name_contains in device.vendor_name):
                target_devices.append(device)
                logger.info(f"Found potential device: {device.product_name} from {device.vendor_name}")
        
        if not target_devices:
            logger.warning("No HID devices found matching your earbud name.")
            logger.info("Available HID devices:")
            for device in all_hid_devices:
                logger.info(f"- {device.product_name} from {device.vendor_name}")
            return False
        
        # Try to open each potential device
        for device in target_devices:
            try:
                device.open()
                
                # Set up the input report handler
                def input_handler(data):
                    logger.info(f"Received data from device: {data}")
                    self._process_input_data(data)
                
                # Register the handler for all reports
                device.set_raw_data_handler(input_handler)
                
                logger.info(f"Successfully monitoring HID device: {device.product_name}")
                return True
            except Exception as e:
                logger.error(f"Error opening device {device.product_name}: {e}")
        
        return False
    
    def _setup_windows_api_monitoring(self):
        """Set up monitoring using Windows API directly."""
        import win32gui
        import win32con
        
        # Define a window class
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._window_proc
        wc.lpszClassName = "EarbudDetectorWindow"
        
        # Register the class
        win32gui.RegisterClass(wc)
        
        # Create a window
        self.hwnd = win32gui.CreateWindowEx(
            0, "EarbudDetectorWindow", "Earbud Detector",
            0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )
        
        logger.info(f"Created window with handle: {self.hwnd}")
        
        # Register for device notifications
        from ctypes import byref, sizeof
        
        class DEV_BROADCAST_DEVICEINTERFACE(ctypes.Structure):
            _fields_ = [
                ('dbcc_size', wintypes.DWORD),
                ('dbcc_devicetype', wintypes.DWORD),
                ('dbcc_reserved', wintypes.DWORD),
                ('dbcc_classguid', ctypes.c_byte * 16),
                ('dbcc_name', ctypes.c_wchar * 1)
            ]
        
        # Use GUID_DEVINTERFACE_HID for HID devices
        HID_GUID = bytearray([
            0x4d, 0x1e, 0x55, 0xb1, 0x6d, 0x98, 0xd1, 0x11,
            0x8c, 0x9f, 0x00, 0x80, 0xc7, 0x2e, 0x4c, 0x44
        ])
        
        device_interface = DEV_BROADCAST_DEVICEINTERFACE()
        device_interface.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE)
        device_interface.dbcc_devicetype = 5  # DBT_DEVTYP_DEVICEINTERFACE
        for i, b in enumerate(HID_GUID):
            device_interface.dbcc_classguid[i] = b
        
        ctypes.windll.user32.RegisterDeviceNotificationW(
            self.hwnd,
            byref(device_interface),
            0x00000000 | 0x00000001  # DEVICE_NOTIFY_WINDOW_HANDLE | DEVICE_NOTIFY_ALL_INTERFACE_CLASSES
        )
        
        logger.info("Registered for device notifications")
        
        # Start a thread to run the message loop
        import threading
        
        def msg_loop():
            self.running = True
            while self.running:
                # Process all waiting messages
                if not win32gui.PumpWaitingMessages():
                    break
                time.sleep(0.1)
        
        self.thread = threading.Thread(target=msg_loop, daemon=True)
        self.thread.start()
        
        logger.info("Started message loop for device notifications")
        return True
    
    def _window_proc(self, hwnd, msg, wparam, lparam):
        """Window procedure to handle window messages."""
        if msg == WM_DEVICECHANGE:
            if wparam == DBT_DEVICEARRIVAL or wparam == DBT_DEVICEREMOVECOMPLETE:
                # A device was added or removed
                dev_broadcast_hdr = ctypes.cast(lparam, ctypes.POINTER(DEV_BROADCAST_HDR))
                logger.info(f"Device change event: {wparam}")
                
                # If we have our device and there's a device event, let's assume it might be a touch event
                if self.device:
                    logger.info("Potential touch event detected!")
                    self._process_input_data({"event_type": "device_change", "wparam": wparam})
        
        # Pass to default window procedure
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    
    def _setup_raw_input_monitoring(self):
        """Set up monitoring using Raw Input API."""
        # This is a more complex approach using the Windows Raw Input API
        # It would need to be expanded considerably to work properly
        logger.info("Raw Input monitoring is not yet implemented.")
        return False
    
    def _process_input_data(self, data):
        """Process input data to detect touch events."""
        logger.info(f"Processing input data: {data}")
        
        # The interpretation of the data depends on your specific earbud model
        # This is a generic approach that looks for patterns in the data
        
        if isinstance(data, list) and len(data) > 0:
            # For PyWinUSB data format (usually a list of integers)
            first_byte = data[0]
            if first_byte == 0x01:
                logger.info("Single tap detected")
            elif first_byte == 0x02:
                logger.info("Double tap detected")
            elif first_byte == 0x03:
                logger.info("Long press detected")
            else:
                logger.info(f"Unknown command: {first_byte}")
        elif isinstance(data, dict):
            # For other data formats
            if "event_type" in data and data["event_type"] == "device_change":
                logger.info("Device change event - possible touch event")
                
                # You might need to poll the device status after receiving this event
                # to determine what specific touch event occurred
    
    def run(self):
        """Run the touch event detector."""
        if self.find_connected_device():
            if self.setup_touch_monitoring():
                try:
                    logger.info("Monitoring for events. Press Ctrl+C to exit.")
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    logger.info("Interrupted by user")
                    self.running = False
            else:
                logger.error("Failed to set up touch monitoring.")
        else:
            logger.error("Failed to find connected earbud device.")


def main():
    detector = EarbudTouchDetector()
    detector.run()

if __name__ == "__main__":
    main()