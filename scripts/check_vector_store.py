# scripts/check_vector_store.py
import asyncio
from pai.memory.vector_store import VectorStore

async def main():
    vs = VectorStore(persistence_path="data/chroma_test", collection_name="test_check")
    await vs.initialize()

    # Write
    await vs.add_document("I can't take a photo", {"user_id": "alice", "memory_id": "d1"}, document_id="d1")
    await vs.add_document("I can take a photo", {"user_id": "alice", "memory_id": "d2"}, document_id="d2")
    await vs.add_document("Bob's note", {"user_id": "bob", "memory_id": "d3"}, document_id="d3")

    print("count:", await vs.count())
    print("alice count:", await vs.count(where={"user_id": {"$eq": "alice"}}))

    # get_by_metadata — respects user isolation
    docs = await vs.get_by_metadata({"user_id": {"$eq": "alice"}})
    print("alice docs:", [d["id"] for d in docs])

    # delete_many
    removed = await vs.delete_many(["d1"])
    print("deleted:", removed)
    print("alice count after:", await vs.count(where={"user_id": {"$eq": "alice"}}))

    # delete_by_metadata
    removed = await vs.delete_by_metadata({"user_id": {"$eq": "alice"}})
    print("deleted by metadata:", removed)
    print("total after:", await vs.count())

    # clear
    await vs.clear()
    print("after clear:", await vs.count())
    await vs.shutdown()

asyncio.run(main())