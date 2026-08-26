import os 
from dotenv import load_dotenv

load_dotenv()

WORDPRESS_BASE_URL = os.getenv("WORDPRESS_BASE_URL")
WORDPRESS_USERNAME = os.getenv("WORDPRESS_USERNAME")
WORDPRESS_APP_PASSWORD = os.getenv("WORDPRESS_APP_PASSWORD")
MEC_API_TOKEN = os.getenv("MEC_API_TOKEN")

STORAGE_MODE = os.getenv(
    "STORAGE_MODE",
    "local"
)

S3_BUCKET_NAME = os.getenv(
    "S3_BUCKET_NAME"
)
