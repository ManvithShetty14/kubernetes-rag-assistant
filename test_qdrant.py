from app.config import settings
from qdrant_client import QdrantClient

client = QdrantClient(
    url=settings.QDRANT_URL,
    api_key=settings.QDRANT_API_KEY,
)

print("Testing Qdrant connection...")

try:
    collections = client.get_collections()

    print("Connection successful!")
    print("Available collections:")

    for collection in collections.collections:
        print(collection.name)

except Exception as e:
    print("Qdrant connection failed!")
    print(e)