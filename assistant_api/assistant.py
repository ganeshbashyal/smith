from .providers import LLMProvider
from .retrieval import LocalRetriever
from .storage import SQLiteStore


class Assistant:
    def __init__(self, store: SQLiteStore, retriever: LocalRetriever, provider: LLMProvider):
        self.store, self.retriever, self.provider = store, retriever, provider

    async def chat(self, session_id: str, message: str) -> str:
        self.store.add_message(session_id, "user", message)
        context = self.retriever.search(message)
        context_text = "\n\n".join(f"Source: {item['source']}\n{item['content']}" for item in context)
        messages = [{"role": "system", "content": "You are a helpful personal assistant. Use supplied context when relevant."}]
        if context_text: messages.append({"role": "system", "content": f"Local context:\n{context_text}"})
        messages.extend(self.store.history(session_id))
        answer = await self.provider.complete(messages)
        self.store.add_message(session_id, "assistant", answer)
        return answer
