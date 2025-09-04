class Session:
    def __init__(self, max_turns=10):
        self.history = []
        self.max_turns = max_turns

    def add_turn(self, user_msg: str):
        self.history.append(user_msg)
        if len(self.history) > self.max_turns:
            self.history.pop(0)

    def get_context(self):
        return self.history
