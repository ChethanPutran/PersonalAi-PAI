from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from functools import wraps
from queue import PriorityQueue
import threading
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from .output_parsers import MultipartResponse, VoiceResponse,WeatherResponse
from .tools import weather_tool, search_tool, capdesc_tool,time_tool,human_assistance
from langchain_core.messages import HumanMessage,SystemMessage
from langchain.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.graph.graph import CompiledGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command

# Load environment variables
load_dotenv("../env")

NUM_LLM_WORKERS = 1
DAEMON = True

class State(TypedDict):
    # Messages have the type "list". The `add_messages` function
    # in the annotation defines how this state key should be updated
    # (in this case, it appends messages to the list, rather than overwriting them)
    messages: Annotated[list, add_messages]


class LLMRunner:
    def __init__(self,tasks_commands=None,no_workers=NUM_LLM_WORKERS, daemon=True):
        self.no_workers = no_workers
        self.tasks_commands = tasks_commands
        self.daemon = daemon
        self.threads: list[threading.Thread] = []
        self.llm_task_queue = PriorityQueue()
        self.running = threading.Event()
        self.running.set()

    def set_comands(self, commands):
        self.tasks_commands = commands

    def llm_worker(self):
        pass
       
    def close(self):
        self.running.clear()

    def llm_wrapper(self, func):
        @wraps(func)
        def wrapper(msg, priority):
            self.llm_task_queue.put((priority, (msg, func)))
        return wrapper

    def start(self):
        for _ in range(self.no_workers):
            th = threading.Thread(target=self.llm_worker, daemon=self.daemon)
            th.start()
            self.threads.append(th)

    def wait(self):
        if not self.daemon:
            for th in self.threads:
                th.join()


