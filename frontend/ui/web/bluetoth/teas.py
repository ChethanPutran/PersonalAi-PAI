"""
Bluetooth Earbud Touch Event Detector
------------------------------------
This script connects to a Bluetooth earbud device and detects touch events.
It requires the 'bleak' library for Bluetooth Low Energy communication.

Install required dependencies:
pip install bleak asyncio
"""

import asyncio
import logging
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Set your earbud's name or part of the name here
EARBUD_NAME_CONTAINS = "Noise Buds N1 Pro"  # Change this to match your device name

# Some common Bluetooth service and characteristic UUIDs
# You may need to adjust these based on your specific earbud model
TOUCH_SERVICE_UUID = None  # Will be discovered during scanning
TOUCH_CHARACTERISTIC_UUID = None  # Will be discovered during scanning

class EarbudTouchDetector:
    def __init__(self, device_name_contains=EARBUD_NAME_CONTAINS):
        self.device_name_contains = device_name_contains
        self.device = None
        self.client = None
        self.touch_service = None
        self.touch_characteristic = None
        
    async def scan_for_device(self):
        """Scan for the earbud device."""
        logger.info(f"Scanning for devices containing '{self.device_name_contains}' in the name...")
        
        devices = await BleakScanner.discover()
        for device in devices:
            if device.name and self.device_name_contains.lower() in device.name.lower():
                self.device = device
                logger.info(f"Found device: {device.name} ({device.address})")
                return True
                
        logger.error(f"No device found with '{self.device_name_contains}' in the name.")
        return False
    
    async def connect_to_device(self):
        """Connect to the earbud device."""
        if not self.device:
            logger.error("No device to connect to. Run scan_for_device first.")
            return False
            
        try:
            logger.info(f"Connecting to {self.device.name}...")
            self.client = BleakClient(self.device)
            await self.client.connect()
            logger.info(f"Connected to {self.device.name}")
            return True
        except BleakError as e:
            logger.error(f"Error connecting to device: {e}")
            return False
    
    async def discover_services(self):
        """Discover available services and characteristics."""
        if not self.client or not self.client.is_connected:
            logger.error("Not connected to any device.")
            return False
            
        logger.info("Discovering services...")
        services = await self.client.get_services()
        
        # Print all services and characteristics for debugging
        for service in services:
            logger.info(f"Service: {service.uuid}")
            for char in service.characteristics:
                props = []
                if "read" in char.properties:
                    props.append("read")
                if "write" in char.properties:
                    props.append("write")
                if "notify" in char.properties:
                    props.append("notify")
                logger.info(f"  Characteristic: {char.uuid}, Properties: {', '.join(props)}")
        
        return True
    
    async def setup_touch_notifications(self):
        """Set up notifications for touch events on the appropriate characteristic."""
        if not self.client or not self.client.is_connected:
            logger.error("Not connected to any device.")
            return False
        
        # You need to identify the correct characteristic for touch events
        # This is a placeholder - you'll need to determine the actual UUID based on your device
        # Some common GATT services/characteristics you might check:
        # - Human Interface Device Service (0x1812)
        # - Battery Service (0x180F)
        # - Custom services from your earbud manufacturer
        
        logger.info("Setting up notifications for touch events...")
        
        # Example: subscribing to a hypothetical touch characteristic
        # Replace this with your actual characteristic UUID once identified
        try:
            services = await self.client.get_services()
            for service in services:
                for char in service.characteristics:
                    if "notify" in char.properties:
                        # This is a candidate for touch events
                        logger.info(f"Setting up notifications for characteristic: {char.uuid}")
                        await self.client.start_notify(char.uuid, self.touch_callback)
                        logger.info(f"Successfully set up notifications for {char.uuid}")
        except BleakError as e:
            logger.error(f"Error setting up notifications: {e}")
            return False
            
        return True
    
    def touch_callback(self, sender, data):
        """Callback function for touch events."""
        logger.info(f"Touch event detected! Data: {data.hex()}")
        # Decode the touch event based on the data received
        self.process_touch_data(data)
    
    def process_touch_data(self, data):
        """Process and interpret the touch data."""
        # This will need to be customized based on your earbud's specific protocol
        try:
            # Example interpretation - modify according to your device's protocol
            if len(data) > 0:
                command_byte = data[0]
                if command_byte == 0x01:
                    logger.info("Single tap detected")
                elif command_byte == 0x02:
                    logger.info("Double tap detected")
                elif command_byte == 0x03:
                    logger.info("Long press detected")
                else:
                    logger.info(f"Unknown command: {command_byte}")
        except Exception as e:
            logger.error(f"Error processing touch data: {e}")
    
    async def disconnect(self):
        """Disconnect from the device."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            logger.info("Disconnected from device")

async def main():
    detector = EarbudTouchDetector()

    await detector.scan_for_device()

    print(sel)
    
    # Scan for and connect to the device
    if await detector.scan_for_device():
        if await detector.connect_to_device():
            # Discover and print services (useful for finding the right characteristics)
            await detector.discover_services()
            
            # Setup notifications for touch events
            await detector.setup_touch_notifications()
            
            # Keep the connection alive to receive notifications
            logger.info("Listening for touch events. Press Ctrl+C to exit.")
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                logger.info("Interrupted by user")
            finally:
                await detector.disconnect()
        else:
            logger.error("Failed to connect to device")
    else:
        logger.error("No matching device found")

if __name__ == "__main__":
    # Run the main function
    asyncio.run(main())