"""Mobile Automation: Android device control and app automation via ADB."""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import subprocess

logger = logging.getLogger(__name__)


class AndroidKeyCode(Enum):
    """Common Android key codes."""
    BACK = 4
    HOME = 3
    POWER = 26
    VOLUME_UP = 24
    VOLUME_DOWN = 25
    ENTER = 66
    TAB = 61
    DEL = 67


@dataclass
class AndroidDevice:
    """Information about an Android device."""
    device_id: str
    name: str
    model: str
    android_version: str
    status: str  # device, unauthorized, offline


@dataclass
class AppInfo:
    """Information about an installed app."""
    package_name: str
    label: str
    version: str
    is_system: bool


class MobileAutomation:
    """Mobile automation engine for Android devices."""
    
    def __init__(self):
        """Initialize mobile automation."""
        self.adb_path = "adb"
        self.current_device = None
    
    async def initialize(self) -> None:
        """Initialize ADB and check for devices."""
        try:
            # Check if adb is available
            result = subprocess.run(
                [self.adb_path, "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info("ADB initialized")
            else:
                logger.error("ADB not found or not working")
                raise RuntimeError("ADB not available")
        except Exception as e:
            logger.error(f"ADB initialization failed: {e}")
            raise
    
    async def get_devices(self) -> List[AndroidDevice]:
        """Get list of connected devices.
        
        Returns:
            List of connected Android devices
        """
        try:
            result = subprocess.run(
                [self.adb_path, "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            devices = []
            for line in result.stdout.split('\n')[1:]:
                if not line.strip() or line.startswith("List of attached"):
                    continue
                
                parts = line.split()
                if len(parts) >= 2:
                    device_id = parts[0]
                    status = parts[1]
                    
                    # Parse additional info if available
                    info_str = ' '.join(parts[2:])
                    match = re.search(r'model:([\w\s-]+)\s+device:(\w+)\s+transport_id', info_str)
                    
                    device = AndroidDevice(
                        device_id=device_id,
                        name=device_id.split('-')[0] if '-' in device_id else device_id,
                        model=match.group(1).strip() if match else "Unknown",
                        android_version="Unknown",
                        status=status
                    )
                    devices.append(device)
            
            logger.info(f"Found {len(devices)} devices")
            return devices
        except Exception as e:
            logger.error(f"Failed to get devices: {e}")
            return []
    
    async def select_device(self, device_id: str) -> bool:
        """Select a device for operations.
        
        Args:
            device_id: Device ID to select
            
        Returns:
            Success status
        """
        devices = await self.get_devices()
        if any(d.device_id == device_id for d in devices):
            self.current_device = device_id
            logger.info(f"Selected device: {device_id}")
            return True
        else:
            logger.error(f"Device not found: {device_id}")
            return False
    
    def _adb_cmd(self, *args) -> Tuple[int, str, str]:
        """Execute adb command."""
        if self.current_device:
            cmd = [self.adb_path, "-s", self.current_device] + list(args)
        else:
            cmd = [self.adb_path] + list(args)
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return 1, "", "Command timeout"
        except Exception as e:
            return 1, "", str(e)
    
    async def install_app(self, apk_path: str) -> bool:
        """Install APK on device.
        
        Args:
            apk_path: Path to APK file
            
        Returns:
            Success status
        """
        if not self.current_device:
            logger.error("No device selected")
            return False
        
        returncode, stdout, stderr = self._adb_cmd("install", apk_path)
        
        if returncode == 0 and "Success" in stdout:
            logger.info(f"Installed app: {apk_path}")
            return True
        else:
            logger.error(f"Install failed: {stderr}")
            return False
    
    async def uninstall_app(self, package_name: str) -> bool:
        """Uninstall app from device.
        
        Args:
            package_name: Package name of app
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        returncode, stdout, stderr = self._adb_cmd("uninstall", package_name)
        
        if returncode == 0:
            logger.info(f"Uninstalled app: {package_name}")
            return True
        else:
            logger.error(f"Uninstall failed: {stderr}")
            return False
    
    async def get_installed_apps(self) -> List[AppInfo]:
        """Get list of installed apps.
        
        Returns:
            List of installed applications
        """
        if not self.current_device:
            return []
        
        returncode, stdout, stderr = self._adb_cmd("shell", "pm", "list", "packages", "-3")
        
        apps = []
        for line in stdout.strip().split('\n'):
            if line.startswith("package:"):
                package_name = line.replace("package:", "").strip()
                apps.append(AppInfo(
                    package_name=package_name,
                    label=package_name,
                    version="Unknown",
                    is_system=False
                ))
        
        logger.info(f"Found {len(apps)} user apps")
        return apps
    
    async def launch_app(self, package_name: str, activity: Optional[str] = None) -> bool:
        """Launch an app on device.
        
        Args:
            package_name: Package name of app
            activity: Optional activity name
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        if activity:
            component = f"{package_name}/{activity}"
        else:
            component = package_name
        
        returncode, stdout, stderr = self._adb_cmd("shell", "am", "start", "-n", component)
        
        if returncode == 0:
            logger.info(f"Launched app: {package_name}")
            return True
        else:
            logger.error(f"Launch failed: {stderr}")
            return False
    
    async def close_app(self, package_name: str) -> bool:
        """Close an app on device.
        
        Args:
            package_name: Package name of app
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        returncode, stdout, stderr = self._adb_cmd("shell", "am", "force-stop", package_name)
        
        if returncode == 0:
            logger.info(f"Closed app: {package_name}")
            return True
        else:
            logger.error(f"Close failed: {stderr}")
            return False
    
    async def tap(self, x: int, y: int) -> bool:
        """Tap on screen at coordinates.
        
        Args:
            x: X coordinate
            y: Y coordinate
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        returncode, _, stderr = self._adb_cmd("shell", "input", "tap", str(x), str(y))
        
        if returncode == 0:
            logger.debug(f"Tapped at ({x}, {y})")
            return True
        else:
            logger.error(f"Tap failed: {stderr}")
            return False
    
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 500) -> bool:
        """Swipe on screen.
        
        Args:
            x1: Starting X
            y1: Starting Y
            x2: Ending X
            y2: Ending Y
            duration: Duration in milliseconds
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        returncode, _, stderr = self._adb_cmd(
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        )
        
        if returncode == 0:
            logger.debug(f"Swiped from ({x1}, {y1}) to ({x2}, {y2})")
            return True
        else:
            logger.error(f"Swipe failed: {stderr}")
            return False
    
    async def type_text(self, text: str) -> bool:
        """Type text on device.
        
        Args:
            text: Text to type
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        # Escape special characters
        escaped_text = text.replace(" ", "%s").replace("&", "\\&")
        
        returncode, _, stderr = self._adb_cmd("shell", "input", "text", escaped_text)
        
        if returncode == 0:
            logger.debug(f"Typed: {text[:50]}...")
            return True
        else:
            logger.error(f"Type failed: {stderr}")
            return False
    
    async def press_key(self, key_code: int) -> bool:
        """Press a key on device.
        
        Args:
            key_code: Android key code
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        returncode, _, stderr = self._adb_cmd("shell", "input", "keyevent", str(key_code))
        
        if returncode == 0:
            logger.debug(f"Pressed key: {key_code}")
            return True
        else:
            logger.error(f"Key press failed: {stderr}")
            return False
    
    async def screenshot(self, output_path: Optional[str] = None) -> Optional[bytes]:
        """Take screenshot on device.
        
        Args:
            output_path: Optional path to save screenshot
            
        Returns:
            Screenshot data or None
        """
        if not self.current_device:
            return None
        
        try:
            if output_path:
                returncode, _, stderr = self._adb_cmd("exec-out", "screencap", "-p")
            else:
                returncode, stdout, stderr = self._adb_cmd("exec-out", "screencap", "-p")
            
            if returncode == 0:
                logger.debug("Screenshot captured")
                if output_path:
                    with open(output_path, 'wb') as f:
                        f.write(stdout.encode())
                    return None
                else:
                    return stdout.encode() if isinstance(stdout, str) else stdout
            else:
                logger.error(f"Screenshot failed: {stderr}")
                return None
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            return None
    
    async def get_screen_info(self) -> Dict[str, Any]:
        """Get screen information from device.
        
        Returns:
            Screen information
        """
        if not self.current_device:
            return {}
        
        returncode, stdout, _ = self._adb_cmd("shell", "wm", "size")
        
        info = {}
        for line in stdout.strip().split('\n'):
            if "Physical size:" in line:
                dimensions = line.replace("Physical size:", "").strip()
                parts = dimensions.split('x')
                if len(parts) == 2:
                    info["width"] = int(parts[0])
                    info["height"] = int(parts[1])
        
        return info
    
    async def read_logcat(self, lines: int = 100) -> List[str]:
        """Read device logcat output.
        
        Args:
            lines: Number of lines to read
            
        Returns:
            List of logcat lines
        """
        if not self.current_device:
            return []
        
        returncode, stdout, _ = self._adb_cmd("logcat", "-d", "-t", str(lines))
        
        if returncode == 0:
            return stdout.strip().split('\n')
        else:
            return []
    
    async def push_file(self, local_path: str, device_path: str) -> bool:
        """Push file to device.
        
        Args:
            local_path: Local file path
            device_path: Device destination path
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        returncode, _, stderr = self._adb_cmd("push", local_path, device_path)
        
        if returncode == 0:
            logger.info(f"Pushed file: {local_path} → {device_path}")
            return True
        else:
            logger.error(f"Push failed: {stderr}")
            return False
    
    async def pull_file(self, device_path: str, local_path: str) -> bool:
        """Pull file from device.
        
        Args:
            device_path: Device file path
            local_path: Local destination path
            
        Returns:
            Success status
        """
        if not self.current_device:
            return False
        
        returncode, _, stderr = self._adb_cmd("pull", device_path, local_path)
        
        if returncode == 0:
            logger.info(f"Pulled file: {device_path} → {local_path}")
            return True
        else:
            logger.error(f"Pull failed: {stderr}")
            return False
