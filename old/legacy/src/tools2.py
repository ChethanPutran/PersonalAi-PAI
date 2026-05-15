from PIL import Image
import cv2
import os
import logging
from datetime import datetime
from typing import Optional
import requests
from pydantic import BaseModel, Field
from transformers import BlipProcessor, BlipForConditionalGeneration
import torch
from langchain_core.tools import tool, StructuredTool
from langgraph.types import interrupt
from langchain_community.tools import DuckDuckGoSearchRun

# ==================== Configuration & Logging ====================

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment validation
def validate_environment(show_warnings: bool = False) -> bool:
    """Validate environment variables and warn when optional services are missing."""
    optional_vars = {
        "NVIDIA_API_KEY": "NVIDIA API key for LLM access",
        "GOOGLE_API_KEY": "Google API key for LLM access",
        "OPENAI_API_KEY": "OpenAI API key for TTS and LLM access",
    }
    tool_specific_vars = {
        "WEATHER_API_KEY": "API key for OpenWeatherMap to enable weather tool",
    }

    warnings = []

    for var, description in optional_vars.items():
        if not os.getenv(var):
            warnings.append(f"{var} ({description}) is not set. Related features will be skipped.")
    
    for var, description in tool_specific_vars.items():
        if not os.getenv(var):
            warnings.append(f"{var} ({description}) is not set. Related tool will be unavailable.")
    
    if show_warnings and warnings:
        for warning in warnings:
            logger.warning(warning)
    
    return True

# ==================== Pydantic Schemas ====================

class SearchInput(BaseModel):
    """Input schema for search tool"""
    query: str = Field(description="The search query to look up")

class TimeZoneInput(BaseModel):
    """Input schema for time tool"""
    timezone: str = Field(
        default="local",
        description="The timezone to get current time for. Use 'local' for local time, or specify like 'America/New_York'"
    )

class WeatherInput(BaseModel):
    """Input schema for weather tool"""
    location: str = Field(
        description="The city and state/country to get weather for (e.g., 'New York, US' or 'London, UK')"
    )

class EmptyInput(BaseModel):
    """Empty input schema for tools that take no arguments"""
    pass

class HumanAssistanceInput(BaseModel):
    """Input schema for human assistance tool"""
    query: str = Field(description="The question or request for human assistance")

# ==================== Singleton Pattern for BLIP Model ====================

class BLIPCaptioner:
    """
    Singleton class to manage BLIP model loading and inference.
    Ensures model is loaded only once and reused across calls.
    """
    _instance = None
    _processor = None
    _model = None
    _device = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BLIPCaptioner, cls).__new__(cls)
        return cls._instance
    
    @classmethod
    def get_model(cls):
        """Lazy load the BLIP model and processor"""
        if cls._model is None:
            try:
                logger.info("Loading BLIP model for image captioning...")
                cls._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                logger.info(f"Using device: {cls._device}")
                
                cls._processor = BlipProcessor.from_pretrained(
                    "Salesforce/blip-image-captioning-base"
                )
                cls._model = BlipForConditionalGeneration.from_pretrained(
                    "Salesforce/blip-image-captioning-base"
                ).to(cls._device)
                
                logger.info("BLIP model loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load BLIP model: {str(e)}")
                raise
        return cls._processor, cls._model, cls._device
    
    @classmethod
    def generate_caption(cls, image: Image.Image, max_tokens: int = 30) -> str:
        """Generate a caption for the given PIL image"""
        try:
            processor, model, device = cls.get_model()
            inputs = processor(images=image, return_tensors="pt").to(device)
            
            with torch.no_grad():
                output = model.generate(
                    **inputs, 
                    max_new_tokens=max_tokens,
                    num_beams=3,
                    temperature=0.7
                )
            
            caption = processor.decode(output[0], skip_special_tokens=True)
            return caption
        except Exception as e:
            logger.error(f"Error generating caption: {str(e)}")
            raise

# ==================== Camera Utilities ====================

class CameraManager:
    """Manages camera operations with proper resource handling"""
    
    @staticmethod
    def capture_image(camera_id: int = 0, timeout: int = 5) -> Optional[Image.Image]:
        """
        Capture an image from the camera.
        
        Args:
            camera_id: Camera device ID (default: 0)
            timeout: Timeout in seconds for camera initialization
            
        Returns:
            PIL Image or None if capture failed
        """
        cap = None
        try:
            cap = cv2.VideoCapture(camera_id)
            
            if not cap.isOpened():
                logger.error(f"Could not open camera with ID {camera_id}")
                return None
            
            # Set camera properties for better quality
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            
            # Read frame with timeout
            import time
            start_time = time.time()
            flag = False
            img = None
            
            while time.time() - start_time < timeout:
                flag, img = cap.read()
                if flag and img is not None:
                    break
            
            if not flag or img is None:
                logger.error("Failed to capture image from camera")
                return None
            
            # Convert BGR to RGB
            cv_image = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(cv_image)
            
            logger.info("Image captured successfully")
            return pil_image
            
        except Exception as e:
            logger.error(f"Error capturing image: {str(e)}")
            return None
        finally:
            if cap is not None:
                cap.release()

# ==================== Tool Implementations ====================

# Built-in tools
search_tool = DuckDuckGoSearchRun()

