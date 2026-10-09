"""A request the crew sent that cannot be done. The API turns it into CTFd's own error shape: {"success": false, "errors": {field: [...]}}."""


class Refused(Exception):
    """`problems` maps a field name to a list of messages; `status` is the HTTP status the API answers with (400 for a bad request,
    404 for something that does not exist, 409 for a request that is fine but meets the wrong state, such as a repeat)."""

    def __init__(self, problems, status=400):
        super().__init__(str(problems))
        self.problems = {field: ([messages] if isinstance(messages, str) else list(messages)) for field, messages in problems.items()}
        self.status = status
