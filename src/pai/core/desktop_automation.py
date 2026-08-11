"""Desktop Automation: Cross-platform desktop control and automation."""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import platform
import subprocess

logger = logging.getLogger(__name__)


class MouseButton(Enum):
    """Mouse button types."""
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class KeyModifier(Enum):
    """Keyboard modifiers."""
    SHIFT = "shift"
    CTRL = "ctrl"
    ALT = "alt"
    CMD = "cmd"


@dataclass
class WindowInfo:
    """Information about a window."""
    title: str
    pid: int
    app_name: str
    coordinates: Tuple[int, int, int, int]  # x, y, width, height
    is_active: bool


@dataclass
class ScreenRegion:
    """A region of the screen."""
    x: int
    y: int
    width: int
    height: int
    
    def contains(self, x: int, y: int) -> bool:
        """Check if point is in region."""
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height
    
    def center(self) -> Tuple[int, int]:
        """Get center of region."""
        return (self.x + self.width // 2, self.y + self.height // 2)


class DesktopAutomation:
    """Desktop automation engine for mouse, keyboard, window management."""
    
    def __init__(self):
        """Initialize desktop automation."""
        self.pyautogui = None
        self.cv2 = None
        self.system = platform.system()
        self.screen_width = None
        self.screen_height = None
    
    async def initialize(self) -> None:
        """Initialize automation libraries."""
        try:
            import pyautogui
            import cv2
            
            self.pyautogui = pyautogui
            self.cv2 = cv2
            
            # Get screen dimensions
            size = pyautogui.size()
            self.screen_width = size[0]
            self.screen_height = size[1]
            
            # Enable failsafe (move to corner to abort)
            pyautogui.FAILSAFE = True
            
            logger.info(f"Desktop automation initialized ({self.screen_width}x{self.screen_height})")
        except ImportError as e:
            logger.error(f"Failed to import automation libraries: {e}")
            raise
    
    async def move_mouse(self, x: int, y: int, duration: float = 0.5) -> None:
        """Move mouse to position.
        
        Args:
            x: X coordinate
            y: Y coordinate
            duration: Duration of movement in seconds
        """
        if not self.pyautogui:
            logger.error("Automation not initialized")
            return
        
        try:
            self.pyautogui.moveTo(x, y, duration=duration)
            logger.debug(f"Moved mouse to ({x}, {y})")
        except Exception as e:
            logger.error(f"Mouse movement failed: {e}")
    
    async def click(self, x: int, y: int, button: MouseButton = MouseButton.LEFT,
                   clicks: int = 1, interval: float = 0.1) -> None:
        """Click at position.
        
        Args:
            x: X coordinate
            y: Y coordinate
            button: Mouse button
            clicks: Number of clicks
            interval: Interval between clicks
        """
        if not self.pyautogui:
            return
        
        try:
            await self.move_mouse(x, y, duration=0.2)
            self.pyautogui.click(x, y, clicks=clicks, interval=interval, button=button.value)
            logger.debug(f"Clicked at ({x}, {y}) with {button.value}")
        except Exception as e:
            logger.error(f"Click failed: {e}")
    
    async def double_click(self, x: int, y: int, interval: float = 0.1) -> None:
        """Double click at position."""
        await self.click(x, y, clicks=2, interval=interval)
    
    async def right_click(self, x: int, y: int) -> None:
        """Right click at position."""
        await self.click(x, y, button=MouseButton.RIGHT)
    
    async def drag(self, x1: int, y1: int, x2: int, y2: int,
                  duration: float = 0.5, button: MouseButton = MouseButton.LEFT) -> None:
        """Drag from one position to another.
        
        Args:
            x1: Starting X
            y1: Starting Y
            x2: Ending X
            y2: Ending Y
            duration: Duration of drag
            button: Mouse button to use
        """
        if not self.pyautogui:
            return
        
        try:
            self.pyautogui.drag(x2 - x1, y2 - y1, duration=duration, button=button.value)
            logger.debug(f"Dragged from ({x1}, {y1}) to ({x2}, {y2})")
        except Exception as e:
            logger.error(f"Drag failed: {e}")
    
    async def type_text(self, text: str, interval: float = 0.05) -> None:
        """Type text using keyboard.
        
        Args:
            text: Text to type
            interval: Interval between keystrokes
        """
        if not self.pyautogui:
            return
        
        try:
            self.pyautogui.typewrite(text, interval=interval)
            logger.debug(f"Typed: {text[:50]}...")
        except Exception as e:
            logger.error(f"Type failed: {e}")
    
    async def press_key(self, key: str, presses: int = 1, interval: float = 0.1) -> None:
        """Press a key.
        
        Args:
            key: Key name (e.g., 'enter', 'tab', 'delete')
            presses: Number of presses
            interval: Interval between presses
        """
        if not self.pyautogui:
            return
        
        try:
            self.pyautogui.press(key, presses=presses, interval=interval)
            logger.debug(f"Pressed key: {key}")
        except Exception as e:
            logger.error(f"Key press failed: {e}")
    
    async def key_combination(self, *keys: str) -> None:
        """Press multiple keys together (e.g., Ctrl+C).
        
        Args:
            keys: Keys to press together (e.g., 'ctrl', 'c')
        """
        if not self.pyautogui:
            return
        
        try:
            self.pyautogui.hotkey(*keys)
            logger.debug(f"Pressed combination: {' + '.join(keys)}")
        except Exception as e:
            logger.error(f"Key combination failed: {e}")
    
    async def screenshot(self, region: Optional[ScreenRegion] = None) -> Optional[Any]:
        """Take screenshot.
        
        Args:
            region: Optional region to capture
            
        Returns:
            Screenshot image
        """
        if not self.pyautogui:
            return None
        
        try:
            if region:
                img = self.pyautogui.screenshot(region=(region.x, region.y, region.width, region.height))
            else:
                img = self.pyautogui.screenshot()
            
            logger.debug("Screenshot captured")
            return img
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return None
    
    async def find_on_screen(self, image_path: str, confidence: float = 0.8) -> Optional[Tuple[int, int]]:
        """Find image on screen using template matching.
        
        Args:
            image_path: Path to template image
            confidence: Confidence threshold (0.0-1.0)
            
        Returns:
            (x, y) coordinates if found, None otherwise
        """
        if not self.pyautogui:
            return None
        
        try:
            location = self.pyautogui.locateOnScreen(image_path, confidence=confidence)
            if location:
                x, y = location
                logger.debug(f"Found image at ({x}, {y})")
                return (x, y)
            else:
                logger.debug(f"Image not found on screen")
                return None
        except Exception as e:
            logger.error(f"Image finding failed: {e}")
            return None
    
    async def ocr_screen_text(self, region: Optional[ScreenRegion] = None) -> str:
        """Extract text from screen using OCR.
        
        Args:
            region: Optional region to analyze
            
        Returns:
            Extracted text
        """
        try:
            from paddleocr import PaddleOCR
            
            # Take screenshot
            screenshot = await self.screenshot(region)
            if screenshot is None:
                return ""
            
            # Convert to numpy for OCR
            import numpy as np
            img_array = np.array(screenshot)
            
            # Extract text
            ocr = PaddleOCR(use_angle_cls=True)
            results = ocr.ocr(img_array)
            
            text_parts = []
            if results:
                for line in results:
                    for detection in line:
                        text_parts.append(detection[1])
            
            return " ".join(text_parts)
        except ImportError:
            logger.warning("PaddleOCR not available")
            return ""
        except Exception as e:
            logger.error(f"OCR failed: {e}")
            return ""
    
    async def get_window_info(self, window_title: str) -> Optional[WindowInfo]:
        """Get information about a window.
        
        Args:
            window_title: Window title to search for
            
        Returns:
            Window information or None
        """
        try:
            if self.system == "Windows":
                import win32gui
                hwnd = win32gui.FindWindow(None, window_title)
                if hwnd:
                    x, y, right, bottom = win32gui.GetWindowRect(hwnd)
                    return WindowInfo(
                        title=window_title,
                        pid=0,
                        app_name=window_title,
                        coordinates=(x, y, right - x, bottom - y),
                        is_active=hwnd == win32gui.GetForegroundWindow()
                    )
            elif self.system == "Darwin":  # macOS
                import subprocess
                result = subprocess.run(
                    f'osascript -e "tell application \\"System Events\\" to get properties of (every window whose name contains \\"{window_title}\\")"',
                    shell=True,
                    capture_output=True,
                    text=True
                )
                logger.debug(f"macOS window search: {result.stdout}")
            
            return None
        except Exception as e:
            logger.error(f"Window info retrieval failed: {e}")
            return None
    
    async def activate_window(self, window_title: str) -> bool:
        """Activate/bring window to foreground.
        
        Args:
            window_title: Window title
            
        Returns:
            Success status
        """
        try:
            if self.system == "Windows":
                import win32gui
                hwnd = win32gui.FindWindow(None, window_title)
                if hwnd:
                    win32gui.SetForegroundWindow(hwnd)
                    logger.debug(f"Activated window: {window_title}")
                    return True
            elif self.system == "Darwin":
                subprocess.run(
                    f'osascript -e "tell application \\"{window_title}\\" to activate"',
                    shell=True
                )
                logger.debug(f"Activated window: {window_title}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Window activation failed: {e}")
            return False
    
    async def open_application(self, app_path: str) -> bool:
        """Open an application.
        
        Args:
            app_path: Path to application
            
        Returns:
            Success status
        """
        try:
            if self.system == "Windows":
                subprocess.Popen(app_path)
            elif self.system == "Darwin":
                subprocess.Popen(["open", "-a", app_path])
            elif self.system == "Linux":
                subprocess.Popen([app_path])
            
            logger.info(f"Opened application: {app_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to open application: {e}")
            return False
    
    async def close_window(self, window_title: str) -> bool:
        """Close a window.
        
        Args:
            window_title: Window title
            
        Returns:
            Success status
        """
        try:
            if self.system == "Windows":
                import win32gui
                hwnd = win32gui.FindWindow(None, window_title)
                if hwnd:
                    win32gui.SendMessage(hwnd, 0x10)  # WM_CLOSE
                    logger.debug(f"Closed window: {window_title}")
                    return True
            
            return False
        except Exception as e:
            logger.error(f"Window close failed: {e}")
            return False