class AssistantAgent(LLMRunner):
    def __init__(self,model = "meta/llama-3.1-70b-instruct",name="Chvis",visualize=False,no_workers=NUM_LLM_WORKERS,daemon=DAEMON):
        super().__init__(no_workers,daemon)
        self.assistant_name = name
        tool_models = [
            model.id for model in ChatNVIDIA.get_available_models() if model.supports_tools
        ]
        if model not in tool_models:
            raise Exception(f"Provided model: f{model} does not support tool binding!")
        
        self.llm = ChatNVIDIA(model=model)
        self.tools = [
            weather_tool,
            time_tool,
            search_tool,
            capdesc_tool,
            human_assistance

        ]
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        self.tool_runner = ToolNode(self.tools) 
        graph_builder = StateGraph(State)

        graph_builder.add_node("tools", self.tool_runner)
        graph_builder.add_node("chatbot", self.chatbot)
        graph_builder.add_node("chatbot_with_tools", self.chatbot_with_toools)

        # Add an entry point to tell the graph where to start its work each time it is run
        graph_builder.add_edge(START, "chatbot")
        graph_builder.add_conditional_edges(
            "chatbot",
            tools_condition)

        self.graph = graph_builder.compile()

        if visualize:
            self.visualize(self.graph)

        response_parser = PydanticOutputParser(pydantic_object=VoiceResponse)
        multipart_parser = PydanticOutputParser(pydantic_object=MultipartResponse)
        weather_parser = PydanticOutputParser(pydantic_object=WeatherResponse)
        memory  = MemorySaver()
        propmt = PromptTemplate(
            template="""
            You are a voice assistant that needs to provide clear, concise responses.
            
            Below is the raw response to the user's query:
            {messages}
            
            Please format this as a response with sentiment analysis, following this format:
            {format_instructions}
            
            Ensure the response is conversational and suitable for speech output.
            """,
            input_variables=["messages"],
            partial_variables={
                "format_instructions": response_parser.get_format_instructions()}
        )
        self.agent_executor:CompiledGraph = create_react_agent(self.llm,prompt=propmt, response_format=VoiceResponse, checkpointer=memory,tools=[])
    
    def add_human_assistance(self,graph,config):
        
        human_command = Command(resume={"data": human_response})
        human_response = (
            "We, the experts are here to help! We'd recommend you check out LangGraph to build your agent."
            " It's much more reliable and extensible than simple autonomous agents."
        )

        human_command = Command(resume={"data": human_response})

        events = graph.stream(human_command, config, stream_mode="values")
        for event in events:
            if "messages" in event:
                event["messages"][-1].pretty_print()

    def route_tools(self,state: State):
        """
        Use in the conditional_edge to route to the ToolNode if the last message
        has tool calls. Otherwise, route to the end.
        """
        if isinstance(state, list):
            ai_message = state[-1]
        elif messages := state.get("messages", []):
            ai_message = messages[-1]
        else:
            raise ValueError(f"No messages found in input state to tool_edge: {state}")
        if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
            return "tools"
        return END

    def print_interraction(self,graph:CompiledGraph,user_input: str):
        for event in graph.stream({"messages": [{"role": "user", "content": user_input}]}):
            for value in event.values():
                print("Assistant:", value["messages"][-1].content)

    def visualize(self,graph:CompiledGraph):
        from IPython.display import Image, display

        try:
            display(Image(graph.get_graph().draw_mermaid_png()))
        except Exception:
            # This requires some extra dependencies and is optional
            pass
        
    def chatbot_with_toools(self,state: State):
        return {"messages": [self.llm_with_tools.invoke(state["messages"])]}
    
    def chatbot(self,state: State):

        # Define the system message to introduce the assistant
        system_message = SystemMessagePromptTemplate.from_template(
            "You are an intelligent and helpful AI assistant named Chvis. "
            "Always refer to yourself as 'Chvis' and respond in a polite and professional tone."
        )

        # Define the user message template (e.g., dynamic user input)
        human_message = HumanMessagePromptTemplate.from_template("{input}")

        # Create the full chat prompt template
        chat_prompt = ChatPromptTemplate.from_messages([
            system_message,
            human_message
        ])
        self.model = chat_prompt | self.llm
        return {"messages": [self.model.invoke({"input": state["messages"]})]}

    def print_history(self,messages):
        for step in messages:
            step.pretty_print()

    def process_query(self, query,thread_id="test123",print_hist=False,parse=True):
        """Process user query and return structured response"""
        # try:
        # Get raw response from agent
        config = {"configurable": {"thread_id": thread_id}}
        res = self.graph.invoke({"messages": query}, config)
        ai_message = res["messages"][-1]
        if print_hist:
            self.print_history(res["messages"])
        
        if parse:
            res = self.agent_executor.invoke({"messages": [ai_message]},config)['structured_response']

        return (ai_message,res)
            
    def llm_worker(self):
        while self.running.is_set():
            if self.tasks_commands is None:
                print(f"❌ LLM Worker Error: Commands are not set!")
                self.close()
            try:
                priority, (msg, func) = self.llm_task_queue.get()
                print("Msg :",priority, (msg, func))
                response = self.process_query(msg)
                print("Res :",response)
                func(msg,response)
            except Exception as e:
                print(f"❌ LLM Worker Error: {e}")
            finally:
                self.llm_task_queue.task_done()


def test_assistant():
    import time
    import sys
    from tasks import TASK_PRIORITY

    llm = AssistantAgent()

    @llm.llm_wrapper
    def handle_message(input_message,llm_response, sentiment):
        print(
            f"Input Message : {input_message} LLM Response : {llm_response} Task : {sentiment} Priority : {TASK_PRIORITY[sentiment]}")

    try:
        handle_message("How are you?", 1)
        handle_message("Can you record the task that I am doing?", 2)
        llm.start()
        time.sleep(3)
        handle_message("Stop the recording", 2)
        time.sleep(2)
        handle_message("Start processing the task", 2)
        time.sleep(2)
        handle_message("Start execution of the task", 2)
        # time.sleep(2)
        handle_message("Stop the execution its emergency!", 2)
        time.sleep(5)
    except KeyboardInterrupt:
        sys.exit(0)
    finally:
        llm.close()


if __name__ == "__main__":
    # test_assistant()
    AssistantAgent()
