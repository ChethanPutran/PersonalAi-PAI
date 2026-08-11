from fastapi import FastAPI,File, UploadFile
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from ....src.agent.agent import AssistantAgent
import whisper
import openai
import tempfile
from dotenv import load_dotenv

load_dotenv(".env")

app = FastAPI()
model = whisper.load_model("base")  # or tiny for faster load
assistance_agent = AssistantAgent()

# openai.api_key = "your-api-key"

@app.post("/process_audio/")
async def process_audio(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    # Transcribe
    result = model.transcribe(tmp_path)
    user_text = result["text"]

    # # LLM Response
    # response = openai.ChatCompletion.create(
    #     model="gpt-3.5-turbo",
    #     messages=[
    #         {"role": "user", "content": user_text}
    #     ]
    # )
    # ai_reply = response["choices"][0]["message"]["content"]
    # return {"query": user_text, "response": ai_reply}
    # ai_reply = assistance_agent.process_query(user_text)
    return {"response": user_text}


class Item(BaseModel):
    name: str
    description: str | None = None
    price: float
    tax: float | None = None


app = FastAPI()


@app.post("/items/")
async def create_item(item: Item):
    print(item)
    return item


@app.get("/items/", response_class=HTMLResponse)

async def read_items():
    return """
    <html>
        <head>
            <title>Some HTML in here</title>
        </head>
        <body>
            <h1>Create Item</h1>
            <form method="POST" action="/items/">
            <input name="name" type="text" placeholder="Item Name">
            <input name="description" type="text" placeholder="Item Description">
            <input name="price" type="number" placeholder="Item Price">
            <input name="tax" type="number" placeholder="Item Tax">
            <input type="submit" placeholder="Submit">
            </form>
        </body>
    </html>
    """


@app.get("/")
async def home():
    return {"message":"Welcome to PAi App"}