from typing import List, Optional
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
# from langchain.pydantic_v1 import BaseModel, Field
from .tasks import TASK_PRIORITY,EMMOTIONS


# Define the response model for PydanticOutputParser
# class VoiceResponse(BaseModel):
#     res: str = Field(description="Output from the LLM")
#     token: str = Field(
#         description="A single token that represents the category of the output")
    
    # # Initialize the output parser
    #     self.output_parser = PydanticOutputParser(
    #         pydantic_object=VoiceResponse)

    #     # Get the format instructions separately
    #     escaped_format_instructions = self.output_parser.get_format_instructions(
    #     ).replace("{", "{{").replace("}", "}}")
       
class WeatherResponse(BaseModel):
    """Structured format for weather information."""
    location: str = Field(description="The location for which weather is provided")
    temperature: float = Field(description="The current temperature in Celsius")
    condition: str = Field(description="The weather condition (sunny, cloudy, rainy, etc.)")
    humidity: Optional[float] = Field(None, description="The humidity percentage")
    
    def format_for_speech(self) -> str:
        """Format the weather response for speech output."""
        speech = f"In {self.location}, it's currently {self.condition} with a temperature of {self.temperature} degrees Celsius."
        
        if self.humidity is not None:
            speech += f" The humidity is {self.humidity}%."
            
        return speech
    
class VoiceResponse(BaseModel):
    """Format for responses with sentiment analysis."""
    # answer: str = Field(description="The answer to the user's question")
    sentiment: str = Field(description=f"The sentiment of the response",enum=list(EMMOTIONS.keys()))
    sentiment_confidence: float = Field(description="Confidence score for the sentiment (0.0 to 1.0)")
    command: str = Field(description=f"Inferrable command from the response",enum=list(TASK_PRIORITY.keys()))
    command_confidence: float = Field(description="Confidence score for the command (0.0 to 1.0)")
    
    # def format_for_speech(self) -> str:
    #     """Format the response for speech output."""
    #     # We can adjust the speech based on confidence and sentiment
    #     return self.answer
    
class MultipartResponse(BaseModel):
    """Format for responses with multiple distinct parts."""
    main_answer: str = Field(description="The primary answer to the query")
    additional_info: Optional[str] = Field(None, description="Any additional context or information")
    follow_up_questions: Optional[List[str]] = Field(None, description="Suggested follow-up questions")
    
    def format_for_speech(self) -> str:
        """Format the multipart response for speech output."""
        speech = self.main_answer
        
        if self.additional_info:
            speech += f" {self.additional_info}"
            
        if self.follow_up_questions and len(self.follow_up_questions) > 0:
            speech += " If you'd like to know more, you could ask me: " + self.follow_up_questions[0]
            
        return speech
    
 