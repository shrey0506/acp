import logging
logger = logging.getLogger("ACP")

class InMemoryBus:
    def __init__(self): self.agents = {}
    def register(self, agent_id, handler_fn): self.agents[agent_id] = handler_fn
    def send(self, message):
        if message.receiver_id in self.agents: return self.agents[message.receiver_id](message)
        raise Exception(f"Agent {message.receiver_id} not registered.")
bus = InMemoryBus()
