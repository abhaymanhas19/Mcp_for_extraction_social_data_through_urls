class PublicError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
