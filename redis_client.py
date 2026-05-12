import os
from dotenv import load_dotenv
from upstash_redis import Redis

load_dotenv()

redis = Redis(
    url = os.getenv("UPSTASH_REDIS_REST_URL"),
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
)