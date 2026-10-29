import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # reads .env into environment variables
client = OpenAI(
    base_url="https://inference.baseten.co/v1",
    api_key=os.environ["BASETEN_API_KEY"],
)
r = client.chat.completions.create(
    model="meta-models/muse-spark-open-1026",
    messages=[{"role": "user", "content": "Reply with the single word: ready"}],
    max_tokens=500,
)
print(repr(r.choices[0].message.content))
print(r.usage)