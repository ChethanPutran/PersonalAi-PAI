import chromadb
from chromadb.utils import embedding_functions
from datetime import datetime
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any
from langchain_core.messages import (
    BaseMessage, HumanMessage, SystemMessage, AIMessage
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import logger
from langgraph.checkpoint.postgres import PostgresSaver
from .enums import *
import sqlite3
from pathlib import Path

POSTGRES_USER="postgres"
POSTGRES_PASSWORD="postgres"
POSTGRES_DB="postgres"
PERSIST_DIRECTORY = Path(__file__).parent.parent / "data" 
PERSIST_DIRECTORY.mkdir(parents=True, exist_ok=True)


DB_URI = f'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:{POSTGRES_DB}' 

class Memory:
    def __init__(self, persist_directory=PERSIST_DIRECTORY):
        self.client = chromadb.PersistentClient(path=persist_directory / "chroma_db")
        
        # Use OpenAI embeddings (or you can use sentence-transformers locally)
        self.embedding_fn = embedding_functions.GoogleGenaiEmbeddingFunction(
            model_name="gemini-embedding-001"
        )
        
        # Create collections for different memory types
        self.conversation_memory = self.client.get_or_create_collection(
            name="conversation_memory",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        
        self.skill_memory = self.client.get_or_create_collection(
            name="skill_memory",
            embedding_function=self.embedding_fn
        )
        
        self.episodic_memory = self.client.get_or_create_collection(
            name="episodic_memory",
            embedding_function=self.embedding_fn
        )
        
    def add_conversation(self, user_input: str, assistant_response: str, metadata: Dict[str, Any] = None):
        """Store conversation in long-term memory"""
        content = f"User: {user_input}\nAssistant: {assistant_response}"
        doc_id = hashlib.md5(content.encode()).hexdigest()
        
        self.conversation_memory.upsert(
            documents=[content],
            metadatas=[metadata or {"timestamp": datetime.now().isoformat()}],
            ids=[doc_id]
        )
        return doc_id
    
    def retrieve_similar(self, query: str, k: int = 5, memory_type: str = "conversation"):
        """Retrieve similar past conversations"""
        collection = getattr(self, f"{memory_type}_memory")
        results = collection.query(query_texts=[query], n_results=k)
        
        if results['documents']:
            return results['documents'][0]
        return []
    
    def store_skill_result(self, skill_name: str, input_params: str, output: str):
        """Store successful skill executions"""
        content = f"Skill: {skill_name}\nInput: {input_params}\nOutput: {output}"
        doc_id = hashlib.md5(content.encode()).hexdigest()
        
        self.skill_memory.upsert(
            documents=[content],
            metadatas=[{"skill": skill_name, "timestamp": datetime.now().isoformat()}],
            ids=[doc_id]
        )
    
    def clear_short_term(self):
        """Clear current session context (not implemented for Chroma)"""
        pass

    
class PercistenceManager:
    def __init__(self, persist_directory=PERSIST_DIRECTORY):
        self.conn = sqlite3.connect(persist_directory / "agent.db", check_same_thread=False)
        # Initialize PostgresSaver with connection string
        self.checkpointer = PostgresSaver.from_conn_string(DB_URI)
        self.graph = None  # Will be set by the agent when initialized

    @staticmethod
    def create_db():
        with PostgresSaver.from_conn_string(DB_URI)  as saver:
            # Ensure tables are created
            saver.setup()

    def get_checkpointer(self):
        return self.checkpointer

    def register_graph(self, graph):
        """Set the graph reference for retrieving state snapshots"""
        self.graph = graph
    # ==================== HISTORY METHODS USING CHECKPOINTS ====================

    def get_conversation_history(self, thread_id: str) -> List[BaseMessage]:
        """
        Retrieve full conversation history for a thread from the checkpoint.
        
        Args:
            thread_id: The thread ID to retrieve history for
            
        Returns:
            List of messages in chronological order
        """
        try:
            config: RunnableConfig = {"configurable": {
                "thread_id": thread_id}, "metadata": {"thread_id": thread_id}}

            # Get the current state of the graph
            state_snapshot = self.graph.get_state(config)

            if state_snapshot and state_snapshot.values:
                messages = state_snapshot.values.get("messages", [])
                return messages
            return []

        except Exception as e:
            logger.error(
                f"Failed to retrieve history for thread {thread_id}: {e}")
            return []

    def get_conversation_history_raw(self, thread_id: str) -> List[Dict[str, Any]]:
        """
        Get conversation history with more details from the checkpoint database.
        
        Args:
            thread_id: The thread ID to retrieve history for
            
        Returns:
            List of message dictionaries with metadata
        """
        try:
            cursor = self.conn.cursor()

            # Query the checkpoint table for this thread
            # Note: The exact table structure may vary based on langgraph version
            cursor.execute("""
                SELECT thread_id, checkpoint_id, checkpoint 
                FROM checkpoints 
                WHERE thread_id = ?
                ORDER BY checkpoint_id
            """, (thread_id,))

            checkpoints = cursor.fetchall()

            history = []
            for checkpoint in checkpoints:
                # Parse checkpoint data (this is simplified - actual parsing depends on storage format)
                history.append({
                    "checkpoint_id": checkpoint[1],
                    # You'd need to extract actual timestamp
                    "timestamp": datetime.now().isoformat(),
                    # Truncated for display
                    "checkpoint_data": checkpoint[2][:100] if checkpoint[2] else None
                })

            return history

        except Exception as e:
            logger.error(f"Failed to retrieve raw history: {e}")
            return []

    def print_history(self, thread_id: str):
        """Print the conversation history for a specific thread."""
        messages = self.get_conversation_history(thread_id)

        print("\n" + "=" * 60)
        print(f"Conversation History - Thread: {thread_id}")
        print("=" * 60)

        if not messages:
            print("No conversation history found for this thread.")
            print("=" * 60)
            return

        for i, msg in enumerate(messages, 1):
            role = "User" if isinstance(msg, HumanMessage) else "Assistant" if isinstance(
                msg, AIMessage) else "System"
            content: str = str(
                msg.content) if msg.content else "[Tool call or other content]"

            # Truncate long messages for display
            if len(content) > 200:
                content = content[:200] + "..."

            print(f"{i}. {role}: {content}")

        print("=" * 60)
        print(f"Total messages: {len(messages)}")

    def clear_history(self, thread_id: Optional[str] = None):
        """
        Clear conversation history for a specific thread or all threads.
        
        WARNING: This directly modifies the checkpoint database.
        
        Args:
            thread_id: Specific thread to clear, or None to clear all
        """
        try:
            cursor = self.conn.cursor()

            if thread_id:
                # Clear specific thread
                cursor.execute(
                    "DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                cursor.execute(
                    "DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                print(f"Cleared history for thread: {thread_id}")
            else:
                # Clear all threads (use with caution!)
                confirm = input(
                    "WARNING: This will clear ALL conversation history. Type 'yes' to confirm: ")
                if confirm.lower() == 'yes':
                    cursor.execute("DELETE FROM checkpoints")
                    cursor.execute("DELETE FROM writes")
                    print("Cleared ALL conversation history")
                else:
                    print("Operation cancelled")

            self.conn.commit()

        except Exception as e:
            logger.error(f"Failed to clear history: {e}")
            print(f"Error clearing history: {e}")

    def get_summary(self, thread_id: str) -> Dict[str, Any]:
        """
        Get summary statistics for a conversation thread.
        
        Args:
            thread_id: The thread ID to analyze
            
        Returns:
            Dictionary with conversation statistics
        """
        messages = self.get_conversation_history(thread_id)

        # Count message types
        user_msgs = sum(1 for m in messages if isinstance(m, HumanMessage))
        ai_msgs = sum(1 for m in messages if isinstance(m, AIMessage))
        system_msgs = sum(1 for m in messages if isinstance(m, SystemMessage))
        tool_msgs = sum(1 for m in messages if hasattr(m, 'tool_call_id'))

        # Calculate average response length (for AI messages)
        ai_lengths = [len(m.content)
                      for m in messages if isinstance(m, AIMessage) and m.content]
        avg_ai_length = sum(ai_lengths) / len(ai_lengths) if ai_lengths else 0

        # Get last interaction time (simplified - would need actual timestamps from checkpoint)
        last_update = datetime.now().isoformat()  # Placeholder

        return {
            "thread_id": thread_id,
            "total_messages": len(messages),
            "user_messages": user_msgs,
            "assistant_messages": ai_msgs,
            "system_messages": system_msgs,
            "tool_interactions": tool_msgs,
            "avg_ai_response_length": round(avg_ai_length, 2),
            "last_updated": last_update,
            "has_history": len(messages) > 0
        }

    def list_threads(self) -> List[Dict[str, Any]]:
        """
        List all available conversation threads with basic info.
        
        Returns:
            List of thread information dictionaries
        """
        try:
            cursor = self.conn.cursor()

            # Get unique thread IDs from checkpoints
            cursor.execute("""
                SELECT DISTINCT thread_id, COUNT(*) as checkpoint_count
                FROM checkpoints 
                GROUP BY thread_id
                ORDER BY thread_id
            """)

            threads = cursor.fetchall()

            result = []
            for thread_id, checkpoint_count in threads:
                # Get a preview of the conversation
                messages = self.get_conversation_history(thread_id)
                first_msg = str(
                    messages[0].content) if messages and messages[0] else "No messages"
                last_msg = str(
                    messages[-1].content) if messages and messages[-1] else "No messages"

                result.append({
                    "thread_id": thread_id,
                    "checkpoint_count": checkpoint_count,
                    "message_count": len(messages),
                    "first_message": first_msg[:100] + "..." if len(first_msg) > 100 else first_msg,
                    "last_message": last_msg[:100] + "..." if len(last_msg) > 100 else last_msg
                })

            return result

        except Exception as e:
            logger.error(f"Failed to list threads: {e}")
            return []

    def export_history(self, thread_id: str, format: str = "json") -> str:
        """
        Export conversation history to a string format.
        
        Args:
            thread_id: The thread to export
            format: Export format ('json', 'text', or 'markdown')
            
        Returns:
            Formatted conversation history as string
        """
        messages = self.get_conversation_history(thread_id)

        if format == "json":
            import json
            export_data = []
            for msg in messages:
                export_data.append({
                    "type": msg.__class__.__name__,
                    "content": msg.content if msg.content else "",
                    "additional_kwargs": getattr(msg, "additional_kwargs", {})
                })
            return json.dumps(export_data, indent=2, default=str)

        elif format == "markdown":
            md_lines = [f"# Conversation History: {thread_id}\n"]
            for i, msg in enumerate(messages, 1):
                role = "**User**" if isinstance(msg, HumanMessage) else "**Assistant**" if isinstance(
                    msg, AIMessage) else "**System**"
                md_lines.append(f"### {i}. {role}\n")
                md_lines.append(
                    f"{msg.content if msg.content else '*Tool interaction*'}\n")
            return "\n".join(md_lines)

        else:  # text format
            text_lines = [
                f"Conversation History - Thread: {thread_id}", "=" * 50]
            for i, msg in enumerate(messages, 1):
                role = "User" if isinstance(msg, HumanMessage) else "Assistant" if isinstance(
                    msg, AIMessage) else "System"
                text_lines.append(f"\n{i}. {role}:")
                text_lines.append(
                    f"{msg.content if msg.content else '[Tool interaction]'}")
                text_lines.append("-" * 30)
            return "\n".join(text_lines)
        
     # ==================== CLEANUP ====================
    def close(self):
        """Close database connection."""
        if hasattr(self, 'conn'):
            self.conn.close()