@tool(args_schema=HumanAssistanceInput)
def human_assistance(query: str) -> str:
    """
    Request assistance from a human when the AI cannot answer a query.
    This will interrupt the execution and wait for human input.
    """
    logger.info(f"Requesting human assistance for: {query}")
    human_response = interrupt({"query": query})
    return human_response["data"]

def time_tool_func(timezone: str = "local") -> str:
    """
    Get the current time for a specific timezone.
    
    Args:
        timezone: Timezone string (e.g., 'local', 'America/New_York', 'Europe/London')
    """
    try:
        if timezone.lower() == "local":
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return f"The current local time is {current_time}"
        else:
            # Attempt to use pytz if available for proper timezone support
            try:
                import pytz
                tz = pytz.timezone(timezone)
                current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
                return f"The current time in {timezone} is {current_time}"
            except ImportError:
                logger.warning("pytz not installed, using local time")
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return f"The current time is {current_time} (Note: timezone '{timezone}' requires pytz library)"
            except Exception as e:
                logger.error(f"Timezone error: {str(e)}")
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return f"The current local time is {current_time} (Could not apply timezone '{timezone}': {str(e)})"
    except Exception as e:
        logger.error(f"Error getting time: {str(e)}")
        return f"Error getting time: {str(e)}"

def weather_tool_func(location: str) -> str:
    """
    Get current weather for a location using OpenWeatherMap API.
    
    Args:
        location: City name with optional state/country (e.g., 'New York, US')
    """
    api_key = os.getenv("WEATHER_API_KEY")
    
    if not api_key:
        return "Weather service is not configured. Please set WEATHER_API_KEY environment variable."
    
    base_url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": location,
        "appid": api_key,
        "units": "metric"
    }
    
    try:
        logger.info(f"Fetching weather for location: {location}")
        response = requests.get(base_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if response.status_code == 200:
            temp = data["main"]["temp"]
            feels_like = data["main"]["feels_like"]
            description = data["weather"][0]["description"]
            humidity = data["main"]["humidity"]
            wind_speed = data["wind"]["speed"]
            
            weather_info = (
                f"Weather in {location}: {description}, "
                f"temperature {temp}°C (feels like {feels_like}°C), "
                f"humidity {humidity}%, wind speed {wind_speed} m/s"
            )
            logger.info(f"Weather retrieved: {weather_info}")
            return weather_info
        else:
            error_msg = data.get('message', 'Unknown error')
            logger.error(f"Weather API error: {error_msg}")
            return f"Error getting weather: {error_msg}"
            
    except requests.exceptions.Timeout:
        logger.error("Weather request timed out")
        return "Weather request timed out. Please try again."
    except requests.exceptions.ConnectionError:
        logger.error("Network error while fetching weather")
        return "Network error: Unable to connect to weather service."
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {str(e)}")
        return f"Failed to get weather information: {str(e)}"
    except KeyError as e:
        logger.error(f"Unexpected API response format: {str(e)}")
        return "Received unexpected response format from weather service."
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"An unexpected error occurred: {str(e)}"

def capdesc_tool_func() -> str:
    """
    Capture an image from the camera and generate a description using BLIP model.
    """
    try:
        logger.info("Attempting to capture image from camera")
        
        # Capture image
        pil_image = CameraManager.capture_image()
        
        if pil_image is None:
            return "Unable to capture image from camera. Please check camera connection and permissions."
        
        logger.info("Image captured successfully, generating caption...")
        
        # Generate caption using BLIP
        caption = BLIPCaptioner.generate_caption(pil_image)
        
        logger.info(f"Caption generated: {caption}")
        return f"I see: {caption}"
        
    except Exception as e:
        logger.error(f"Error in capdesc_tool: {str(e)}")
        return f"Failed to capture and describe image: {str(e)}"


def health_check() -> dict:
    """Perform a health check on all components"""
    health_status = {
        "environment": validate_environment(),
        "camera": False,
        "blip_model": False,
        "llm": False,
        "tools": {}
    }
    
    # Check camera
    try:
        img = CameraManager.capture_image(timeout=2)
        health_status["camera"] = img is not None
    except Exception as e:
        logger.error(f"Camera check failed: {str(e)}")
    
    # Check BLIP model
    try:
        BLIPCaptioner.get_model()
        health_status["blip_model"] = True
    except Exception as e:
        logger.error(f"BLIP model check failed: {str(e)}")
    
    # Check tool availability
    for tool in [weather_tool, time_tool, search_tool, capdesc_tool, human_assistance]:
        health_status["tools"][tool.name] = tool is not None
    
    return health_status

# ==================== Create Structured Tools ====================

weather_tool = StructuredTool.from_function(
    func=weather_tool_func,
    name="weather_tool",
    description="Get the current weather for a location. Returns temperature, conditions, humidity, and wind speed.",
    args_schema=WeatherInput
)

time_tool = StructuredTool.from_function(
    func=time_tool_func,
    name="time_tool",
    description="Get the current time. Use 'local' for local time, or specify a timezone like 'America/New_York'.",
    args_schema=TimeZoneInput
)

capdesc_tool = StructuredTool.from_function(
    func=capdesc_tool_func,
    name="capdesc_tool",
    description="Capture an image from the camera and describe what is seen using AI vision.",
    args_schema=EmptyInput
)
